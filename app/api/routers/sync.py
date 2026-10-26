"""Notion(또는 history.jsonl) → 벡터DB 동기화 '작업(job)' 리소스.

RESTful 설계 메모
- 오래 걸리는 작업은 동사형 URL(/sync, /reindex) 대신 '작업'을 리소스로 만든다.
- POST /admin/sync-jobs        → 202 Accepted + Location 헤더 (아직 안 끝났다는 뜻)
- GET  /admin/sync-jobs/{id}   → 진행 상태 폴링
- 진행 중 상태는 메모리 dict, 끝난 작업은 {DATA_DIR}/sync_jobs.jsonl에 한 줄씩 남긴다.
  재시작해도 이력이 남고, 마지막 성공 시각(/health)과 연속 실패 알림의 근거가 된다.
- SYNC_INTERVAL_MINUTES > 0이면 서버가 주기적으로 같은 작업을 만든다 (app/api/main.py의 lifespan).
"""
import asyncio
import json
import logging
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status

from app.api.deps import get_embedder, get_store, require_admin
from app.api.schemas import SyncJob, SyncJobCreate
from app.config import Settings, get_settings
from app.rag.embedder import Embedder
from app.rag.ingest import ingest_from_source
from app.rag.store import VectorStore

log = logging.getLogger("policy-api")

router = APIRouter(prefix="/admin/sync-jobs", tags=["admin"], dependencies=[Depends(require_admin)])

_jobs: dict[str, SyncJob] = {}
_unhealthy_streak = 0   # 연속으로 '문제 있는' 작업 수 (실패, 정책서 로딩 실패, 삭제 가드 발동)
ALERT_AFTER = 3         # 이 횟수 연속이면 알림 (삭제 가드는 한 번이어도 즉시)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def history_path(settings: Settings) -> Path:
    return Path(settings.data_dir) / "sync_jobs.jsonl"


def _append_history(settings: Settings, job: SyncJob) -> None:
    try:
        path = history_path(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(job.model_dump(), ensure_ascii=False) + "\n")
    except OSError as e:  # 이력 기록 실패가 동기화 결과를 바꾸면 안 된다
        log.warning("동기화 이력 기록 실패: %s", e)


def read_history(settings: Settings) -> list[dict]:
    path = history_path(settings)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def last_success(settings: Settings) -> dict | None:
    """마지막으로 성공한 작업 (정책서 로딩 실패가 섞인 작업은 '완전한 성공'이 아니라 제외)."""
    for row in reversed(read_history(settings)):
        if row.get("status") == "succeeded" and not (row.get("result") or {}).get("spec_error"):
            return {"id": row["id"], "finished_at": row.get("finished_at")}
    return None


def _notify(settings: Settings, msg: str) -> None:
    log.warning(msg)
    if not settings.budget_alert_webhook_url:
        return
    try:
        req = urllib.request.Request(settings.budget_alert_webhook_url, method="POST",
                                     data=json.dumps({"content": msg}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "policy-bot"})
        urllib.request.urlopen(req, timeout=5).close()
    except OSError as e:
        log.warning("동기화 알림 전송 실패: %s", e)


def _track_health(settings: Settings, job: SyncJob) -> None:
    global _unhealthy_streak
    result = job.result or {}
    problem = job.error or result.get("spec_error") or result.get("delete_skipped")
    if not problem:
        _unhealthy_streak = 0
        return
    _unhealthy_streak += 1
    if result.get("delete_skipped") or _unhealthy_streak == ALERT_AFTER:  # 연속 실패는 한 번만 알린다
        _notify(settings, f"⚠️ 정책 인덱스 동기화 문제 (연속 {_unhealthy_streak}회): {problem}")


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
        _append_history(settings, job)
        _track_health(settings, job)


def create_job() -> SyncJob | None:
    """진행 중인 작업이 있으면 None (HTTP 요청과 주기 실행이 같은 규칙을 쓴다)."""
    if any(j.status in ("queued", "running") for j in _jobs.values()):
        return None
    job = SyncJob(id=uuid.uuid4().hex[:12], status="queued", created_at=_now())
    _jobs[job.id] = job
    return job


async def run_periodically(settings: Settings, embedder: Embedder, store: VectorStore) -> None:
    """SYNC_INTERVAL_MINUTES마다 동기화. 사람이 sync를 잊어도 정책서 수정이 인덱스에 따라온다."""
    interval = settings.sync_interval_minutes * 60
    while True:
        await asyncio.sleep(interval)
        job = create_job()
        if job is None:   # 승인 직후 재인덱싱 등이 돌고 있으면 이번 주기는 건너뛴다
            continue
        await asyncio.to_thread(_run, job.id, False, settings, embedder, store)  # 동기 코드라 이벤트 루프를 막지 않게


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=SyncJob, summary="동기화 작업 생성")
def create_sync_job(
    body: SyncJobCreate,
    background: BackgroundTasks,
    response: Response,
    settings: Settings = Depends(get_settings),
    embedder: Embedder = Depends(get_embedder),
    store: VectorStore = Depends(get_store),
):
    job = create_job()
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 진행 중인 동기화 작업이 있습니다")
    background.add_task(_run, job.id, body.force, settings, embedder, store)
    response.headers["Location"] = f"/admin/sync-jobs/{job.id}"
    return job


@router.get("/{job_id}", response_model=SyncJob, summary="동기화 작업 상태 조회")
def get_sync_job(job_id: str, settings: Settings = Depends(get_settings)):
    job = _jobs.get(job_id)
    if job:
        return job
    for row in reversed(read_history(settings)):   # 재시작 전에 끝난 작업
        if row.get("id") == job_id:
            return SyncJob(**row)
    raise HTTPException(status.HTTP_404_NOT_FOUND, "작업을 찾을 수 없습니다")
