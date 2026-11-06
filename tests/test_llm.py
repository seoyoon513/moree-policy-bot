"""LLM 호출 1곳(A-4): 예산 확인 → 호출 → 사용량 기록 → 오류 분류. 네트워크 없음 (가짜 클라이언트)."""
import asyncio
import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app import limits, llm, usage
from app.logging_setup import set_request_id

REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _status(cls, code: int, message: str):
    return cls(message, response=httpx.Response(code, request=REQ), body=None)


def _msg(text="ok", inp=100, out=20):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=inp, output_tokens=out))


class FakeMessages:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result or _msg(), exc, []

    def create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return self.result


class AsyncFakeMessages(FakeMessages):
    async def create(self, **kw):
        return FakeMessages.create(self, **kw)


@pytest.fixture
def usage_file(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_path / "usage.jsonl"))
    monkeypatch.delenv("USAGE_LOG_DISABLED", raising=False)
    return tmp_path / "usage.jsonl"


# --- 분류 ---
@pytest.mark.parametrize("exc, kind, retryable", [
    (anthropic.APITimeoutError(request=REQ), "timeout", True),          # Connection의 하위 클래스지만 timeout으로
    (anthropic.APIConnectionError(request=REQ), "connection", True),
    (_status(anthropic.RateLimitError, 429, "rate_limit_error"), "rate_limit", True),
    (_status(anthropic.OverloadedError, 529, "overloaded_error"), "overloaded", True),
    (_status(anthropic.InternalServerError, 500, "api_error"), "overloaded", True),
    (_status(anthropic.AuthenticationError, 401, "invalid x-api-key"), "auth", False),
    (_status(anthropic.BadRequestError, 400, "Your credit balance is too low"), "spend_limit", False),
    (_status(anthropic.BadRequestError, 400, "workspace spend limit reached"), "spend_limit", False),
    (_status(anthropic.BadRequestError, 400, "messages: field required"), "bad_request", False),
])
def test_classify(exc, kind, retryable):
    err = llm.classify(exc)
    assert (err.kind, err.retryable) == (kind, retryable)
    assert err.user_message and "Error" not in err.user_message  # 사용자 문구에 내부 예외 이름이 섞이지 않게


# --- 호출 순서 ---
def test_sync_call_records_usage_with_feature_user_and_request_id(usage_file):
    set_request_id("m-abc")
    fake = FakeMessages()
    msg = llm.LLMClient("k", client=SimpleNamespace(messages=fake)).create(
        "answer", model="claude-test", user="u1", max_tokens=10, messages=[])
    set_request_id(None)
    assert llm.text_of(msg) == "ok" and fake.calls[0]["model"] == "claude-test"
    [row] = [json.loads(l) for l in usage_file.read_text().splitlines()]
    assert (row["feature"], row["user"], row["request_id"], row["input_tokens"]) == ("answer", "u1", "m-abc", 100)
    assert msg.usage_record.cost_usd == row["cost_usd"]


def test_budget_is_checked_before_calling(monkeypatch):
    fake = FakeMessages()

    def over_budget(s, spend=None):
        raise limits.LimitExceeded("budget", "예산 초과")

    monkeypatch.setattr(limits, "check_budget", over_budget)
    with pytest.raises(limits.LimitExceeded):
        llm.LLMClient("k", client=SimpleNamespace(messages=fake)).create("rerank", model="m", max_tokens=1, messages=[])
    assert fake.calls == []  # 돈이 나가기 전에 막는다


def test_failure_raises_llm_error_and_records_nothing(usage_file):
    fake = FakeMessages(exc=anthropic.APIConnectionError(request=REQ))
    with pytest.raises(llm.LLMError) as e:
        llm.LLMClient("k", client=SimpleNamespace(messages=fake)).create("answer", model="m", max_tokens=1, messages=[])
    assert e.value.kind == "connection" and isinstance(e.value.__cause__, anthropic.APIConnectionError)
    assert not usage_file.exists()  # 실패한 호출은 토큰이 청구되지 않으니 기록도 없다


def test_async_call_same_rules(usage_file):
    fake = AsyncFakeMessages()
    client = llm.LLMClient("k", async_client=SimpleNamespace(messages=fake))
    msg = asyncio.run(client.acreate("summarize", model="m", max_tokens=1, messages=[]))
    assert llm.text_of(msg) == "ok"
    assert json.loads(usage_file.read_text())["feature"] == "summarize"


def test_alert_only_for_config_problems_and_throttled(monkeypatch):
    sent = []
    monkeypatch.setattr(llm.ops, "notify", lambda s, m: sent.append(m))
    monkeypatch.setattr(llm, "_last_alert", {})
    bad_key = FakeMessages(exc=_status(anthropic.AuthenticationError, 401, "invalid x-api-key"))
    busy = FakeMessages(exc=_status(anthropic.OverloadedError, 529, "overloaded"))
    for fake in (bad_key, bad_key, busy):
        with pytest.raises(llm.LLMError):
            llm.LLMClient("k", client=SimpleNamespace(messages=fake)).create("answer", model="m", max_tokens=1,
                                                                             messages=[])
    assert len(sent) == 1 and "API 키" in sent[0]  # 키 문제는 1시간에 한 번, 혼잡은 알리지 않음


# --- 호출하는 쪽 ---
def test_summarizer_now_goes_through_budget_and_reports_friendly_error(monkeypatch):
    from app.services import summarizer
    from app.services.summarizer import SummaryError, ThreadMessage

    fake = AsyncFakeMessages(exc=_status(anthropic.BadRequestError, 400, "Your credit balance is too low"))
    monkeypatch.setattr(summarizer, "get_llm", lambda: llm.LLMClient("k", async_client=SimpleNamespace(messages=fake)))
    msgs = [ThreadMessage(1, "가짜", __import__("datetime").datetime(2026, 6, 1), "안녕", "https://discord.com/x")]
    with pytest.raises(SummaryError, match="지출 한도"):
        asyncio.run(summarizer.summarize_thread("스레드", msgs))

    def over_budget(s, spend=None):
        raise limits.LimitExceeded("budget", "이번 달 예산을 모두 사용")

    monkeypatch.setattr(limits, "check_budget", over_budget)
    with pytest.raises(SummaryError, match="예산"):
        asyncio.run(summarizer.summarize_thread("스레드", msgs))


def test_no_direct_sdk_calls_outside_llm_module():
    """messages.create를 직접 부르는 코드가 다시 생기지 않게 (테스트·app/llm.py 제외)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    offenders = [str(p.relative_to(root)) for p in root.rglob("*.py")
                 if ".venv" not in p.parts and "tests" not in p.parts and p.name != "llm.py"
                 and "messages.create(" in p.read_text(encoding="utf-8")]
    assert offenders == []
