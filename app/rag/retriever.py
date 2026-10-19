"""[R] Retrieval: 질문 → 관련 청크 검색. 메타데이터 필터(영역/상태)를 같이 걸 수 있다."""
from app.rag.embedder import Embedder
from app.rag.models import SearchHit
from app.rag.store import VectorStore


class Retriever:
    def __init__(self, embedder: Embedder, store: VectorStore, min_score: float = 0.0,
                 reranker=None, rerank_candidates: int = 15):
        self.embedder = embedder
        self.store = store
        # 리랭커가 있으면 벡터 검색으로 후보를 넉넉히 뽑고(rerank_candidates), 최종 순위·거절은 리랭커가 정한다
        self.reranker = reranker
        self.rerank_candidates = rerank_candidates
        # 벡터 검색은 '가장 가까운 것'을 무조건 돌려준다. 관련 없는 결과를 막으려면 하한선이 필요
        self.min_score = min_score

    def search(self, query: str, k: int = 5, area: str | None = None, status: str | None = None,
               one_per_page: bool = True) -> list[SearchHit]:
        if self.reranker:
            candidates = self._vector_search(query, self.rerank_candidates, area, status, one_per_page)
            return self.reranker.rerank(query, candidates, k)
        return self._vector_search(query, k, area, status, one_per_page)

    def _vector_search(self, query: str, k: int, area: str | None, status: str | None,
                       one_per_page: bool) -> list[SearchHit]:
        filters = [{key: val} for key, val in (("area", area), ("status", status)) if val]
        where = filters[0] if len(filters) == 1 else ({"$and": filters} if filters else None)

        # 페이지당 1개로 줄일 거라 넉넉히 가져온다
        raw = self.store.query(self.embedder.embed_query(query), k=k * 3 if one_per_page else k, where=where)
        raw = [h for h in raw if h.score >= self.min_score]
        if not one_per_page:
            return raw[:k]

        best: dict[str, SearchHit] = {}
        for h in raw:
            if h.page_id not in best or h.score > best[h.page_id].score:
                best[h.page_id] = h
        return sorted(best.values(), key=lambda h: h.score, reverse=True)[:k]
