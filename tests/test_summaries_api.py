"""A-6: 정리·승인 API (쓰기 경로 한 곳). 가짜 LLM·가짜 노션 저장소, 네트워크 없음."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import limits
from app.api import deps
from app.api.main import app
from app.api.routers import summaries as summaries_router
from app.api.routers import sync
from shared.config import Settings
from app.services import approvals, spec_reflect, storage
from app.services.summarizer import SummaryError

T0 = datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc)


def _payload(n=3, **kw):
    msgs = [{"index": i, "author": f"작성자{i}", "created_at": (T0 + timedelta(hours=i)).isoformat(),
             "content": f"메시지 {i}", "jump_url": f"https://discord.com/channels/1/2/{i}"} for i in range(1, n + 1)]
    return {"thread_id": "thread-1", "thread_name": "가짜 스레드", "thread_url": "https://discord.com/channels/1/2",
            "messages": msgs, "user_id": "u1"} | kw


def _decision(**kw):
    d = {"area": "리뷰", "topic": "리뷰 사진 개수", "status": "최종결정", "decision": "최대 5장", "rationale": None,
         "decided_by": None, "scope": None, "external_refs": [], "open_questions": [], "source_messages": [2],
         "relation": "new", "prior_id": None}
    return d | kw


class FakeRepo:
    def __init__(self):
        self.calls = []

    async def add(self, record):
        self.calls.append(("add", record["id"]))
        return f"https://notion.so/{record['id']}"

    async def update(self, record, previous):
        self.calls.append(("update", record["id"]))
        return f"https://notion.so/{record['id']}"

    async def find_url(self, record_id):
        return None


@pytest.fixture
def api(tmp_path, monkeypatch):
    repo, synced, summarized = FakeRepo(), [], []

    async def fake_summarize(name, messages, previous=None, since=None, user=None):
        summarized.append(user)
        return [_decision()]

    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(storage, "get_decision_repository", lambda: repo)
    monkeypatch.setattr(approvals, "summarize_thread", fake_summarize)
    monkeypatch.setattr(spec_reflect, "NOTION_SPEC_PAGES", {})
    monkeypatch.setattr(spec_reflect, "NOTION_SPEC_INBOX_PAGE", "")
    monkeypatch.setattr(sync, "request_sync", lambda *a, **k: synced.append(1))
    app.dependency_overrides[deps.get_settings] = lambda: Settings(data_dir=str(tmp_path))
    app.dependency_overrides[deps.get_embedder] = lambda: None
    app.dependency_overrides[deps.get_store] = lambda: None
    yield TestClient(app), repo, synced, summarized, tmp_path
    app.dependency_overrides.clear()


def _approve(client, sid, **kw):
    body = {"decisions": [_decision()], "approved_by": "승인자", "user_id": "u1"} | kw
    return client.post(f"/summaries/{sid}/approval", json=body)


def test_summary_creates_pending_with_location(api):
    client, _, _, summarized, tmp = api
    r = client.post("/summaries", json=_payload())
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "ready" and r.headers["Location"] == f"/summaries/{body['summary_id']}"
    assert [d["topic"] for d in body["decisions"]] == ["리뷰 사진 개수"]
    assert (tmp / "pending" / f"{body['summary_id']}.json").exists()  # API를 재시작해도 승인할 수 있게
    assert summarized == ["u1"]  # 요약 비용이 요청한 사람으로 기록되게 user가 넘어간다


def test_approval_saves_once_and_is_idempotent(api):
    client, repo, synced, _, _ = api
    sid = client.post("/summaries", json=_payload()).json()["summary_id"]
    first = _approve(client, sid)
    assert first.status_code == 200
    saved = first.json()["saved"]
    assert len(saved) == 1 and saved[0]["notion_url"].endswith(saved[0]["id"]) and not first.json()["replayed"]
    # 근거 메시지 #2 시각이 결정일 → 대기 파일을 거쳐도 메시지 번호(int 키)로 찾아진다
    assert storage.current_records("thread-1")[0]["decided_at"].startswith("2026-06-01T14:00")
    again = _approve(client, sid)
    assert again.json()["replayed"] and again.json()["saved"] == saved
    assert repo.calls == [("add", saved[0]["id"])]  # 두 번 와도 노션 저장은 한 번
    assert synced == [1]                              # 재인덱싱도 한 번


def test_after_approval_same_messages_mean_nothing_new(api):
    client, *_ = api
    sid = client.post("/summaries", json=_payload()).json()["summary_id"]
    _approve(client, sid)
    r = client.post("/summaries", json=_payload())
    assert r.status_code == 200 and r.json()["status"] == "nothing_new"


def test_unknown_or_malicious_summary_id_is_404(api):
    client, *_ = api
    assert _approve(client, "deadbeef00000000").status_code == 404
    assert _approve(client, "..%2F..%2Fsecret").status_code == 404


def test_invalid_area_is_rejected_before_saving(api):
    client, repo, *_ = api
    sid = client.post("/summaries", json=_payload()).json()["summary_id"]
    r = _approve(client, sid, decisions=[_decision(area="없는영역")])
    assert r.status_code == 422 and repo.calls == []


def test_summary_failures_map_to_http(api, monkeypatch):
    client, *_ = api

    async def broken(*a, **k):
        raise SummaryError("Claude 서버가 혼잡해요. 잠시 뒤에 다시 시도해 주세요.")

    monkeypatch.setattr(approvals, "summarize_thread", broken)
    r = client.post("/summaries", json=_payload())
    assert r.status_code == 502 and "혼잡" in r.json()["detail"]

    async def over(*a, **k):
        raise limits.LimitExceeded("budget", "이번 달 예산을 모두 사용")

    monkeypatch.setattr(approvals, "summarize_thread", over)
    assert client.post("/summaries", json=_payload()).status_code == 429


def test_write_api_requires_key(api):
    client, *_ = api
    app.dependency_overrides[deps.get_settings] = lambda: Settings(api_key="secret")
    assert client.post("/summaries", json=_payload()).status_code == 401


def test_pending_cleanup(tmp_path):
    store = approvals.PendingStore(tmp_path)
    store.save({"id": "old0000000000000", "source": {}})
    future = datetime.now(timezone.utc) + approvals.PENDING_TTL + timedelta(minutes=1)
    assert store.cleanup(now=future) == 1


# --- 재인덱싱: 돌고 있으면 끝난 뒤 한 번 더 ---
def test_request_sync_reruns_after_running_job(monkeypatch, tmp_path):
    from types import SimpleNamespace
    runs = []
    monkeypatch.setattr(sync, "_jobs", {})
    monkeypatch.setattr(sync, "_rerun", False)
    monkeypatch.setattr(sync.ops, "record_sync_result", lambda *a, **k: None)

    def fake_ingest(settings, embedder, store, force=False, allow_mass_delete=False):
        runs.append(1)
        if len(runs) == 1:  # 첫 작업이 도는 중에 새 승인이 들어온다
            sync.request_sync(settings, embedder, store, delay=0)
        return SimpleNamespace(updated_pages=1, skipped_pages=0, removed_chunks=0, delete_blocked=None)

    monkeypatch.setattr(sync, "ingest_from_source", fake_ingest)
    sync.request_sync(Settings(data_dir=str(tmp_path)), None, None, delay=0)
    assert len(runs) == 2 and all(j.status == "succeeded" for j in sync._jobs.values())


def test_router_module_exposes_pending_store_dependency():
    assert summaries_router.get_pending_store(Settings(data_dir="x")).dir.as_posix().endswith("x/pending")
