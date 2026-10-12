"""CLI로 인덱싱 돌리기 (API 서버 없이).

    python -m scripts.ingest           # 바뀐 페이지만
    python -m scripts.ingest --force   # 전부 재임베딩
    python -m scripts.ingest --search "리뷰 작성 조건"   # 인덱싱 후 바로 검색 확인
"""
import argparse

from app.config import get_settings
from app.rag.embedder import build_embedder
from app.rag.ingest import ingest_from_source
from app.rag.retriever import Retriever
from app.rag.store import VectorStore


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--search", default=None)
    args = ap.parse_args()

    s = get_settings()
    embedder = build_embedder(s)
    store = VectorStore(s.chroma_path, s.chroma_collection)

    print(f"소스: {s.resolved_source()} | 임베딩: {s.embedding_backend}")
    r = ingest_from_source(s, embedder, store, force=args.force)
    print(f"페이지 {r.total_pages}개 | 갱신 {r.updated_pages} | 건너뜀 {r.skipped_pages} | "
          f"청크 {r.chunks_written}개 저장 | 삭제 {r.removed_chunks}개")

    if args.search:
        hits = Retriever(embedder, store, min_score=s.rag_min_score).search(args.search)
        if not hits:
            print(f"  (점수 {s.rag_min_score} 이상인 결과 없음)")
        for h in hits:
            print(f"  {h.score:.3f}  {h.title}  [{h.metadata.get('status', '-')}]  {h.url}")


if __name__ == "__main__":
    main()
