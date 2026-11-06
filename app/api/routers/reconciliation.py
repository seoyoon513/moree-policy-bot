"""원장 ↔ 노션 대조 보고서 (A-5d). 보고만 한다 — 고치는 건 scripts/reconcile.py --fix-missing."""
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import require_admin
from app.config import Settings, get_settings
from app.notion.decisions import get_decision_repository
from app.services import reconcile

router = APIRouter(prefix="/admin/reconciliation", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("", summary="원장 ↔ 노션 대조 (보고만)")
async def get_reconciliation(s: Settings = Depends(get_settings)):
    repo = get_decision_repository(s)
    if repo is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "노션이 설정되지 않았어요 (NOTION_TOKEN·NOTION_DATA_SOURCE_ID)")
    return await reconcile.reconcile(s.data_dir, repo)
