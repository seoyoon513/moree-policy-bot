"""인덱싱 파이프라인: Notion → 청킹 → 임베딩 → Chroma.

청크 내용이 바뀐 페이지만 다시 임베딩해서 비용을 아낀다.
처음에는 노션 last_edited로 비교했는데, 노션 시각은 **분 단위**라 같은 분 안의 수정(저장 직후 재정리 등)과
로더 로직 변경(새 메타데이터)을 놓쳤다. 그래서 '실제로 인덱스에 들어갈 내용'의 해시로 비교한다.
"""
import hashlib
import json
import logging
from dataclasses import dataclass

from app.config import Settings
from app.rag.chunker import chunk_document
from app.rag.embedder import Embedder
from app.rag.models import Chunk, PolicyDocument
from app.rag.store import VectorStore

log = logging.getLogger("policy-api")

# 해시에서 빼는 메타데이터: 인덱스 내용과 무관하게 바뀌는 값 (본문 블록만 고쳐도 last_edited는 바뀐다)
_VOLATILE = {"last_edited"}


@dataclass
class IngestResult:
    total_pages: int
    updated_pages: int
    skipped_pages: int
    chunks_written: int
    removed_chunks: int
    spec_error: str | None = None   # 정책서를 못 읽었을 때 (결정 기록 인덱싱은 그대로 진행)


def content_hash(chunks: list[Chunk]) -> str:
    payload = [(c.text, {k: v for k, v in sorted(c.metadata.items()) if k not in _VOLATILE}) for c in chunks]
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:16]


def _indexed_hashes(store: VectorStore) -> dict[str, str]:
    got = store.col.get(include=["metadatas"])
    return {m["page_id"]: m.get("content_hash", "") for m in got["metadatas"]}


def ingest_documents(docs: list[PolicyDocument], embedder: Embedder, store: VectorStore,
                     settings: Settings, force: bool = False, keep_page_ids: set[str] = frozenset()) -> IngestResult:
    """keep_page_ids: 이번에 못 읽었지만 지우면 안 되는 문서 (정책서 로딩 실패 시 기존 정책서 청크)."""
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
    removed = store.delete_missing({d.page_id for d in docs} | set(keep_page_ids))
    return IngestResult(len(docs), updated, skipped, written, removed)


def ingest_from_source(settings: Settings, embedder: Embedder, store: VectorStore, force: bool = False) -> IngestResult:
    """설정에 따라 Notion 또는 data/history.jsonl의 결정 기록 + 노션 정책서(설정돼 있으면)를 인덱싱."""
    from app.rag.notion_loader import build_loader
    from app.rag.spec_index import SPEC_PREFIX, load_spec_documents
    docs = build_loader(settings).load()
    keep: set[str] = set()
    spec_error = None
    try:
        docs += load_spec_documents(settings)
    except Exception as e:  # noqa: BLE001 — 정책서 하나 때문에 방금 승인한 결정이 검색에 안 나오면 안 된다
        log.warning("정책서 로딩 실패, 기존 정책서 인덱스 유지: %s", e)
        spec_error = f"{type(e).__name__}: {e}"
        keep = {pid for pid in _indexed_hashes(store) if pid.startswith(SPEC_PREFIX)}
    result = ingest_documents(docs, embedder, store, settings, force, keep_page_ids=keep)
    result.spec_error = spec_error
    return result
