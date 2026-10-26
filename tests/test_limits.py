"""사용자별 일일 제한, 월 예산 차단, 예산 알림 1회."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import limits
from app import usage as usage_log
from app.api import deps
from app.api.main import app
from app.config import Settings
from tests.test_rag_api import FakeGenerator, setup  # noqa: F401  (fixture 재사용)


@pytest.fixture
def usage_file(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_path / "usage.jsonl"))
    monkeypatch.delenv("USAGE_LOG_DISABLED", raising=False)
    return tmp_path


def _answer(user, cost=0.01, ts=None):
    u = usage_log.Usage("answer", "m", user=user)
    u.compute_cost()
    if ts:
        u.ts = ts
    u.cost_usd = cost
    # compute_cost를 다시 부르지 않도록 직접 기록
    import json
    with usage_log.usage_path().open("a") as f:
        f.write(json.dumps(u.__dict__) + "\n")


def test_answers_today_counts_only_that_user_today(usage_file):
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1, hours=1)).isoformat()
    _answer("a"); _answer("a"); _answer("b"); _answer("a", ts=yesterday)
    assert limits.answers_today("a") == 2 and limits.answers_today("b") == 1


def test_daily_limit_and_budget(usage_file):
    _answer("a"); _answer("a")
    s = Settings(_env_file=None, daily_answer_limit=2)
    with pytest.raises(limits.LimitExceeded) as e:
        limits.check_answer_allowed("a", s)
    assert e.value.reason == "daily"
    assert limits.check_answer_allowed("b", s)["used_today"] == 0
    s2 = Settings(_env_file=None, daily_answer_limit=0, monthly_budget_usd=0.02)
    with pytest.raises(limits.LimitExceeded) as e:
        limits.check_answer_allowed("b", s2)
    assert e.value.reason == "budget"


def test_budget_alert_sent_once_per_level(usage_file):
    s = Settings(_env_file=None, monthly_budget_usd=0.025, data_dir=str(usage_file))
    _answer("a")                       # $0.01 → 40%
    assert limits.maybe_alert(s) is None
    _answer("a")                       # $0.02 → 80%
    assert limits.maybe_alert(s) == "alert"
    assert limits.maybe_alert(s) is None   # 같은 달 같은 단계는 한 번만
    _answer("a")                       # $0.03 → 120%
    assert limits.maybe_alert(s) == "100"


def test_answer_endpoint_returns_429_over_daily_limit(setup, usage_file):  # noqa: F811
    _answer("u1")
    app.dependency_overrides[deps.get_settings] = lambda: Settings(_env_file=None, rag_min_score=0.0, daily_answer_limit=1)
    c = TestClient(app)
    r = c.post("/policies/answers", json={"question": "리뷰 누가 쓸 수 있어?"}, headers={"X-User-Id": "u1"})
    assert r.status_code == 429
    r = c.post("/policies/answers", json={"question": "리뷰 누가 쓸 수 있어?"}, headers={"X-User-Id": "u2"})
    assert r.status_code == 200 and r.json()["quota"]["used_today"] == 1
