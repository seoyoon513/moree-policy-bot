"""노션 접근 일원화(A-3): 클라이언트 한 곳, 스키마 한 곳(쓰기·읽기 공용), 저장소 주입, 원장 한 곳. 네트워크 없음."""
import asyncio
import json

import pytest

from app.services import storage
from shared.config import Settings
from app.ledger import Ledger
from app.notion import client as notion
from app.notion import schema as ns
from app.notion.decisions import get_decision_repository
from app.rag.notion_loader import JsonlLoader, page_to_document


def _record(**kw) -> dict:
    r = {"id": "rec00001", "version": 1, "area": "북마크", "topic": "북마크 최대 개수", "status": "최종결정",
         "decision": "최대 200개", "rationale": "서버 부하", "decided_by": "가짜PM", "scope": "앱 전체",
         "external_refs": ["https://example.com/a"], "open_questions": [{"question": "웹도?", "ask_to": "가짜PM"}],
         "source": {"thread_id": "t1", "thread_url": "https://discord.com/channels/1/2", "thread_name": "가짜 스레드"},
         "decided_at": "2026-06-01T10:00:00+09:00", "approved_by": "승인자", "approved_at": "2026-06-02T11:00:00+09:00"}
    r.update(kw)
    return r


# --- 클라이언트 ---
def test_client_has_explicit_retry_and_our_logger():
    c = notion.new_sync_client("secret_fake")
    assert c._max_retries == notion.RETRY.max_retries == 3
    assert c.logger.name == "notion_client"  # 라이브러리 기본(stdout 별도 출력) 대신 우리 로깅으로


def test_notion_id_from_url_and_uuid():
    pid = "a" * 32
    assert notion.notion_id(f"https://www.notion.so/team/Moree-{pid}?v=1") == pid
    assert notion.notion_id("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa") == pid
    with pytest.raises(ValueError):
        notion.notion_id("not-an-id")


def test_repository_only_when_configured():
    assert get_decision_repository(Settings()) is None
    assert get_decision_repository(Settings(notion_token="t", notion_data_source_id="ds")).data_source_id == "ds"


# --- 스키마: 봇이 쓴 속성을 검색 로더가 그대로 읽는다 ---
def _as_page(props: dict) -> dict:
    """build_properties의 요청 모양 → 노션이 돌려주는 페이지 모양 (type 필드 + plain_text)."""
    out = {}
    for name, value in props.items():
        t = next(iter(value))
        v = value[t]
        if t in ("title", "rich_text"):
            v = [{"plain_text": x["text"]["content"]} for x in v]
        out[name] = {"type": t, t: v}
    return {"id": "page-1", "url": "https://notion.so/page-1", "properties": out}


def test_written_properties_round_trip_through_loader():
    doc = page_to_document(_as_page(ns.build_properties(_record(reopens="old00001"))))
    assert doc.title == "북마크 최대 개수"
    assert doc.metadata | {"last_edited": ""} == {
        "area": "북마크", "status": "최종결정", "decider": "가짜PM", "decided_at": "2026-06-01",
        "scope": "앱 전체", "thread_url": "https://discord.com/channels/1/2", "last_edited": "",
        "record_id": "rec00001", "reopens": "old00001"}
    assert "결정: 최대 200개" in doc.body and "확인필요: • 웹도? → 가짜PM" in doc.body


def test_schema_declares_every_written_property():
    assert set(ns.build_properties(_record(reopens="x"))) <= set(ns.PROPERTIES)


# --- 원장 ---
def test_ledger_skips_broken_lines_and_counts_them(tmp_path):
    led = Ledger(tmp_path)
    led.append(_record())
    led.append(_record(version=2, decision="최대 300개"))
    with led.path.open("a", encoding="utf-8") as f:
        f.write('{"id": "half-writ')  # 쓰다가 죽은 줄
    assert [r["decision"] for r in led.current()] == ["최대 300개"]
    assert led.broken == 1 and led.thread_ids() == {"t1"}


def test_jsonl_loader_survives_broken_line(tmp_path):
    led = Ledger(tmp_path)
    led.append(_record())
    with led.path.open("a", encoding="utf-8") as f:
        f.write("not json\n")
    docs = JsonlLoader(Settings(data_dir=str(tmp_path))).load()  # 전에는 json.loads 예외로 인덱싱 전체 실패
    assert [d.title for d in docs] == ["북마크 최대 개수"]


# --- 저장: 저장소를 주입 ---
class FakeRepo:
    def __init__(self, fail: bool = False):
        self.fail, self.calls = fail, []

    async def add(self, record):
        self.calls.append(("add", record["id"]))
        if self.fail:
            raise RuntimeError("APIResponseError: 503")
        return f"https://notion.so/{record['id']}"

    async def update(self, record, previous):
        self.calls.append(("update", record["id"], len(previous)))
        return f"https://notion.so/{record['id']}"

    async def find_url(self, record_id):
        return None


def _decision(**kw) -> dict:
    d = {"area": "북마크", "topic": "주제", "status": "최종결정", "decision": "A", "rationale": None, "decided_by": None,
         "scope": None, "external_refs": [], "open_questions": [], "source_messages": [], "relation": "new"}
    d.update(kw)
    return d


def _source():
    return {"thread_id": "t1", "thread_name": "t", "thread_url": "u", "messages_by_index": {}}


def test_save_uses_injected_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    repo = FakeRepo()
    [first] = asyncio.run(storage.save_decisions([_decision()], source=_source(), approved_by="승인자", repo=repo))
    [second] = asyncio.run(storage.save_decisions([_decision(relation="update", prior_id=first.record["id"], decision="B")],
                                                  source=_source(), approved_by="승인자", repo=repo))
    assert repo.calls == [("add", first.record["id"]), ("update", first.record["id"], 1)]
    assert second.record["version"] == 2 and second.notion_url.endswith(first.record["id"])


def test_notion_failure_keeps_ledger_line(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    [res] = asyncio.run(storage.save_decisions([_decision()], source=_source(), approved_by="승인자",
                                               repo=FakeRepo(fail=True)))
    assert res.notion_error and res.notion_url is None
    lines = (tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["id"] == res.record["id"]  # 원장이 먼저: 노션이 실패해도 승인은 남는다


def test_save_without_repository_is_local_only(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    [res] = asyncio.run(storage.save_decisions([_decision()], source=_source(), approved_by="승인자", repo=None))
    assert res.notion_url is None and res.notion_error is None
    assert storage.current_records("t1")[0]["id"] == res.record["id"]
