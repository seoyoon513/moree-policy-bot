"""RAG 정확도 지표 계산 (순수 함수 → API 키 없이 테스트 가능).

검색(Retrieval) 지표
- Hit@k     : 정답 결정이 상위 k개 안에 하나라도 있는가 (가장 직관적인 '찾았나?')
- Recall@k  : 정답이 여러 개일 때 그중 몇 %를 찾았나
- MRR       : 정답이 몇 번째에 나왔나의 역수 평균 (1위=1.0, 2위=0.5 ...). 순위 품질
- 거절 정확도: 문서에 없는 질문에 '결과 0건'을 돌려줬나 (엉뚱한 근거로 답하는 걸 막는 능력)

답변(Generation) 지표
- 핵심어 포함률: must_include 단어가 답변에 다 들어 있나 (정답성의 값싼 근사)
- 인용률     : [1] 같은 근거 표시가 있나 (근거 기반 답변 형식 준수)
- 거절 정확도 : 없는 질문에 "확인되지 않음/찾지 못했습니다"라고 답했나
- (선택) LLM 채점: 근거 충실도 · 질문 응답성 1~5점

문항 유형(type) — 유형별로 따로 집계해 "어떤 질문에 약한지"를 본다 (EXPERIMENTS 0-1)
- basic: 결정 DB에 있는 일반 질문 (기본값) / none: 문서에 없는 질문 (expected_titles가 비면 기본값)
- exact: API 경로·필드명·버전 같은 정확 문자열 (하이브리드 검색 4-2의 대상)
- spec: 결정 DB에는 없고 정책서에만 있는 내용 (정책서 인덱싱 4-1의 대상)
- trap: 제안·논의중이거나 채택되지 않은 안. 답변이 확정처럼 말하면 오답 (must_include에 "제안" 등)
- history: "처음엔/예전엔" 같은 변경 경위 질문
- reopen: 재논의 중인 결정 / conflict: 노션과 디스코드가 다른 결정 (데이터가 생기면 추가)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

REFUSAL_MARKERS = ("확인되지 않음", "찾지 못했습니다", "찾지 못했어요", "관련 정책 문서를 찾지")
CITATION_RE = re.compile(r"\[\d+\]")


@dataclass
class GoldenItem:
    id: str
    question: str
    expected_titles: list[str] = field(default_factory=list)
    must_include: list[str] = field(default_factory=list)
    area: str | None = None
    type: str = ""

    def __post_init__(self):
        if not self.type:
            self.type = "none" if self.is_negative else "basic"

    @property
    def is_negative(self) -> bool:
        return not self.expected_titles


def load_golden(path: str | Path) -> list[GoldenItem]:
    items = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            d = json.loads(line)
            items.append(GoldenItem(d["id"], d["question"], d.get("expected_titles", []),
                                    d.get("must_include", []), d.get("area"), d.get("type", "")))
    return items


def _is_relevant(title: str, item: GoldenItem) -> bool:
    return any(exp in title for exp in item.expected_titles)


def retrieval_scores(item: GoldenItem, titles: list[str]) -> dict:
    """titles: 검색 결과 제목 (점수 높은 순, 하한선 적용 후)."""
    if item.is_negative:
        return {"negative": True, "rejected": len(titles) == 0, "n_results": len(titles)}
    first_rank = next((i for i, t in enumerate(titles, 1) if _is_relevant(t, item)), None)
    found = {e for e in item.expected_titles if any(e in t for t in titles)}
    return {
        "negative": False,
        "hit": first_rank is not None,
        "top1": first_rank == 1,
        "rr": 1 / first_rank if first_rank else 0.0,
        "recall": len(found) / len(item.expected_titles),
        "n_results": len(titles),
    }


def aggregate_retrieval(rows: list[dict]) -> dict:
    pos = [r for r in rows if not r["negative"]]
    neg = [r for r in rows if r["negative"]]
    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    correct = sum(r["hit"] for r in pos) + sum(r["rejected"] for r in neg)
    return {
        "n": len(rows),
        "hit_at_k": avg([float(r["hit"]) for r in pos]),
        "top1": avg([float(r["top1"]) for r in pos]),
        "mrr": avg([r["rr"] for r in pos]),
        "recall_at_k": avg([r["recall"] for r in pos]),
        "reject_acc": avg([float(r["rejected"]) for r in neg]),
        # 정답 있는 질문은 찾고, 없는 질문은 거절해야 '맞음'
        "overall_acc": round(correct / len(rows), 3) if rows else None,
        "avg_results": avg([r["n_results"] for r in rows]),
    }


def answer_scores(item: GoldenItem, answer: str) -> dict:
    refused = any(m in answer for m in REFUSAL_MARKERS)
    if item.is_negative:
        return {"negative": True, "refused": refused}
    missing = [w for w in item.must_include if w not in answer]
    return {
        "negative": False,
        "keywords_ok": not missing,
        "missing": missing,
        "cited": bool(CITATION_RE.search(answer)),
        "wrongly_refused": refused and not CITATION_RE.search(answer),
    }


def aggregate_answers(rows: list[dict]) -> dict:
    pos = [r for r in rows if not r["negative"]]
    neg = [r for r in rows if r["negative"]]
    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    out = {
        "n": len(rows),
        "keyword_acc": avg([float(r["keywords_ok"]) for r in pos]),
        "citation_rate": avg([float(r["cited"]) for r in pos]),
        "wrong_refusal_rate": avg([float(r["wrongly_refused"]) for r in pos]),
        "reject_acc": avg([float(r["refused"]) for r in neg]),
    }
    judged = [r for r in pos if "faithfulness" in r]
    if judged:
        out["judge_faithfulness"] = avg([r["faithfulness"] for r in judged])
        out["judge_relevance"] = avg([r["relevance"] for r in judged])
    return out


def by_type(items: list[GoldenItem], rows: list[dict], aggregate) -> dict[str, dict]:
    """rows는 items와 같은 순서. 유형별로 aggregate(rows)를 따로 계산한다."""
    groups: dict[str, list[dict]] = {}
    for it, r in zip(items, rows):
        groups.setdefault(it.type, []).append(r)
    return {t: aggregate(rs) for t, rs in sorted(groups.items())}
