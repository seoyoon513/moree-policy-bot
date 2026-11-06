"""LLM(Claude) 호출은 여기 하나를 지나간다 (ARCHITECTURE_REVIEW A-4).

모든 호출이 같은 순서를 밟는다 — 호출하는 쪽이 빼먹을 수 없게:
  1) 월 예산 확인 (limits.check_budget → 넘었으면 LimitExceeded)
  2) 호출 + 지연 측정
  3) 사용량 기록 (usage.jsonl: 기능·모델·토큰·비용·사용자·request_id)
  4) 실패하면 종류별로 분류한 LLMError (사용자에게 보여 줄 문구 + 재시도 가능 여부)
     지출 한도·API 키 문제는 운영 웹훅으로도 알린다 (종류별 1시간에 한 번).

전에는 요약·정책서 매칭·리랭크·답변·평가 채점이 각자 클라이언트를 만들고 기록했고,
요약(/정책정리)은 예산 확인을 거치지 않았다.

재시도: anthropic SDK가 429·5xx·529·연결 오류를 MAX_RETRIES번까지 자동으로 다시 보낸다.
여기서 받는 오류는 그 재시도까지 실패한 것이다.
"""
from __future__ import annotations

import asyncio
import logging
import time

import anthropic
from anthropic import Anthropic, AsyncAnthropic

from app import limits, ops
from app import usage as usage_log
from app.config import Settings, get_settings

log = logging.getLogger("policy-llm")

MAX_RETRIES = 2

# kind → (사용자 문구, 다시 시도하면 나을 수 있나, 운영 알림 대상인가)
_KINDS: dict[str, tuple[str, bool, bool]] = {
    "spend_limit": ("Claude API 지출 한도에 도달해서 지금은 답할 수 없어요. 관리자에게 알려 주세요.", False, True),
    "auth": ("Claude API 키 설정에 문제가 있어요. 관리자에게 알려 주세요.", False, True),
    "rate_limit": ("요청이 몰려 잠시 막혔어요. 1분쯤 뒤에 다시 시도해 주세요.", True, False),
    "overloaded": ("Claude 서버가 혼잡해요. 잠시 뒤에 다시 시도해 주세요.", True, False),
    "timeout": ("Claude 응답이 너무 늦어요. 잠시 뒤에 다시 시도해 주세요.", True, False),
    "connection": ("Claude 서버에 연결하지 못했어요. 잠시 뒤에 다시 시도해 주세요.", True, False),
    "too_large": ("보낼 내용이 너무 길어요. 범위를 줄여서 다시 시도해 주세요.", False, False),
    "bad_request": ("Claude 요청 형식에 문제가 있어요. 관리자에게 알려 주세요.", False, False),
    "unknown": ("Claude 호출 중 알 수 없는 오류가 났어요. 잠시 뒤에 다시 시도해 주세요.", True, False),
}
# 400인데 메시지에 이런 말이 있으면 크레딧·지출 한도 (Console 워크스페이스 한도 포함, DIRECTION 5절)
_SPEND_WORDS = ("credit balance", "spend limit", "spending limit", "usage limit", "billing")


class LLMError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        self.kind = kind
        self.user_message, self.retryable, self.alert = _KINDS.get(kind, _KINDS["unknown"])
        self.detail = detail[:500]
        super().__init__(f"{self.user_message} ({kind})")


def classify(e: Exception) -> LLMError:
    """anthropic SDK 예외 → LLMError. isinstance 순서가 중요하다 (Timeout은 Connection의 하위 클래스)."""
    detail = f"{type(e).__name__}: {e}"
    if isinstance(e, anthropic.APITimeoutError):
        return LLMError("timeout", detail)
    if isinstance(e, anthropic.APIConnectionError):
        return LLMError("connection", detail)
    if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return LLMError("auth", detail)
    if isinstance(e, anthropic.RateLimitError):
        return LLMError("rate_limit", detail)
    if isinstance(e, (anthropic.OverloadedError, anthropic.InternalServerError, anthropic.ServiceUnavailableError)):
        return LLMError("overloaded", detail)
    if isinstance(e, anthropic.RequestTooLargeError):
        return LLMError("too_large", detail)
    if isinstance(e, anthropic.BadRequestError):
        msg = str(e).lower()
        return LLMError("spend_limit" if any(w in msg for w in _SPEND_WORDS) else "bad_request", detail)
    return LLMError("unknown", detail)


def text_of(msg) -> str:
    """응답의 텍스트 블록만 이어 붙인다 (도구 호출 블록은 제외)."""
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


_last_alert: dict[str, float] = {}
ALERT_INTERVAL_S = 3600


def _should_alert(kind: str, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    if now - _last_alert.get(kind, -ALERT_INTERVAL_S - 1) < ALERT_INTERVAL_S:
        return False
    _last_alert[kind] = now
    return True


class LLMClient:
    """sync(API 서버의 리랭크·답변)와 async(봇의 요약·정책서 매칭)를 같은 규칙으로.
    client / async_client: 테스트에서 가짜를 넣는다 (messages.create만 있으면 된다)."""

    def __init__(self, api_key: str | None = None, *, client=None, async_client=None, settings: Settings | None = None):
        self._settings = settings
        self._api_key = api_key
        self._client = client
        self._async_client = async_client

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    def _key(self) -> str:
        return self._api_key or self.settings.anthropic_api_key

    @property
    def client(self) -> Anthropic:
        if self._client is None:
            self._client = Anthropic(api_key=self._key(), max_retries=MAX_RETRIES)
        return self._client

    @property
    def async_client(self) -> AsyncAnthropic:
        if self._async_client is None:
            self._async_client = AsyncAnthropic(api_key=self._key(), max_retries=MAX_RETRIES)
        return self._async_client

    def _failed(self, feature: str, model: str, e: Exception) -> LLMError:
        err = classify(e)
        log.error("LLM 호출 실패 (%s, %s): %s", feature, err.kind, err.detail,
                  extra={"feature": feature, "model": model, "llm_error": err.kind})
        if err.alert and _should_alert(err.kind):
            ops.notify(self.settings, f"⚠️ Claude 호출 실패 — {err.user_message} ({feature}, {err.kind})")
        return err

    def create(self, feature: str, *, model: str, user: str | None = None, **kwargs):
        """messages.create와 같은 인자. 돌려주는 값도 같은 Message. 사용량은 msg.usage_record에 붙인다."""
        limits.check_budget(self.settings)
        started = time.perf_counter()
        try:
            msg = self.client.messages.create(model=model, **kwargs)
        except anthropic.AnthropicError as e:
            raise self._failed(feature, model, e) from e
        msg.usage_record = usage_log.from_anthropic(feature, model, msg, int((time.perf_counter() - started) * 1000),
                                                    user=user)
        return msg

    async def acreate(self, feature: str, *, model: str, user: str | None = None, **kwargs):
        limits.check_budget(self.settings)
        started = time.perf_counter()
        try:
            msg = await self.async_client.messages.create(model=model, **kwargs)
        except anthropic.AnthropicError as e:
            err = classify(e)
            # 알림은 블로킹 HTTP라 이벤트 루프를 막지 않게 스레드로
            await asyncio.to_thread(self._failed, feature, model, e)
            raise err from e
        msg.usage_record = usage_log.from_anthropic(feature, model, msg, int((time.perf_counter() - started) * 1000),
                                                    user=user)
        return msg


_default: LLMClient | None = None


def get_llm() -> LLMClient:
    """설정의 API 키를 쓰는 공용 클라이언트 (봇 프로세스용)."""
    global _default
    if _default is None:
        _default = LLMClient()
    return _default
