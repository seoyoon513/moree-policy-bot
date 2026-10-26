"""인덱싱 파이프라인: Notion → 청킹 → 임베딩 → Chroma.

청크 내용이 바뀐 페이지만 다시 임베딩해서 비용을 아낀다.
처음에는 노션 last_edited로 비교했는데, 노션 시각은 **분 단위**라 같은 분 안의 수정(저장 직후 재정리 등)과
로더 로직 변경(새 메타데이터)을 놓쳤다. 그래서 '실제로 인덱스에 들어갈 내용'의 해시로 비교한다.
"""
import hashlib
import json
from dataclasses import dataclass

from app.config import Settings
from app.rag.chunker import chunk_document
from app.rag.embedder import Embedder
from app.rag.models import Chunk, PolicyDocument
from app.rag.store import VectorStore

# 해시에서 빼는 메타데이터: 인덱스 내용과 무관하게 바뀌는 값 (본문 블록만 고쳐도 last_edited는 바뀐다)
_VOLATILE = {"last_edited"}


@dataclass
class IngestResult:
    total_pages: int
    updated_pages: int
    skipped_pages: int
    chunks_written: int
    removed_chunks: int


def content_hash(chunks: list[Chunk]) -> str:
    payload = [(c.text, {k: v for k, v in sorted(c.metadata.items()) if k not in _VOLATILE}) for c in chunks]
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:16]


def _indexed_hashes(store: VectorStore) -> dict[str, str]:
    got = store.col.get(include=["metadatas"])
    return {m["page_id"]: m.get("content_hash", "") for m in got["metadatas"]}


def ingest_documents(docs: list[PolicyDocument], embedder: Embedder, store: VectorStore,
                     settings: Settings, force: bool = False) -> IngestResult:
    indexed = {} if force else _indexed_hashes(store)
    updated = skipped = written = 0
    for doc in docs:
        chunks = chunk_document(doc, settings.chunk_size, settings.chunk_overlap)  # 임베딩 전이라 비용 없음
        h = content_hash(chunks)
        if indexed.get(doc.page_id) == h:
            skipped += 1
            continue
        for c in chunks:
            c.metadata["content_hash"] = h
        vectors = embedder.embed_documents([c.text for c in chunks])
        store.replace_page(doc.page_id, chunks, vectors)
        updated += 1
        written += len(chunks)
    removed = store.delete_missing({d.page_id for d in docs})
    return IngestResult(len(docs), updated, skipped, written, removed)


def ingest_from_source(settings: Settings, embedder: Embedder, store: VectorStore, force: bool = False) -> IngestResult:
    """설정에 따라 Notion 또는 data/history.jsonl에서 읽어 인덱싱."""
    from app.rag.notion_loader import build_loader
    docs = build_loader(settings).load()
    return ingest_documents(docs, embedder, store, settings, force)
