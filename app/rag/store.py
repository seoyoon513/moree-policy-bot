"""[R 단계 준비 4] 벡터DB(Chroma) 래퍼. 로컬 파일로 저장되므로 별도 서버 불필요."""
import chromadb

from app.rag.models import Chunk, SearchHit


class VectorStore:
    def __init__(self, path: str | None, collection: str):
        client = chromadb.PersistentClient(path=path) if path else chromadb.EphemeralClient()
        # cosine 거리 사용 → score = 1 - distance
        self.col = client.get_or_create_collection(collection, metadata={"hnsw:space": "cosine"})

    def replace_page(self, page_id: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """페이지가 수정되면 청크 개수가 달라질 수 있으니, 기존 청크를 지우고 다시 넣는다."""
        self.col.delete(where={"page_id": page_id})
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
        for text, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
            hits.append(SearchHit(
                page_id=meta["page_id"], title=meta.get("title", ""), url=meta.get("url", ""),
                text=text, score=round(1 - dist, 4), metadata=meta,
            ))
        return hits

    def count(self) -> int:
        return self.col.count()
