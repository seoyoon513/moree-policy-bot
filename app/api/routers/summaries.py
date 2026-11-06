"""정리(요약)와 승인 리소스 (A-6: 쓰기 경로를 API 한 곳으로).

RESTful 설계 메모
- POST /summaries                     : 스레드 메시지 → 정리 결과. 승인 대기가 생기면 201 + Location,
                                        새로 만든 게 없으면(새 메시지 없음·달라진 결정 없음) 200
- POST /summaries/{id}/approval       : 그 정리를 승인(하위 리소스 생성). 같은 승인이 다시 오면 저장하지 않고
                                        첫 결과를 돌려준다(멱등) → 봇이 네트워크 문제로 다시 보내도 안전
- 저장 후 재인덱싱은 응답을 막지 않게 백그라운드로 (같은 프로세스라 HTTP 왕복 없음)
쓰기 API라 X-API-Key가 필요하다 (compose 내부망에서 봇만 부른다).
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status

from app import limits
from app.api.deps import get_embedder, get_store, require_api_key
from app.api.schemas import ApprovalCreate, ApprovalResult, SummaryCreate, SummaryResult
from app.config import Settings, get_settings
from app.domain import ThreadMessage
from app.rag.embedder import Embedder
from app.rag.store import VectorStore
from app.services import approvals
from app.services.summarizer import SummaryError

router = APIRouter(prefix="/summaries", tags=["summaries"], dependencies=[Depends(require_api_key)])


def get_pending_store(s: Settings = Depends(get_settings)) -> approvals.PendingStore:
    return approvals.PendingStore(s.data_dir)


@router.post("", response_model=SummaryResult, summary="스레드 정리(요약) → 승인 대기",
             responses={201: {"description": "승인 대기 생성"}, 200: {"description": "새로 만든 것 없음"}})
async def create_summary(
    body: SummaryCreate,
    response: Response,
    store: approvals.PendingStore = Depends(get_pending_store),
):
    messages = [ThreadMessage(m.index, m.author, m.created_at, m.content, m.jump_url, list(m.agreed_by))
                for m in body.messages]
    try:
        out = await approvals.create_summary(store, thread_id=body.thread_id, thread_name=body.thread_name,
                                             thread_url=body.thread_url, messages=messages, user=body.user_id)
    except limits.LimitExceeded as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e)) from e
    except SummaryError as e:
        # LLM 실패·응답 파싱 실패: 상위 서비스 문제라 502. 문구는 사용자에게 그대로 보여 줄 수 있는 것
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"요약에 실패했어요: {e}") from e
    if out["status"] == "ready":
        response.status_code = status.HTTP_201_CREATED
        response.headers["Location"] = f"/summaries/{out['summary_id']}"
    return out


@router.post("/{summary_id}/approval", response_model=ApprovalResult, summary="정리 결과 승인 → 저장·정책서 반영")
async def approve_summary(
    summary_id: str,
    body: ApprovalCreate,
    background: BackgroundTasks,
    store: approvals.PendingStore = Depends(get_pending_store),
    settings: Settings = Depends(get_settings),
    embedder: Embedder = Depends(get_embedder),
    vstore: VectorStore = Depends(get_store),
):
    try:
        result = await approvals.approve(store, summary_id, decisions=[d.model_dump() for d in body.decisions],
                                         approved_by=body.approved_by, user=body.user_id)
    except approvals.SummaryNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "정리 결과를 찾을 수 없어요. 다시 /정책정리 해 주세요.") from e
    if not result.get("replayed"):
        from app.api.routers.sync import request_sync
        background.add_task(request_sync, settings, embedder, vstore)  # 새 결정을 검색 인덱스에 반영
    return result
