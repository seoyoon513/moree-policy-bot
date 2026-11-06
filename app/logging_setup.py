"""로그 설정 한 곳 (ARCHITECTURE_REVIEW A-2). 봇·API·스크립트가 시작할 때 setup_logging()을 한 번 부른다.

- LOG_FORMAT=text(기본, 사람이 읽기) | json(서버, 한 줄 = JSON 하나라 grep·수집기로 다루기 쉬움)
- LOG_LEVEL=INFO 등
- request_id: 요청 하나(멘션 질문, /정책정리 → 승인, API 호출)를 끝까지 따라가는 ID.
  contextvars라 asyncio 작업마다 따로 들고 다니고, 그 작업에서 만든 하위 작업도 물려받는다.
  봇은 X-Request-ID 헤더로 API에 넘긴다 → 봇 로그와 API 로그를 같은 ID로 grep.
- 로그에 붙일 추가 필드: log.info("저장", extra={"record_id": ..., "thread_id": ...})
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

# 표준 LogRecord 속성. 이 외의 속성은 extra={...}로 넘긴 값이라 JSON에 그대로 싣는다
_STD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "request_id", "service"}


def new_request_id(prefix: str = "") -> str:
    """새 request_id를 만들어 현재 작업(context)에 설정하고 돌려준다."""
    rid = f"{prefix}{uuid.uuid4().hex[:8]}"
    request_id_var.set(rid)
    return rid


def set_request_id(rid: str | None) -> None:
    request_id_var.set(rid)


def current_request_id() -> str | None:
    return request_id_var.get()


class _ContextFilter(logging.Filter):
    """모든 로그 레코드에 service·request_id를 붙인다 (포매터가 쓸 수 있게)."""

    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self.service
        record.request_id = request_id_var.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": getattr(record, "service", None),
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        out |= {k: v for k, v in vars(record).items() if k not in _STD_ATTRS and not k.startswith("_")}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"


def setup_logging(service: str, *, fmt: str | None = None, level: str | None = None) -> None:
    """루트 로거에 핸들러 하나를 단다. 여러 번 불러도 핸들러가 쌓이지 않는다.
    fmt·level을 안 주면 Settings(LOG_FORMAT·LOG_LEVEL)를 쓴다."""
    if fmt is None or level is None:
        from app.config import get_settings
        s = get_settings()
        fmt = fmt or s.log_format
        level = level or s.log_level
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_ContextFilter(service))
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT))
    handler._policy_bot = True  # type: ignore[attr-defined]  # 우리가 단 핸들러 표시 (다시 부르면 교체)
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, "_policy_bot", False)]:
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level.upper())
    # 외부 라이브러리의 요청 단위 로그는 줄인다 (notion-client·httpx는 INFO에 호출마다 한 줄씩 남김)
    for noisy in ("httpx", "httpcore", "notion_client", "chromadb"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
