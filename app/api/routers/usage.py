"""토큰 사용량 집계 조회 (관리자 전용).

GET /admin/usage?days=7 → 기능별(summarize/answer/embed_query/embed_index) 호출 수·토큰·비용·지연
"""
from fastapi import APIRouter, Depends, Query

from app import usage as usage_log
from app.api.deps import require_admin

router = APIRouter(prefix="/admin/usage", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("", summary="토큰 사용량/비용 집계")
def get_usage(days: int = Query(7, ge=1, le=365)):
    return usage_log.summarize(days)
