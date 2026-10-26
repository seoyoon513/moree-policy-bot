"""[R 단계 준비 4] 벡터DB(Chroma) 래퍼. 로컬 파일로 저장되므로 별도 서버 불필요."""
import chromadb

from app.rag.models import Chunk, SearchHit


class VectorStore:
    def __init__(self, path: str | None, collection: str):
        client = chromadb.PersistentClient(path=path) if path else chromadb.EphemeralClient()
        # cosine 거리 사용 → score = 1 - distance
        self.col = client.get_or_create_collection(collection, metadata={"hnsw:space": "cosine"})
        self._keyword = None  # BM25 인덱스 캐시. 청크가 바뀌면 지운다

    def keyword_index(self):
        """하이브리드 검색용 BM25 + 청크 메타데이터. 처음 부를 때 만들고, 청크가 바뀌면 다시 만든다."""
        if self._keyword is None:
            from app.rag.keyword import BM25
            got = self.col.get(include=["documents", "metadatas"])
            self._keyword = (BM25(got["ids"], got["documents"]),
                             {cid: (doc, meta) for cid, doc, meta in zip(got["ids"], got["documents"], got["metadatas"])})
        return self._keyword

    def get_by_ids(self, ids: list[str], vector: list[float]) -> list[SearchHit]:
        """지정한 청크들을 질문 벡터와의 코사인 점수와 함께 (키워드 검색으로만 찾은 청크에도 하한선을 적용하려고)."""
        if not ids:
            return []
        got = self.col.get(ids=ids, include=["documents", "metadatas", "embeddings"])
        qn = sum(x * x for x in vector) ** 0.5 or 1.0
        hits = []
        for cid, text, meta, emb in zip(got["ids"], got["documents"], got["metadatas"], got["embeddings"]):
            en = sum(x * x for x in emb) ** 0.5 or 1.0
            score = sum(a * b for a, b in zip(vector, emb)) / (qn * en)
            hits.append(SearchHit(page_id=meta["page_id"], title=meta.get("title", ""), url=meta.get("url", ""),
                                  text=text, score=round(score, 4), metadata={**meta, "chunk_id": cid}))
        return hits

    def replace_page(self, page_id: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """페이지가 수정되면 청크 개수가 달라질 수 있으니, 기존 청크를 지우고 다시 넣는다."""
        self.col.delete(where={"page_id": page_id})
        self._keyword = None
        if not chunks:
            return
        self.col.add(
            ids=[c.chunk_id for c in chunks],
            documents=[c.text for c in chunks],
            embeddings=vectors,
            metadatas=[c.metadata for c in chunks],
        )

    def delete_missing(self, alive_page_ids: set[str]) -> int:
        """Notion에서 삭제된 페이지의 청크 정리."""
        got = self.col.get(include=["metadatas"])
        stale = [cid for cid, m in zip(got["ids"], got["metadatas"]) if m.get("page_id") not in alive_page_ids]
        if stale:
            self.col.delete(ids=stale)
            self._keyword = None
        return len(stale)

    def query(self, vector: list[float], k: int, where: dict | None = None) -> list[SearchHit]:
        if self.col.count() == 0:
            return []
        res = self.col.query(
            query_embeddings=[vector],
            n_results=min(k, self.col.count()),
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
        hits: list[SearchHit] = []
        for cid, text, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
            hits.append(SearchHit(
                page_id=meta["page_id"], title=meta.get("title", ""), url=meta.get("url", ""),
                text=text, score=round(1 - dist, 4), metadata={**meta, "chunk_id": cid},
            ))
        return hits

    def get_first(self, where: dict) -> SearchHit | None:
        """조건에 맞는 페이지의 첫 청크 (재논의 짝처럼 검색 점수와 무관하게 같이 보여줄 문서용, score=0)."""
        got = self.col.get(where=where, include=["documents", "metadatas"])
        if not got["ids"]:
            return None
        i = min(range(len(got["ids"])), key=lambda j: got["ids"][j])  # chunk_id "{page}:0"이 앞
        meta = got["metadatas"][i]
        return SearchHit(page_id=meta["page_id"], title=meta.get("title", ""), url=meta.get("url", ""),
                         text=got["documents"][i], score=0.0, metadata=meta)

    def count(self) -> int:
        return self.col.count()
