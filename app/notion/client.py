"""노션 클라이언트를 만드는 곳은 여기 하나다.

재시도는 notion-client(3.1+)에 내장된 정책을 그대로 쓴다:
- 429(rate limit)는 모든 요청을 재시도한다 — 서버가 "처리 안 했으니 나중에"라고 한 것이라 안전하다.
- 500·503은 GET·DELETE만 재시도한다 — 페이지 생성(POST)·블록 추가(PATCH)를 다시 보내면 중복이 생길 수 있다.
  쓰기의 재시도는 "기록ID로 먼저 조회" 같은 멱등 처리와 함께 아웃박스에서 한다 (A-5b).
여기서는 횟수·대기·타임아웃을 명시하고, 라이브러리 로그를 우리 로깅(app/logging_setup)으로 보낸다
(기본값은 라이브러리가 stdout에 따로 찍는다).
"""
from __future__ import annotations

import logging
import re

from notion_client import AsyncClient, Client
from notion_client.client import RetryOptions

from app.config import Settings, get_settings

RETRY = RetryOptions(max_retries=3, initial_retry_delay_ms=1_000, max_retry_delay_ms=30_000)
TIMEOUT_MS = 30_000


def _options(token: str) -> dict:
    return {"auth": token, "timeout_ms": TIMEOUT_MS, "retry": RETRY,
            "logger": logging.getLogger("notion_client"), "log_level": logging.WARNING}


def enabled(s: Settings | None = None) -> bool:
    """결정 기록 DB를 쓸 수 있나 (토큰 + data source ID)."""
    s = s or get_settings()
    return bool(s.notion_token and s.notion_data_source_id)


def new_async_client(token: str | None = None) -> AsyncClient:
    """새 비동기 클라이언트. asyncio.run()을 매번 새로 도는 곳(API의 정책서 인덱싱)은 이걸 쓴다:
    httpx 연결은 만든 이벤트 루프에 묶여서, 다른 루프에서 재사용하면 깨진다."""
    return AsyncClient(_options(token or get_settings().notion_token))


def new_sync_client(token: str | None = None) -> Client:
    return Client(_options(token or get_settings().notion_token))


_async_client: AsyncClient | None = None


def get_async_client() -> AsyncClient:
    """봇처럼 이벤트 루프 하나에서 계속 도는 프로세스용 공용 클라이언트."""
    global _async_client
    if _async_client is None:
        _async_client = new_async_client()
    return _async_client


def notion_id(value: str) -> str:
    """페이지 URL이나 ID 문자열에서 32자리 ID를 뽑는다."""
    path = value.split("?")[0].split("#")[0]
    uuid_form = re.findall(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", path)
    if uuid_form:
        return uuid_form[-1].replace("-", "")
    # 'Moree-1a2b...' 처럼 제목 뒤에 붙은 32자리: 앞이 hex가 아닌 위치에서만 매칭
    found = re.findall(r"(?<![0-9a-fA-F])([0-9a-fA-F]{32})(?![0-9a-fA-F])", path)
    if not found:
        raise ValueError(f"Notion ID를 찾을 수 없어요: {value}")
    return found[-1]
