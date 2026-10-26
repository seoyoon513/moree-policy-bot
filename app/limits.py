"""사용량 제한: 사용자별 하루 답변 횟수 + 월 예산.

왜 필요한가 (2026-10-19 객관적 평가)
- 비용 측정과 절감은 했지만 '상한선'이 없었다. 한 사람이 /정책질문을 연달아 치거나
  스크립트가 반복 호출해도 막을 장치가 없었다. API 키는 외부인만 막는다.

기준 데이터는 data/usage.jsonl 하나다 (봇·API가 같이 기록). 별도 카운터를 두지 않아서
서버를 재시작해도 횟수가 유지되고, 실제로 돈이 나간 호출만 센다.
규모가 커지면(수만 줄 이상) 매 요청 파일 스캔이 느려지므로 그때 DB나 캐시로 옮긴다.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app import usage as usage_log
from app.config import Settings

KST = ZoneInfo("Asia/Seoul")
log = logging.getLogger("policy-api")


class LimitExceeded(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason  # "daily" | "budget"


def _kst(ts: str) -> datetime:
    return datetime.fromisoformat(ts).astimezone(KST)


def answers_today(user: str, now: datetime | None = None) -> int:
    today = (now or datetime.now(KST)).astimezone(KST).date()
    return sum(1 for r in usage_log.load(days=2)
               if r.get("feature") == "answer" and r.get("user") == user and _kst(r["ts"]).date() == today)


def month_spend(now: datetime | None = None) -> float:
    n = (now or datetime.now(KST)).astimezone(KST)
    return round(sum(r.get("cost_usd", 0.0) for r in usage_log.load(days=32)
                     if (t := _kst(r["ts"])).year == n.year and t.month == n.month), 4)


def check_answer_allowed(user: str | None, s: Settings) -> dict:
    """답변 전에 호출. 막아야 하면 LimitExceeded, 아니면 현재 사용량을 돌려준다."""
    spend = month_spend()
    if s.monthly_budget_usd and s.budget_hard_stop and spend >= s.monthly_budget_usd:
        raise LimitExceeded("budget", f"이번 달 예산(${s.monthly_budget_usd:.2f})을 모두 사용해서 답변을 멈췄어요. 관리자에게 문의해 주세요.")
    used = answers_today(user) if user else 0
    if user and s.daily_answer_limit and used >= s.daily_answer_limit:
        raise LimitExceeded("daily", f"오늘 질문 한도({s.daily_answer_limit}회)를 다 썼어요. 내일 다시 시도하거나 /정책검색을 이용해 주세요.")
    return {"used_today": used, "daily_limit": s.daily_answer_limit or None,
            "month_spend_usd": spend, "monthly_budget_usd": s.monthly_budget_usd or None}


def _alert_state_path(s: Settings) -> Path:
    return Path(s.data_dir) / "budget_alerts.json"


def maybe_alert(s: Settings, now: datetime | None = None) -> str | None:
    """예산 비율을 처음 넘었을 때 한 번만 알린다 (월·단계별). 보낸 단계를 돌려준다."""
    if not s.monthly_budget_usd:
        return None
    n = (now or datetime.now(KST)).astimezone(KST)
    spend = month_spend(n)
    ratio = round(spend / s.monthly_budget_usd, 4)  # 0.02/0.025=0.7999… 같은 부동소수점 오차 방지
    level = "100" if ratio >= 1 else ("alert" if ratio >= s.budget_alert_ratio else None)
    if not level:
        return None
    key = f"{n:%Y-%m}:{level}"
    path = _alert_state_path(s)
    try:
        sent = json.loads(path.read_text()) if path.exists() else []
    except (OSError, ValueError):
        sent = []
    if key in sent:
        return None
    msg = (f"⚠️ 정책 봇 이번 달 비용 ${spend:.2f} / 예산 ${s.monthly_budget_usd:.2f} ({ratio:.0%})"
           + (" — 예산 소진으로 답변을 멈췄어요." if level == "100" and s.budget_hard_stop else ""))
    log.warning(msg)
    if s.budget_alert_webhook_url:
        try:
            req = urllib.request.Request(s.budget_alert_webhook_url, method="POST",
                                         data=json.dumps({"content": msg}).encode(),
                                         headers={"Content-Type": "application/json", "User-Agent": "policy-bot"})
            urllib.request.urlopen(req, timeout=5).close()
        except OSError as e:  # 알림 실패가 답변을 막으면 안 된다
            log.warning("예산 알림 전송 실패: %s", e)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sent + [key]))
    except OSError:
        pass
    return level
