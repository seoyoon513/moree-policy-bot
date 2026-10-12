"""Notion(또는 history.jsonl) → 벡터DB 동기화 '작업(job)' 리소스.

RESTful 설계 메모
- 오래 걸리는 작업은 동사형 URL(/sync, /reindex) 대신 '작업'을 리소스로 만든다.
- POST /admin/sync-jobs        → 202 Accepted + Location 헤더 (아직 안 끝났다는 뜻)
- GET  /admin/sync-jobs/{id}   → 진행 상태 폴링
- 작업 저장소는 메모리 dict라 서버 재시작 시 사라짐 (MVP 한정).
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status

from app.api.deps import get_embedder, get_store, require_admin
from app.api.schemas import SyncJob, SyncJobCreate
from app.config import Settings, get_settings
from app.rag.embedder import Embedder
from app.rag.ingest import ingest_from_source
from app.rag.store import VectorStore

router = APIRouter(prefix="/admin/sync-jobs", tags=["admin"], dependencies=[Depends(require_admin)])

_jobs: dict[str, SyncJob] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(job_id: str, force: bool, settings: Settings, embedder: Embedder, store: VectorStore) -> None:
    job = _jobs[job_id]
    job.status = "running"
    try:
        result = ingest_from_source(settings, embedder, store, force=force)
        job.result = result.__dict__
        job.status = "succeeded"
    except Exception as e:  # noqa: BLE001 — 작업 상태로 노출
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"
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
    if any(j.status in ("queued", "running") for j in _jobs.values()):
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 진행 중인 동기화 작업이 있습니다")
    job = SyncJob(id=uuid.uuid4().hex[:12], status="queued", created_at=_now())
    _jobs[job.id] = job
    background.add_task(_run, job.id, body.force, settings, embedder, store)
    response.headers["Location"] = f"/admin/sync-jobs/{job.id}"
    return job


@router.get("/{job_id}", response_model=SyncJob, summary="동기화 작업 상태 조회")
def get_sync_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "작업을 찾을 수 없습니다")
    return job
