"""인덱싱 파이프라인: Notion → 청킹 → 임베딩 → Chroma.

last_edited 시간이 바뀐 페이지만 다시 임베딩해서 비용을 아낀다.
"""
from dataclasses import dataclass

from app.config import Settings
from app.rag.chunker import chunk_document
from app.rag.embedder import Embedder
from app.rag.models import PolicyDocument
from app.rag.store import VectorStore


@dataclass
class IngestResult:
    total_pages: int
    updated_pages: int
    skipped_pages: int
    chunks_written: int
    removed_chunks: int


def _indexed_versions(store: VectorStore) -> dict[str, str]:
    got = store.col.get(include=["metadatas"])
    return {m["page_id"]: m.get("last_edited", "") for m in got["metadatas"]}


def ingest_documents(docs: list[PolicyDocument], embedder: Embedder, store: VectorStore,
                     settings: Settings, force: bool = False) -> IngestResult:
    indexed = {} if force else _indexed_versions(store)
    updated = skipped = written = 0
    for doc in docs:
        if not force and indexed.get(doc.page_id) == doc.metadata.get("last_edited") and doc.metadata.get("last_edited"):
            skipped += 1
            continue
        chunks = chunk_document(doc, settings.chunk_size, settings.chunk_overlap)
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
