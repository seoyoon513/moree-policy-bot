"""토큰 기록/집계, 평가 지표 계산 테스트 (API 키 불필요)."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import usage as usage_log
from app.api import deps
from app.api.main import app
from app.config import Settings
from app.evaluation import (GoldenItem, aggregate_answers, aggregate_retrieval, answer_scores,
                            retrieval_scores)


@pytest.fixture
def usage_file(tmp_path, monkeypatch):
    p = tmp_path / "usage.jsonl"
    monkeypatch.setenv("USAGE_LOG_PATH", str(p))
    monkeypatch.delenv("USAGE_LOG_DISABLED", raising=False)
    return p


def test_cost_uses_price_table(monkeypatch):
    monkeypatch.setenv("PRICE_LLM_INPUT_PER_MTOK", "1")
    monkeypatch.setenv("PRICE_LLM_OUTPUT_PER_MTOK", "10")
    u = usage_log.Usage("answer", "m", input_tokens=2_000_000, output_tokens=100_000).compute_cost()
    assert u.cost_usd == 3.0


def test_from_anthropic_records_cache_tokens(usage_file):
    resp = SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=50,
                                                 cache_read_input_tokens=1000, cache_creation_input_tokens=0))
    u = usage_log.from_anthropic("summarize", "m", resp, latency_ms=12)
    assert (u.input_tokens, u.output_tokens, u.cache_read_tokens) == (100, 50, 1000)
    assert usage_file.read_text().count("\n") == 1


def test_summarize_groups_by_feature(usage_file):
    for feat, i, o in [("answer", 1000, 100), ("answer", 3000, 300), ("embed_query", 20, 0)]:
        usage_log.record(usage_log.Usage(feat, "m", kind="embedding" if feat.startswith("embed") else "llm",
                                         input_tokens=i, output_tokens=o, latency_ms=10))
    s = usage_log.summarize(days=1)
    assert s["total_calls"] == 3
    assert s["features"]["answer"]["avg_input_tokens"] == 2000
    assert s["features"]["embed_query"]["calls"] == 1


def test_usage_endpoint_requires_admin(usage_file):
    app.dependency_overrides[deps.get_settings] = lambda: Settings(_env_file=None, admin_token="t")
    try:
        c = TestClient(app)
        assert c.get("/admin/usage").status_code == 401
        r = c.get("/admin/usage", headers={"X-Admin-Token": "t"})
        assert r.status_code == 200 and r.json()["total_calls"] == 0
    finally:
        app.dependency_overrides.clear()


POS = GoldenItem("q", "질문", expected_titles=["주소 검색"], must_include=["도로명"])
NEG = GoldenItem("n", "없는 질문")


def test_retrieval_metrics():
    rows = [
        retrieval_scores(POS, ["좌표 조회", "행사 등록 주소 검색 방식"]),  # 2위에서 찾음
        retrieval_scores(POS, ["무관"]),                                 # 못 찾음
        retrieval_scores(NEG, []),                                      # 올바르게 거절
        retrieval_scores(NEG, ["무관"]),                                 # 엉뚱한 결과 반환
    ]
    a = aggregate_retrieval(rows)
    assert a["hit_at_k"] == 0.5 and a["mrr"] == 0.25 and a["top1"] == 0.0
    assert a["reject_acc"] == 0.5 and a["overall_acc"] == 0.5


def test_answer_metrics():
    good = answer_scores(POS, "최신 결론: 도로명주소 API 사용 [1]")
    bad = answer_scores(POS, "구글 API를 쓴다")
    refused = answer_scores(NEG, "문서에서 확인되지 않음")
    a = aggregate_answers([good, bad, refused])
    assert good["keywords_ok"] and good["cited"]
    assert bad["missing"] == ["도로명"]
    assert a["keyword_acc"] == 0.5 and a["citation_rate"] == 0.5 and a["reject_acc"] == 1.0


# --- 리랭커 ---
from app.rag.models import SearchHit
from app.rag.reranker import LLMReranker


class _FakeMsgs:
    def __init__(self, text): self.text = text
    def create(self, **kw):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)],
                               usage=SimpleNamespace(input_tokens=500, output_tokens=20))


def _hits():
    return [SearchHit(f"p{i}", f"결정{i}", "", f"[정책] 결정{i}\n본문{i}", 0.5 - i * 0.01, {}) for i in range(1, 5)]


def test_reranker_reorders_and_drops_irrelevant():
    r = LLMReranker("k", "m", client=SimpleNamespace(messages=_FakeMsgs("[[1, 1], [2, 3], [3, 2], [4, 0]]")))
    out = r.rerank("q", _hits(), k=5)
    assert [h.page_id for h in out] == ["p2", "p3"]       # 3점 → 2점, 1점 이하는 버림
    assert out[0].metadata["rerank"] == 3


def test_reranker_rejects_when_nothing_relevant():
    r = LLMReranker("k", "m", client=SimpleNamespace(messages=_FakeMsgs("[[1, 1], [2, 0]]")))
    assert r.rerank("q", _hits(), k=5) == []


def test_reranker_falls_back_to_vector_order_on_bad_output():
    r = LLMReranker("k", "m", client=SimpleNamespace(messages=_FakeMsgs("모르겠어요")))
    assert [h.page_id for h in r.rerank("q", _hits(), k=2)] == ["p1", "p2"]


def test_small_model_uses_its_own_price():
    big = usage_log.Usage("answer", "claude-sonnet-5-5", input_tokens=1_000_000).compute_cost()
    small = usage_log.Usage("rerank", "claude-haiku-5-5", input_tokens=1_000_000).compute_cost()
    assert big.cost_usd == 3.0 and small.cost_usd == 1.0


def test_reranker_counts_fallbacks():
    r = LLMReranker("k", "m", client=SimpleNamespace(messages=_FakeMsgs("1번은 관련 있고...")))
    r.rerank("q", _hits(), k=2)
    assert (r.calls, r.fallbacks) == (1, 1) and r.last_raw.startswith("1번")


class _FakeToolMsgs:
    def __init__(self, pairs): self.pairs = pairs; self.kw = None
    def create(self, **kw):
        self.kw = kw
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"scores": self.pairs})],
                               usage=SimpleNamespace(input_tokens=500, output_tokens=40), stop_reason="tool_use")


def test_reranker_uses_forced_tool_output():
    msgs = _FakeToolMsgs([[1, 0], [2, 2], [3, 3], [4, 1]])
    r = LLMReranker("k", "m", client=SimpleNamespace(messages=msgs))
    assert [h.page_id for h in r.rerank("q", _hits(), k=5)] == ["p3", "p2"]
    assert msgs.kw["tool_choice"] == {"type": "tool", "name": "submit_scores"}
    assert r.fallbacks == 0
