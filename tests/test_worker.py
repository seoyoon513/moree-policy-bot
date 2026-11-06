"""백그라운드 처리기·대량 삭제 안전장치(A-5c). 네트워크 없음."""
import asyncio
import time

from app.config import Settings
from app.outbox import NOTION_UPSERT, Outbox
from app.rag.ingest import ingest_documents
from app.rag.models import PolicyDocument
from app.rag.store import VectorStore
from app.services import storage
from app.worker import Worker
from tests.test_rag_api import FakeEmbedder


class Repo:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    async def add(self, record):
        self.calls += 1
        if self.fail:
            raise RuntimeError("503")
        return f"https://notion.so/{record['id']}"

    async def update(self, record, previous):
        return await self.add(record)

    async def find_url(self, record_id):
        return None


def _decision():
    return {"area": "리뷰", "topic": "t", "status": "논의중", "decision": "d", "rationale": None, "decided_by": None,
            "scope": None, "external_refs": [], "open_questions": [], "source_messages": [], "relation": "new"}


def _settings(tmp_path, **kw):
    return Settings(data_dir=str(tmp_path), notion_token="t", notion_data_source_id="ds", **kw)


def test_worker_retries_due_followups_and_requests_reindex(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    [r] = asyncio.run(storage.save_decisions([_decision()], source={"thread_id": "t1", "messages_by_index": {}},
                                             approved_by="a", repo=Repo(fail=True)))
    repo, syncs = Repo(), []
    monkeypatch.setattr("app.worker.get_decision_repository", lambda s: repo)
    w = Worker(_settings(tmp_path), lambda s: syncs.append(1))

    async def go():
        first = await w.tick()                     # 아직 다음 시도 시각 전 → 실행 안 함
        outbox = Outbox(tmp_path)
        key = f"{r.record['id']}:1:{NOTION_UPSERT}"
        outbox.record(key, "failed", attempt=1, next_at="2000-01-01T00:00:00+00:00")  # 때가 됨
        second = await w.tick()
        await asyncio.sleep(0.05)                  # 백그라운드 재인덱싱 스레드
        return first, second

    first, second = asyncio.run(go())
    assert first["notion_done"] == first["notion_failed"] == 0 and repo.calls == 1  # 첫 바퀴는 시도 안 함
    assert second["notion_done"] == 1 and second["synced"] and syncs == [1]
    assert Outbox(tmp_path).pending() == []


def test_periodic_sync_after_interval(tmp_path):
    syncs = []
    w = Worker(_settings(tmp_path, periodic_sync_minutes=10), lambda s: syncs.append(1))

    async def go():
        a = await w.tick(now=time.monotonic() + 60)          # 1분 → 아직
        b = await w.tick(now=time.monotonic() + 11 * 60)     # 11분 → 동기화
        await asyncio.sleep(0.05)
        return a, b

    a, b = asyncio.run(go())
    assert not a["synced"] and b["synced"] and syncs == [1]


def test_periodic_sync_can_be_turned_off(tmp_path):
    w = Worker(_settings(tmp_path, periodic_sync_minutes=0), lambda s: (_ for _ in ()).throw(AssertionError))
    assert not asyncio.run(w.tick(now=time.monotonic() + 10 ** 6))["synced"]


# --- 대량 삭제 안전장치 (F6) ---
def _docs(n, prefix="p"):
    return [PolicyDocument(f"{prefix}{i}", f"제목{i}", "", f"본문 {i}", {"area": "리뷰"}) for i in range(n)]


def test_mass_delete_is_blocked_then_allowed_explicitly():
    store, emb, s = VectorStore(None, "test_guard"), FakeEmbedder(), Settings()
    ingest_documents(_docs(20), emb, store, s)
    pages = lambda: {m["page_id"] for m in store.col.get(include=["metadatas"])["metadatas"]}
    # 노션 권한이 바뀌어 3개만 읽힘 → 17개를 지우지 않는다
    r = ingest_documents(_docs(3), emb, store, s)
    assert r.delete_blocked and r.removed_chunks == 0 and len(pages()) == 20
    # 정상적인 소량 삭제(20 → 15)는 그대로
    r = ingest_documents(_docs(15), emb, store, s)
    assert not r.delete_blocked and len(pages()) == 15
    # 정말 지울 때는 명시적으로
    r = ingest_documents(_docs(2), emb, store, s, allow_mass_delete=True)
    assert len(pages()) == 2
