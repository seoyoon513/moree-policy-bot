"""후속 작업 장부(A-5): 원장 줄의 ops + outbox.jsonl 결과 → 실패가 남고, 다시 실행하면 이어진다. 네트워크 없음."""
import asyncio
from datetime import timedelta

import pytest

from app import ops as ops_mod
from shared.config import Settings
from app.outbox import MAX_ATTEMPTS, NOTION_UPSERT, SPEC_REFLECT, Outbox, op_key
from app.services import followups, spec_reflect, storage


class Repo:
    def __init__(self, fail_times: int = 0):
        self.fail_times, self.calls = fail_times, []

    async def _go(self, kind, record):
        self.calls.append((kind, record["id"], record.get("version")))
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("APIResponseError: 503 service_unavailable")
        return f"https://notion.so/{record['id']}"

    async def add(self, record):
        return await self._go("add", record)

    async def update(self, record, previous):
        return await self._go("update", record)

    async def find_url(self, record_id):
        return None


def _decision(**kw):
    d = {"area": "리뷰", "topic": "리뷰 사진", "status": "최종결정", "decision": "최대 5장", "rationale": None,
         "decided_by": None, "scope": None, "external_refs": [], "open_questions": [], "source_messages": [],
         "relation": "new"}
    return d | kw


SRC = {"thread_id": "t1", "thread_name": "t", "thread_url": "u", "messages_by_index": {}}


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(spec_reflect, "NOTION_SPEC_PAGES", {"리뷰": "0" * 32})
    monkeypatch.setattr(spec_reflect, "NOTION_SPEC_INBOX_PAGE", "")
    return tmp_path


def _save(repo, **kw):
    return asyncio.run(storage.save_decisions([_decision(**kw)], source=SRC, approved_by="승인자", repo=repo,
                                              plan_reflect=True))


def test_ops_are_written_into_the_ledger_line(data):
    [r] = _save(Repo())
    assert r.record["ops"] == [NOTION_UPSERT, SPEC_REFLECT]
    assert storage.current_records("t1")[0]["ops"] == [NOTION_UPSERT, SPEC_REFLECT]
    [r2] = _save(Repo(), status="논의중")
    assert r2.record["ops"] == [NOTION_UPSERT]  # 반영 대상 상태가 아니면 정책서 작업은 없다
    [r3] = asyncio.run(storage.save_decisions([_decision()], source=SRC, approved_by="a", repo=None))
    assert "ops" not in r3.record  # 노션 미설정이면 할 일 없음


def test_notion_failure_is_recorded_and_retried(data, monkeypatch):
    repo = Repo(fail_times=1)
    [r] = _save(repo)
    assert r.notion_pending and r.notion_url is None and "503" in r.notion_error
    outbox = Outbox(data)
    [p_notion, p_reflect] = outbox.pending()
    assert (p_notion.op, p_notion.attempt, p_reflect.op) == (NOTION_UPSERT, 1, SPEC_REFLECT)
    assert not p_notion.due()  # 다음 시도 시각(백오프) 전

    async def fake_reflect(saved, user=None):
        return [spec_reflect.Outcome(rec["id"], rec["topic"], "same") for rec, _ in saved]

    monkeypatch.setattr(spec_reflect, "reflect_records", fake_reflect)
    out = asyncio.run(followups.run_pending(outbox, repo, only_due=False))
    assert out["notion_done"] == 1 and out["remaining"] == 0
    assert outbox.notion_url(r.record) == f"https://notion.so/{r.record['id']}"
    assert [c[0] for c in repo.calls] == ["add", "add"]  # 첫 시도 실패 + 재시도


def test_only_current_version_is_pending(data):
    [v1] = _save(Repo(fail_times=1), status="논의중")   # v1 노션 실패
    asyncio.run(storage.save_decisions([_decision(status="논의중", relation="update", prior_id=v1.record["id"],
                                                  decision="최대 10장")], source=SRC, approved_by="a", repo=Repo()))
    # v2가 저장됐으니 v1의 노션 작업은 대기 목록에 없다 (v2 저장이 그 내용을 덮는다)
    assert Outbox(data).pending() == []


def test_gives_up_after_max_attempts_or_non_retryable(data):
    outbox = Outbox(data)
    for _ in range(MAX_ATTEMPTS):
        last = outbox.fail("aaa:1:notion_upsert", "503")
    assert last["result"] == "dead"
    assert outbox.fail("bbb:1:notion_upsert", "400 validation_error", retryable=False)["result"] == "dead"


def test_dead_and_old_pending_show_in_health(data):
    [r] = _save(Repo(fail_times=1), status="논의중")
    outbox = Outbox(data)
    outbox.fail(op_key(r.record, NOTION_UPSERT), "401 unauthorized", retryable=False)
    s = Settings(data_dir=str(data), embedding_backend="local", anthropic_api_key="k")
    ops_mod.record_sync_result(s, ok=True, result={})
    report = ops_mod.health_report(s, 1)
    assert report["status"] == "degraded" and report["followups"]["dead"] == 1
    assert any("포기한 후속 작업" in p for p in report["problems"])


def test_backoff_grows_and_caps():
    from app.outbox import BACKOFF_MAX_S, backoff
    assert backoff(1) == timedelta(minutes=1) and backoff(3) == timedelta(minutes=4)
    assert backoff(20) == timedelta(seconds=BACKOFF_MAX_S)


def test_broken_outbox_line_means_not_done_yet(data):
    outbox = Outbox(data)
    outbox.done("x:1:notion_upsert", notion_url="u")
    with outbox.path.open("a", encoding="utf-8") as f:
        f.write('{"op": "y:1:notion_up')
    assert set(outbox.latest()) == {"x:1:notion_upsert"}


def test_decision_cannot_overwrite_reserved_fields(data):
    """A-6부터 결정은 HTTP로 들어온다 → id·version·ops 같은 저장 단계 값을 덮어쓰지 못한다."""
    [r] = _save(Repo(), id="hijack", version=99, ops=["evil"], approved_by="someone")
    assert r.record["id"] != "hijack" and r.record["version"] == 1
    assert r.record["ops"] == [NOTION_UPSERT, SPEC_REFLECT] and r.record["approved_by"] == "승인자"


def test_reflect_after_save_explains_skips(data, monkeypatch):
    async def fake_reflect(saved, user=None):
        return [spec_reflect.Outcome(rec["id"], rec["topic"], "modify", "XX-01-01") for rec, _ in saved]

    monkeypatch.setattr(spec_reflect, "reflect_records", fake_reflect)
    ok = _save(Repo())
    pending = _save(Repo(fail_times=1))
    lines = asyncio.run(followups.reflect_after_save(ok + pending))
    assert lines[0] == "📚 기준 정책서 반영 제안"
    assert any("XX-01-01" in l for l in lines) and any("노션 저장이 끝나면 다시 시도" in l for l in lines)
    assert Outbox(data).latest()[op_key(ok[0].record, SPEC_REFLECT)]["result"] == "done"
