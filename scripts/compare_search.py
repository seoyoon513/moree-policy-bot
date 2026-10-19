"""키워드 검색 vs 임베딩 검색을 실제 데이터로 비교 (공부용).

    python -m scripts.compare_search "인터넷 없이 앱 켜면 어디로 가?"

검색 인덱스(Chroma)는 건드리지 않고, 저장된 결정을 직접 읽어 점수만 계산한다.
"""
import math
import re
import sys

from app.config import get_settings
from app.rag.embedder import build_embedder
from app.rag.notion_loader import build_loader


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w·]+", text))


def keyword_score(query: str, doc: str) -> float:
    q, d = tokens(query), tokens(doc)
    return len(q & d) / len(q | d) if q | d else 0.0


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def main():
    query = " ".join(sys.argv[1:]) or "인터넷 없이 앱 켜면 어디로 가?"
    s = get_settings()
    docs = build_loader(s).load()
    embedder = build_embedder(s)

    texts = [f"{d.title}\n{d.body}" for d in docs]
    q_vec = embedder.embed_query(query)
    d_vecs = embedder.embed_documents(texts)
    print(f"질문: {query}   (임베딩 {len(q_vec)}차원, 결정 {len(docs)}건)\n")

    rows = sorted(
        ((cosine(q_vec, v), keyword_score(query, t), d.title) for d, t, v in zip(docs, texts, d_vecs)),
        reverse=True,
    )
    print(f"{'임베딩':>6} {'키워드':>6}  결정")
    for emb, kw, title in rows:
        print(f"{emb:>6.3f} {kw:>6.3f}  {title}")
    print(f"\n현재 검색 하한선 RAG_MIN_SCORE = {s.rag_min_score}")


if __name__ == "__main__":
    main()
