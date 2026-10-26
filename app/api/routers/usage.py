"""토큰 사용량 집계 조회 (관리자 전용).

GET /admin/usage?days=7 → 기능별(summarize/answer/embed_query/embed_index) 호출 수·토큰·비용·지연
"""
from fastapi import APIRouter, Depends, Query

from app import limits
from app import usage as usage_log
from app.api.deps import require_admin
from app.config import Settings, get_settings

router = APIRouter(prefix="/admin/usage", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("", summary="토큰 사용량/비용 집계")
def get_usage(days: int = Query(7, ge=1, le=365), s: Settings = Depends(get_settings)):
    out = usage_log.summarize(days)
    today = {}
    for r in usage_log.load(days=2):
        if r.get("feature") == "answer" and r.get("user"):
            if limits._kst(r["ts"]).date() == limits.datetime.now(limits.KST).date():
                today[r["user"]] = today.get(r["user"], 0) + 1
    out["budget"] = {"month_spend_usd": limits.month_spend(), "monthly_budget_usd": s.monthly_budget_usd or None,
                     "daily_answer_limit": s.daily_answer_limit or None, "answers_today_by_user": today}
    return out
