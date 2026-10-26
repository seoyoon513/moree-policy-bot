"""[R 보강] 키워드 검색(BM25) — 하이브리드 검색의 한쪽 (EXPERIMENTS 4-2).

벡터 검색은 "스크랩 = 북마크 저장" 같은 의미 유사에는 강하지만, 문서에 그대로 있는 정확한 문자열
(글자수 제한 숫자, API 경로, 필드명)을 놓친다. 실제로 정책서 행에 "글자수 제한 N자"가 있는데
벡터·리랭크 모두 그 행을 후보로 못 올렸다 (골든셋 c01). BM25는 겹치는 토큰을 직접 센다.

토큰화 — 한국어는 띄어쓰기 단위로 자르면 "제목은"·"제목이"가 다른 토큰이 되어 BM25가 약하다.
형태소 분석기(kiwi 등) 대신 먼저 **한글 글자 2-gram**을 쓴다 (의존성 없음, 조사가 붙어도 "폴더"·"더명"이 겹친다).
영문·숫자·경로는 통째로 한 토큰 ("/api/items", "sharelink", "20").
"""
from __future__ import annotations

import math
import re
from collections import Counter

_ASCII = re.compile(r"[a-z0-9_./{}?=-]+")
_HANGUL = re.compile(r"[가-힣]+")


def tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens = [t.strip("./-?=") or t for t in _ASCII.findall(text)]
    for run in _HANGUL.findall(text):
        tokens += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
    return [t for t in tokens if t]


class BM25:
    """작은 코퍼스(청크 수백 개)용 메모리 BM25. 문서가 바뀌면 새로 만든다 (VectorStore가 캐시·무효화)."""

    def __init__(self, ids: list[str], texts: list[str], k1: float = 1.5, b: float = 0.75):
        self.ids = ids
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(t)) for t in texts]
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        df = Counter(tok for tf in self.tfs for tok in tf)
        n = len(texts)
        self.idf = {tok: math.log(1 + (n - c + 0.5) / (c + 0.5)) for tok, c in df.items()}

    def search(self, query: str, k: int, allowed: set[str] | None = None) -> list[tuple[str, float]]:
        q = set(tokenize(query))
        scored = []
        for cid, tf, ln in zip(self.ids, self.tfs, self.lens):
            if allowed is not None and cid not in allowed:
                continue
            s = 0.0
            for tok in q:
                f = tf.get(tok)
                if f:
                    s += self.idf[tok] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * ln / self.avg))
            if s > 0:
                scored.append((cid, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]


def rrf(rankings: list[list[str]], k: int = 60) -> list[str]:
    """Reciprocal Rank Fusion: 점수 척도가 다른 두 검색(코사인 0~1, BM25 0~∞)을 순위만으로 합친다."""
    score: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, 1):
            score[cid] = score.get(cid, 0.0) + 1 / (k + rank)
    return sorted(score, key=score.get, reverse=True)
