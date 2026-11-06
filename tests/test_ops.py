"""로그·운영 기본기(A-2): request_id, JSON 로그, 동기화 상태, /health, 운영 알림."""
import asyncio
import json
import logging

from fastapi.testclient import TestClient

from app import ops
from app.api import deps
from app.api.main import app
from app.config import Settings
from app.logging_setup import (JsonFormatter, _ContextFilter, current_request_id, new_request_id, set_request_id,
                               setup_logging)
from app.rag.store import VectorStore


def _settings(tmp_path, **kw):
    return Settings(data_dir=str(tmp_path), embedding_backend="local", admin_token="t", **kw)


# --- 로그 ---
def _format(record_kwargs: dict, extra: dict | None = None) -> dict:
    record = logging.LogRecord("policy-bot", logging.INFO, __file__, 1, record_kwargs["msg"], record_kwargs.get("args"),
                               None)
    for k, v in (extra or {}).items():
        setattr(record, k, v)
    _ContextFilter("bot").filter(record)
    return json.loads(JsonFormatter().format(record))


def test_json_log_has_request_id_and_extra_fields():
    set_request_id("s-abc123")
    out = _format({"msg": "저장 %d건", "args": (2,)}, {"record_id": "a1b2"})
    assert out["msg"] == "저장 2건"
    assert (out["service"], out["request_id"], out["record_id"]) == ("bot", "s-abc123", "a1b2")
    set_request_id(None)
    assert _format({"msg": "x"})["request_id"] == "-"


def test_request_id_is_per_asyncio_task():
    async def handler(prefix):
        rid = new_request_id(prefix)
        await asyncio.sleep(0)  # 다른 작업이 끼어들어도
        return rid == current_request_id()

    async def main():
        return await asyncio.gather(handler("m-"), handler("s-"))

    assert asyncio.run(main()) == [True, True]


def test_setup_logging_does_not_stack_handlers():
    root = logging.getLogger()
    before = len(root.handlers)
    setup_logging("test", fmt="json", level="INFO")
    setup_logging("test", fmt="text", level="INFO")
    ours = [h for h in root.handlers if getattr(h, "_policy_bot", False)]
    assert len(ours) == 1 and len(root.handlers) == before + 1
    root.removeHandler(ours[0])


def test_bot_sends_request_id_header():
    from policy_search import api_headers
    set_request_id("m-1234")
    assert api_headers({"X-Admin-Token": "t"})["X-Request-ID"] == "m-1234"
    set_request_id(None)
    assert "X-Request-ID" not in api_headers()


def test_api_echoes_or_creates_request_id(tmp_path):
    app.dependency_overrides[deps.get_settings] = lambda: _settings(tmp_path)
    app.dependency_overrides[deps.get_store] = lambda: VectorStore(None, "test_ops")
    try:
        c = TestClient(app)
        assert c.get("/health", headers={"X-Request-ID": "m-abcd"}).headers["X-Request-ID"] == "m-abcd"
        made = c.get("/health", headers={"X-Request-ID": "bad id\nINFO fake"}).headers["X-Request-ID"]
        assert made.startswith("a-")  # 이상한 값은 버리고 새로 (로그 위조 방지)
    finally:
        app.dependency_overrides.clear()


def test_usage_record_carries_request_id():
    from app import usage
    set_request_id("s-cost")
    assert usage.Usage("answer", "m").request_id == "s-cost"
    set_request_id(None)


# --- 동기화 상태·health ---
def test_sync_state_survives_and_alerts_once(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    sent = []
    monkeypatch.setattr(ops, "notify", lambda _s, msg: sent.append(msg) or True)
    for _ in range(4):
        ops.record_sync_result(s, ok=False, error="APIResponseError: 401")
    assert ops.read_sync_state(s)["consecutive_failures"] == 4
    assert len(sent) == 1  # 3번째에 한 번만
    ops.record_sync_result(s, ok=True, result={"updated_pages": 1})
    state = ops.read_sync_state(s)
    assert state["consecutive_failures"] == 0 and state["last_ok_at"]
    assert not list(tmp_path.glob("*.tmp"))  # 임시 파일 정리됨


def test_health_report(tmp_path):
    s = _settings(tmp_path)
    assert ops.health_report(s, 5)["status"] == "degraded"  # 동기화 기록 없음
    ops.record_sync_result(s, ok=True, result={})
    r = ops.health_report(s, 5)
    assert r["status"] == "ok" and r["problems"] == [] and r["last_sync_ok_at"]
    ops.record_sync_result(s, ok=False, error="boom")
    assert ops.health_report(s, 5)["status"] == "degraded"


def test_notify_without_webhook_only_logs(tmp_path):
    assert ops.notify(_settings(tmp_path), "hi") is False
    assert _settings(tmp_path, budget_alert_webhook_url="https://x").resolved_ops_webhook_url() == "https://x"
    assert _settings(tmp_path, ops_webhook_url="https://o", budget_alert_webhook_url="https://x"
                     ).resolved_ops_webhook_url() == "https://o"


def test_log_format_is_validated():
    assert any("LOG_FORMAT" in p for p in Settings(log_format="yaml").problems("api"))


def test_startup_message_names_host():
    from bot import startup_message
    assert "devbox-1" in startup_message("devbox-1")
