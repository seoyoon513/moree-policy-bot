"""하이브리드 검색(4-2) 테스트: BM25 토큰화·점수, RRF, 하한선·필터·캐시. 네트워크 없음, 데이터는 가짜."""
import uuid

from app.config import Settings
from app.rag.ingest import ingest_documents
from app.rag.keyword import BM25, rrf, tokenize
from app.rag.models import PolicyDocument
from app.rag.retriever import Retriever
from app.rag.store import VectorStore
from test_rag_api import DOCS, FakeEmbedder


def test_tokenize_keeps_paths_and_bigrams_hangul():
    toks = tokenize("제목명은 20자, API는 /api/items?onlyOpen=true 사용")
    assert "api/items?onlyopen=true" in toks and "20" in toks   # 앞뒤 구두점은 양쪽에서 똑같이 뗀다
    assert {"제목", "목명", "명은"} <= set(toks)   # 조사가 붙어도 "제목"이 남는다
    assert tokenize("제목명이")[:2] == ["제목", "목명"]


def test_bm25_prefers_rare_exact_terms():
    bm = BM25(["a", "b", "c"], ["목록 이름 규칙", "제목명 글자수 제한 20자", "목록 아이콘 선택"])
    assert bm.search("제목명 20자", k=3)[0][0] == "b"
    assert [cid for cid, _ in bm.search("목록", k=3, allowed={"a"})] == ["a"]
    assert bm.search("전혀 없는 단어", k=3) == []


def test_rrf_rewards_agreement():
    assert rrf([["x", "y", "z"], ["y", "w"]])[0] == "y"


def _store(docs):
    store = VectorStore(None, f"test_{uuid.uuid4().hex[:8]}")
    ingest_documents(docs, FakeEmbedder(), store, Settings())
    return store


def test_hybrid_keeps_vector_threshold_and_filters():
    store = _store(DOCS)
    emb = FakeEmbedder()
    # 아무 글자나 겹친다고 결과가 생기지 않는다: 키워드로만 걸린 청크도 코사인 하한선을 똑같이 통과해야 한다
    strict = Retriever(emb, store, min_score=0.99, hybrid=True).search("리뷰 사진 몇 장")
    assert strict == []
    only = Retriever(emb, store, hybrid=True).search("리뷰", area="북마크")
    assert {h.metadata["area"] for h in only} == {"북마크"}
    top = Retriever(emb, store, hybrid=True).search("리뷰 작성 조건")
    assert top[0].page_id == "p1" and len({h.page_id for h in top}) == len(top)


def test_keyword_index_rebuilds_after_ingest():
    store = _store(DOCS)
    bm, _ = store.keyword_index()
    assert store.keyword_index()[0] is bm   # 캐시
    ingest_documents(DOCS + [PolicyDocument("p9", "새 정책", "", "shareLink 필드", {"area": "북마크"})],
                     FakeEmbedder(), store, Settings())
    bm2, chunks = store.keyword_index()
    assert bm2 is not bm and "p9:0" in chunks
    assert bm2.search("sharelink", k=1)[0][0] == "p9:0"
