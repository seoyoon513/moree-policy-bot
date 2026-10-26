"""외부 API 키 없이 돌아가는 테스트. 가짜 임베더(글자 n-gram 해시) + 메모리 Chroma 사용."""
import hashlib
import math
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.main import app
from app.config import Settings
from app.rag.chunker import chunk_document
from app.rag.ingest import ingest_documents
from app.rag.models import PolicyDocument
from app.rag.retriever import Retriever
from app.rag.store import VectorStore

DIM = 256


class FakeEmbedder:
    """문자 2-gram을 해시해서 벡터로. 겹치는 단어가 많을수록 유사도가 높아진다."""

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * DIM
        t = text.replace(" ", "")
        for i in range(len(t) - 1):
            v[int(hashlib.md5(t[i:i + 2].encode()).hexdigest(), 16) % DIM] += 1
        n = math.sqrt(sum(x * x for x in v)) or 1
        return [x / n for x in v]

    def embed_documents(self, texts): return [self._vec(t) for t in texts]
    def embed_query(self, text): return self._vec(text)


class FakeGenerator:
    def answer(self, question, hits, user=None, context=None):
        from app.rag.generator import GeneratedAnswer
        from app.usage import Usage
        if not hits:
            return GeneratedAnswer("없음")
        u = Usage("answer", "fake", input_tokens=1000, output_tokens=200, latency_ms=5).compute_cost()
        return GeneratedAnswer(f"최신 결론: {hits[0].title} [1]", u)


DOCS = [
    PolicyDocument("p1", "리뷰 작성 가능 조건", "https://notion.so/p1",
                   "행사 참여 인증(발도장)을 한 사용자만 리뷰를 작성할 수 있다.\n사진은 최대 5장.",
                   {"area": "리뷰", "status": "최종결정", "decider": "PM", "decided_at": "2026-09-20", "last_edited": "t1"}),
    PolicyDocument("p2", "리뷰 신고 처리", "https://notion.so/p2",
                   "신고 3회 누적 시 리뷰를 블라인드 처리한다.",
                   {"area": "리뷰", "status": "논의중", "last_edited": "t1"}),
    PolicyDocument("p3", "북마크 정렬 기준", "https://notion.so/p3",
                   "북마크 목록은 행사 시작일 가까운 순으로 정렬한다.",
                   {"area": "북마크", "status": "제안", "last_edited": "t1"}),
]


@pytest.fixture
def setup():
    settings = Settings(admin_token="t")
    store = VectorStore(None, f"test_{uuid.uuid4().hex[:8]}")
    emb = FakeEmbedder()
    ingest_documents(DOCS, emb, store, settings)
    app.dependency_overrides[deps.get_store] = lambda: store
    app.dependency_overrides[deps.get_embedder] = lambda: emb
    app.dependency_overrides[deps.get_generator] = lambda: FakeGenerator()
    app.dependency_overrides[deps.get_settings] = lambda: Settings(_env_file=None, admin_token="t", rag_min_score=0.0)
    yield store, emb, settings
    app.dependency_overrides.clear()


def test_chunk_has_context_header():
    long_doc = PolicyDocument("x", "긴 정책", "", "\n".join(["가나다라마바사" * 20] * 10), {"area": "알림"})
    chunks = chunk_document(long_doc, size=300, overlap=50)
    assert len(chunks) > 1
    assert all(c.text.startswith("[정책] 긴 정책 | 영역: 알림") for c in chunks)
    assert chunks[0].chunk_id == "x:0"


def test_incremental_ingest_skips_unchanged(setup):
    store, emb, settings = setup
    r = ingest_documents(DOCS, emb, store, settings)
    assert (r.updated_pages, r.skipped_pages) == (0, 3)
    r = ingest_documents(DOCS[:2], emb, store, settings)  # p3 삭제됨
    assert r.removed_chunks == 1


def test_ingest_compares_content_not_last_edited(setup):
    """노션 last_edited는 분 단위라 같은 분 안의 수정이 같은 값으로 온다 → 내용으로 비교해야 놓치지 않는다."""
    from dataclasses import replace
    store, emb, settings = setup
    same_minute_edit = replace(DOCS[0], body="사진은 최대 10장.")  # last_edited는 그대로 t1
    r = ingest_documents([same_minute_edit, *DOCS[1:]], emb, store, settings)
    assert (r.updated_pages, r.skipped_pages) == (1, 2)
    assert "10장" in store.col.get(where={"page_id": "p1"})["documents"][0]

    touched_only = replace(same_minute_edit, metadata={**same_minute_edit.metadata, "last_edited": "t2"})
    r = ingest_documents([touched_only, *DOCS[1:]], emb, store, settings)  # 본문 블록만 고친 경우: 인덱스 내용 같음
    assert r.updated_pages == 0


def test_retriever_ranks_and_filters(setup):
    store, emb, _ = setup
    ret = Retriever(emb, store)
    assert ret.search("리뷰 작성 조건")[0].page_id == "p1"
    only_final = ret.search("리뷰", area="리뷰", status="최종결정")
    assert [h.page_id for h in only_final] == ["p1"]


def test_search_endpoint(setup):
    c = TestClient(app)
    r = c.get("/policies/search", params={"q": "북마크 정렬"})
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["title"] == "북마크 정렬 기준"
    assert body["items"][0]["status"] == "제안"


def test_search_validation(setup):
    assert TestClient(app).get("/policies/search", params={"q": "a"}).status_code == 422


def test_answer_endpoint(setup):
    r = TestClient(app).post("/policies/answers", json={"question": "리뷰 누가 쓸 수 있어?", "area": "리뷰"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"].startswith("최신 결론:")
    assert {s["page_id"] for s in body["sources"]} == {"p1", "p2"}  # 영역 필터로 북마크 제외
    assert body["usage"]["input_tokens"] == 1000
    assert body["usage"]["cost_usd"] == 0.006  # 1000*3/1M + 200*15/1M


def test_admin_requires_token(setup):
    assert TestClient(app).post("/admin/sync-jobs", json={}).status_code == 401


def test_health(setup):
    r = TestClient(app).get("/health")
    assert r.json() == {"status": "ok", "indexed_chunks": 3, "last_sync": None}


def test_min_score_drops_unrelated_results(setup):
    store, emb, _ = setup
    loose = Retriever(emb, store, min_score=0.0).search("오프라인 온보딩")
    strict = Retriever(emb, store, min_score=0.3).search("오프라인 온보딩")
    assert loose and all(h.score >= 0.3 for h in strict)
    assert len(strict) < len(loose)


def test_search_endpoint_applies_threshold(setup):
    app.dependency_overrides[deps.get_settings] = lambda: Settings(_env_file=None, rag_min_score=0.99)
    r = TestClient(app).get("/policies/search", params={"q": "오프라인 온보딩"})
    assert r.status_code == 200 and r.json()["count"] == 0


def test_answer_returns_502_when_claude_fails(setup):
    import anthropic
    import httpx

    class BrokenGenerator:
        def answer(self, question, hits, user=None, context=None):
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))

    app.dependency_overrides[deps.get_generator] = lambda: BrokenGenerator()
    r = TestClient(app).post("/policies/answers", json={"question": "리뷰 누가 쓸 수 있어?"})
    assert r.status_code == 502
    assert r.json()["detail"].startswith("Claude 호출 실패: APIConnectionError")


def test_policies_require_api_key_when_configured(setup):
    app.dependency_overrides[deps.get_settings] = lambda: Settings(_env_file=None, api_key="secret", rag_min_score=0.0)
    c = TestClient(app)
    assert c.get("/policies/search", params={"q": "북마크 정렬"}).status_code == 401
    assert c.get("/policies/search", params={"q": "북마크 정렬"}, headers={"X-API-Key": "secret"}).status_code == 200
    assert c.get("/health").status_code == 200  # 헬스체크는 인증 없이 (Docker healthcheck용)


# --- 재논의 짝 (S-2) ---
REOPEN_DOCS = [
    PolicyDocument("r1", "북마크 폴더 개수 제한", "https://notion.so/r1", "폴더는 최대 10개까지 만들 수 있다.",
                   {"area": "북마크", "status": "최종결정", "record_id": "aaa", "last_edited": "t1"}),
    PolicyDocument("r2", "폴더 개수 상한 완화", "https://notion.so/r2", "상한을 20개로 늘리자는 의견, 서버 부하 확인 중.",
                   {"area": "북마크", "status": "논의중", "record_id": "bbb", "reopens": "aaa", "last_edited": "t1"}),
]


@pytest.fixture
def reopen_store():
    store = VectorStore(None, f"test_{uuid.uuid4().hex[:8]}")
    emb = FakeEmbedder()
    ingest_documents(DOCS + REOPEN_DOCS, emb, store, Settings())
    return store, emb


def test_search_attaches_reopen_pair_both_ways(reopen_store):
    store, emb = reopen_store
    ret = Retriever(emb, store)
    # 이전 최종결정만 걸려도 재논의 기록이 바로 뒤에 붙는다
    hits = ret.search("폴더는 최대 10개까지", k=1)
    assert [h.page_id for h in hits] == ["r1", "r2"]
    # 재논의 기록만 걸려도 이전 최종결정이 붙는다 (상태 필터와 무관)
    hits = ret.search("상한을 20개로 늘리자 서버 부하", k=1, status="논의중")
    assert [h.page_id for h in hits] == ["r2", "r1"]


def test_answer_context_marks_reopen_relation(reopen_store):
    from app.rag.generator import build_context
    store, emb = reopen_store
    ctx = build_context(Retriever(emb, store).search("폴더는 최대 10개까지", k=1))
    assert 'note="재논의 대상 — [2]에서 변경 논의 중"' in ctx
    assert 'note="재논의 중 — [1]의 최종결정을 바꾸자는 논의. [1]은 아직 유효"' in ctx


def test_answer_context_without_pairs_has_no_note(setup):
    from app.rag.generator import build_context
    store, emb, _ = setup
    assert "note=" not in build_context(Retriever(emb, store).search("리뷰 작성 조건"))


def test_answer_uses_context_tail_for_search_only(setup):
    from app.api.routers.policies import SEARCH_CONTEXT_CHARS, search_query
    assert search_query("이거 맞아?", None) == "이거 맞아?"
    q = search_query("이거 맞아?", "오래된 잡담 " * 100 + "북마크 정렬 기준 얘기")
    assert q.startswith("이거 맞아?\n") and q.endswith("북마크 정렬 기준 얘기")
    assert len(q) <= len("이거 맞아?\n") + SEARCH_CONTEXT_CHARS
    # 질문만으로는 주제가 없어도 맥락(최근 메시지)으로 맞는 문서를 찾는다
    r = TestClient(app).post("/policies/answers", json={"question": "이거 정책이랑 맞아?", "context": "B: 북마크 정렬 어떻게 하지"})
    assert r.status_code == 200 and r.json()["sources"][0]["page_id"] == "p3"


def test_user_message_wraps_conversation_separately():
    from app.rag.generator import build_user_message
    assert "<conversation>" not in build_user_message("질문", [])
    msg = build_user_message("그럼 비회원은?", [], "[이전 질문] 리뷰 누가 써?")
    assert msg.index("<conversation>") < msg.index("질문: 그럼 비회원은?")
