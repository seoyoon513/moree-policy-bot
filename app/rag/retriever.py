"""[R] Retrieval: 질문 → 관련 청크 검색. 메타데이터 필터(영역/상태)를 같이 걸 수 있다."""
from app.rag.embedder import Embedder
from app.rag.models import SearchHit
from app.rag.store import VectorStore


class Retriever:
    def __init__(self, embedder: Embedder, store: VectorStore, min_score: float = 0.0,
                 reranker=None, rerank_candidates: int = 15, hybrid: bool = False):
        self.embedder = embedder
        self.store = store
        # 하이브리드: 벡터 순위 + BM25 키워드 순위를 RRF로 합친다 (keyword.py). 거절은 그대로 코사인 하한선·리랭커가 한다
        self.hybrid = hybrid
        # 리랭커가 있으면 벡터 검색으로 후보를 넉넉히 뽑고(rerank_candidates), 최종 순위·거절은 리랭커가 정한다
        self.reranker = reranker
        self.rerank_candidates = rerank_candidates
        # 벡터 검색은 '가장 가까운 것'을 무조건 돌려준다. 관련 없는 결과를 막으려면 하한선이 필요
        self.min_score = min_score

    def search(self, query: str, k: int = 5, area: str | None = None, status: str | None = None,
               one_per_page: bool = True) -> list[SearchHit]:
        first_stage = self._hybrid_search if self.hybrid else self._vector_search
        if self.reranker:
            candidates = first_stage(query, self.rerank_candidates, area, status, one_per_page)
            hits = self.reranker.rerank(query, candidates, k)
        else:
            hits = first_stage(query, k, area, status, one_per_page)
        return self.with_reopen_pairs(hits)

    def with_reopen_pairs(self, hits: list[SearchHit]) -> list[SearchHit]:
        """재논의 짝을 같이 넣는다. 한쪽만 보이면 "확정됐다" 또는 "아직 논의 중"으로 반쪽 답이 나온다.
        - 재논의 기록이 걸리면 → 그것이 가리키는 이전 최종결정
        - 최종결정이 걸리면 → 그것을 다시 논의 중인 기록
        짝은 검색 점수와 무관하게 바로 뒤에 붙인다 (k를 넘을 수 있음). 필터(영역·상태)도 무시: 짝이 빠지면 오답이 된다."""
        seen = {h.page_id for h in hits}
        out: list[SearchHit] = []
        for h in hits:
            out.append(h)
            wheres = []
            if h.metadata.get("reopens"):
                wheres.append({"record_id": h.metadata["reopens"]})
            if h.metadata.get("record_id"):
                wheres.append({"reopens": h.metadata["record_id"]})
            for where in wheres:
                pair = self.store.get_first(where)
                if pair and pair.page_id not in seen:
                    seen.add(pair.page_id)
                    out.append(pair)
        return out

    @staticmethod
    def _where(area: str | None, status: str | None) -> dict | None:
        filters = [{key: val} for key, val in (("area", area), ("status", status)) if val]
        return filters[0] if len(filters) == 1 else ({"$and": filters} if filters else None)

    @staticmethod
    def _dedupe_pages(hits: list[SearchHit], k: int) -> list[SearchHit]:
        """순서를 지키며 페이지당 첫 청크만."""
        seen, out = set(), []
        for h in hits:
            if h.page_id not in seen:
                seen.add(h.page_id)
                out.append(h)
        return out[:k]

    def _hybrid_search(self, query: str, k: int, area: str | None, status: str | None,
                       one_per_page: bool) -> list[SearchHit]:
        vector = self.embedder.embed_query(query)
        n = max(k * 3, 30)  # 두 검색 모두 넉넉히 뽑아 합친 뒤 자른다
        vec_hits = self.store.query(vector, k=n, where=self._where(area, status))
        bm25, chunks = self.store.keyword_index()
        allowed = None
        if area or status:
            allowed = {cid for cid, (_, m) in chunks.items()
                       if (not area or m.get("area") == area) and (not status or m.get("status") == status)}
        kw_hits = bm25.search(query, n, allowed)

        from app.rag.keyword import rrf
        order = rrf([[h.metadata["chunk_id"] for h in vec_hits], [cid for cid, _ in kw_hits]])
        by_id = {h.metadata["chunk_id"]: h for h in vec_hits}
        for h in self.store.get_by_ids([cid for cid in order if cid not in by_id], vector):
            by_id[h.metadata["chunk_id"]] = h  # 키워드로만 찾은 청크도 코사인 점수를 붙여 하한선을 똑같이 적용
        ranked = [by_id[cid] for cid in order if cid in by_id and by_id[cid].score >= self.min_score]
        return self._dedupe_pages(ranked, k) if one_per_page else ranked[:k]

    def _vector_search(self, query: str, k: int, area: str | None, status: str | None,
                       one_per_page: bool) -> list[SearchHit]:
        where = self._where(area, status)

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
