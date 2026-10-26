"""노션 ↔ Chroma 동기화 정합성: 삭제 가드, 무중단 교체, 정책서 캐시, 작업 이력."""
import asyncio
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.api.routers import sync
from app.api.schemas import SyncJob
from app.config import Settings
from app.rag.ingest import ingest_documents
from app.rag.models import PolicyDocument
from app.rag.spec_loader import _cache_settled, load_spec
from app.rag.store import VectorStore
from tests.test_rag_api import DOCS, FakeEmbedder


def _store():
    return VectorStore(None, f"t_{uuid.uuid4().hex[:8]}")


def _many(n):
    return [PolicyDocument(f"d{i}", f"정책 {i}", "", f"내용 {i}번 정책", {"area": "a"}) for i in range(n)]


def test_empty_source_does_not_wipe_index():
    store, emb, s = _store(), FakeEmbedder(), Settings(_env_file=None)
    ingest_documents(DOCS, emb, store, s)
    before = store.count()
    r = ingest_documents([], emb, store, s)   # 노션 장애로 빈 목록이 온 경우
    assert r.removed_chunks == 0 and r.delete_skipped and store.count() == before


def test_mass_disappearance_is_skipped_but_small_deletes_pass():
    store, emb, s = _store(), FakeEmbedder(), Settings(_env_file=None)
    docs = _many(12)
    ingest_documents(docs, emb, store, s)
    r = ingest_documents(docs[:11], emb, store, s)   # 1/12 삭제는 정상
    assert r.removed_chunks > 0 and r.delete_skipped is None
    r = ingest_documents(docs[:3], emb, store, s)    # 11개 중 8개가 한 번에 사라짐 → 가드
    assert r.removed_chunks == 0 and r.delete_skipped


def test_replace_page_drops_leftover_old_chunks():
    store, emb, s = _store(), FakeEmbedder(), Settings(_env_file=None)
    long = PolicyDocument("x", "긴 정책", "", "\n".join(["가나다라마바사" * 20] * 10), {"area": "a"})
    ingest_documents([long], emb, store, s)
    assert len(store.col.get(where={"page_id": "x"})["ids"]) > 1
    ingest_documents([replace(long, body="짧아졌다")], emb, store, s)
    got = store.col.get(where={"page_id": "x"})
    assert got["ids"] == ["x:0"] and "짧아졌다" in got["documents"][0]   # 남은 옛 청크 없음


def test_spec_cache_is_not_trusted_when_fetched_within_the_edit_minute():
    edited = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)

    def cached(dt):
        return {"last_edited": edited.isoformat(), "fetched_at": dt.isoformat()}

    assert not _cache_settled(cached(edited + timedelta(seconds=20)))   # 같은 분 안에 만든 캐시
    assert _cache_settled(cached(edited + timedelta(seconds=61)))
    assert not _cache_settled({"last_edited": edited.isoformat()})      # fetched_at 없는 옛 캐시


def test_load_spec_refetches_unsettled_cache(tmp_path):
    edited = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    calls = {"n": 0}

    class Client:
        class pages:
            @staticmethod
            async def retrieve(page_id):
                return {"id": "a" * 32, "last_edited_time": edited.isoformat(), "properties": {}, "url": ""}

        class blocks:
            class children:
                @staticmethod
                async def list(**kw):
                    calls["n"] += 1
                    return {"results": [], "has_more": False}

    asyncio.run(load_spec(Client, "a" * 32, tmp_path))   # 수정 직후(같은 분) 캐시 생성
    asyncio.run(load_spec(Client, "a" * 32, tmp_path))   # 아직 믿을 수 없으니 다시 가져온다
    assert calls["n"] == 2


def test_job_history_survives_restart(tmp_path):
    s = Settings(_env_file=None, data_dir=str(tmp_path))
    sync._append_history(s, SyncJob(id="abc", status="succeeded", created_at="t", finished_at="t2",
                                    result={"updated_pages": 1}))
    sync._jobs.clear()   # 재시작
    assert sync.get_sync_job("abc", s).result == {"updated_pages": 1}
    assert sync.last_success(s)["id"] == "abc"


def test_spec_error_is_not_counted_as_last_success(tmp_path):
    s = Settings(_env_file=None, data_dir=str(tmp_path))
    sync._append_history(s, SyncJob(id="ok", status="succeeded", created_at="t", finished_at="t1", result={}))
    sync._append_history(s, SyncJob(id="bad", status="succeeded", created_at="t", finished_at="t2",
                                    result={"spec_error": "boom"}))
    assert sync.last_success(s)["id"] == "ok"


def test_alert_after_consecutive_problems(tmp_path, monkeypatch):
    s = Settings(_env_file=None, data_dir=str(tmp_path))
    sent = []
    monkeypatch.setattr(sync, "_notify", lambda settings, msg: sent.append(msg))
    sync._unhealthy_streak = 0
    for _ in range(5):
        sync._track_health(s, SyncJob(id="x", status="failed", created_at="t", error="boom"))
    assert len(sent) == 1   # 3번째에 한 번만
    sync._track_health(s, SyncJob(id="y", status="succeeded", created_at="t", result={}))
    assert sync._unhealthy_streak == 0
    sync._track_health(s, SyncJob(id="z", status="succeeded", created_at="t", result={"delete_skipped": "wipe"}))
    assert len(sent) == 2   # 삭제 가드는 즉시
