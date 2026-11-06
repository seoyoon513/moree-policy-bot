"""Notion(또는 history.jsonl) → 벡터DB 동기화 '작업(job)' 리소스.

RESTful 설계 메모
- 오래 걸리는 작업은 동사형 URL(/sync, /reindex) 대신 '작업'을 리소스로 만든다.
- POST /admin/sync-jobs        → 202 Accepted + Location 헤더 (아직 안 끝났다는 뜻)
- GET  /admin/sync-jobs/{id}   → 진행 상태 폴링
- 작업 저장소는 메모리 dict라 서버 재시작 시 사라짐 (MVP 한정).
  대신 마지막 결과(성공 시각·연속 실패 수)는 data/sync_state.json에 남겨 /health에서 본다 (app/ops.py).
"""
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status

from app.api.deps import get_embedder, get_store, require_admin
from app.api.schemas import SyncJob, SyncJobCreate
from app import ops
from shared.config import Settings, get_settings
from app.rag.embedder import Embedder
from app.rag.ingest import ingest_from_source
from app.rag.store import VectorStore

log = logging.getLogger("policy-api")

router = APIRouter(prefix="/admin/sync-jobs", tags=["admin"], dependencies=[Depends(require_admin)])

_jobs: dict[str, SyncJob] = {}
_lock = threading.Lock()
_rerun = False  # 돌고 있는 동안 새 요청이 오면, 끝난 뒤 한 번 더 (방금 저장한 결정을 그 작업이 못 읽었을 수 있다)

# 노션은 방금 만든 페이지가 query 결과에 뜨기까지 몇 초 걸릴 수 있어서 잠깐 기다렸다가 동기화
AFTER_WRITE_DELAY_S = 3.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _running() -> bool:
    return any(j.status in ("queued", "running") for j in _jobs.values())


def _new_job() -> SyncJob:
    job = SyncJob(id=uuid.uuid4().hex[:12], status="queued", created_at=_now())
    _jobs[job.id] = job
    return job


def request_sync(settings: Settings, embedder: Embedder, store: VectorStore,
                 delay: float = AFTER_WRITE_DELAY_S) -> None:
    """쓰기(승인 저장) 뒤에 API 안에서 부른다 (백그라운드 스레드). 이미 돌고 있으면 끝난 뒤 한 번 더 돌게 예약.
    전에는 봇이 HTTP로 작업을 만들고 409면 5초씩 기다리며 재시도했다."""
    global _rerun
    time.sleep(delay)
    with _lock:
        if _running():
            _rerun = True
            return
        job = _new_job()
    _run(job.id, False, settings, embedder, store)


def _run(job_id: str, force: bool, settings: Settings, embedder: Embedder, store: VectorStore,
         allow_mass_delete: bool = False) -> None:
    global _rerun
    while True:
        _run_once(job_id, force, settings, embedder, store, allow_mass_delete)
        with _lock:
            if not _rerun:
                return
            _rerun, force, allow_mass_delete = False, False, False
            job_id = _new_job().id


def _run_once(job_id: str, force: bool, settings: Settings, embedder: Embedder, store: VectorStore,
              allow_mass_delete: bool = False) -> None:
    job = _jobs[job_id]
    job.status = "running"
    try:
        result = ingest_from_source(settings, embedder, store, force=force, allow_mass_delete=allow_mass_delete)
        if result.delete_blocked:
            ops.notify(settings, f"⚠️ 검색 인덱스 대량 삭제를 멈췄어요 — {result.delete_blocked}")
        job.result = result.__dict__
        job.status = "succeeded"
        log.info("동기화 성공 (job=%s, 갱신 %d · 건너뜀 %d · 삭제 %d)", job_id, result.updated_pages,
                 result.skipped_pages, result.removed_chunks, extra={"job_id": job_id})
        ops.record_sync_result(settings, ok=True, result=job.result)
    except Exception as e:  # noqa: BLE001 — 작업 상태로 노출
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"
        log.exception("동기화 실패 (job=%s)", job_id, extra={"job_id": job_id})
        ops.record_sync_result(settings, ok=False, error=job.error)
    finally:
        job.finished_at = _now()


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=SyncJob, summary="동기화 작업 생성")
def create_sync_job(
    body: SyncJobCreate,
    background: BackgroundTasks,
    response: Response,
    settings: Settings = Depends(get_settings),
    embedder: Embedder = Depends(get_embedder),
    store: VectorStore = Depends(get_store),
):
    with _lock:
        if _running():
            raise HTTPException(status.HTTP_409_CONFLICT, "이미 진행 중인 동기화 작업이 있습니다")
        job = _new_job()
    background.add_task(_run, job.id, body.force, settings, embedder, store, body.allow_mass_delete)
    response.headers["Location"] = f"/admin/sync-jobs/{job.id}"
    return job


@router.get("/{job_id}", response_model=SyncJob, summary="동기화 작업 상태 조회")
def get_sync_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "작업을 찾을 수 없습니다")
    return job
