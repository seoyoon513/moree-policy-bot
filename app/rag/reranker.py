"""[R 보강] LLM 리랭커: 벡터 검색 후보를 작은 모델이 '질문에 실제로 답하는 문서인가'로 다시 채점.

왜 필요한가 (기준선 측정 결과, 2026-10-19)
- Hit@5는 100%인데 Top1은 83%: 정답은 찾지만 비슷한 결정들(점수 차 0.01~0.04) 사이에서 순서를 틀린다.
- 거절 정확도 12.5%: 문서에 없는 질문도 코사인 유사도 0.3~0.45의 '그럴듯한' 결과가 나온다.
  정답 문서 최저 점수(≈0.32)와 겹쳐서 하한선만으로는 분리가 안 된다.
임베딩은 질문과 문서를 '따로' 벡터로 만들어 비교하지만, 리랭커는 질문과 문서를 '같이' 읽고 판단한다
(cross-encoder 방식). 그래서 순위와 관련 없음 판단이 더 정확한 대신 호출 비용과 지연이 든다.

채점 기준 (0~3)
  3 = 질문에 직접 답하는 결정   2 = 답의 일부/조건을 담은 결정
  1 = 같은 기능이지만 질문과 다른 내용   0 = 무관
min_relevance(기본 2) 이상만 남긴다 → 전부 미만이면 빈 결과 = 거절.
"""
from __future__ import annotations

import json
import logging
import re

from anthropic import Anthropic

from app import usage as usage_log
from app.rag.models import SearchHit

log = logging.getLogger("policy-api")

PROMPT = """질문과 정책 결정 후보 목록이 주어진다. 각 후보가 질문에 답하는 데 얼마나 쓸모 있는지 0~3점으로 채점한다.
3: 질문에 직접 답한다 / 2: 답의 일부나 조건을 담고 있다 / 1: 같은 기능이지만 질문과 다른 내용이다 / 0: 무관하다
단어가 겹쳐도 질문이 묻는 대상(화면, 기능, 상황)이 다르면 1 이하다.
모든 후보를 빠짐없이 채점해서 submit_scores 도구로 제출한다."""

# 자유 텍스트로 JSON을 받으면 모델이 설명을 붙이거나 길게 써서 잘리는 일이 생긴다(측정: 38회 중 22회 실패).
# 도구 호출을 강제하면 응답이 스키마에 맞는 구조로만 나온다.
SCORE_TOOL = {
    "name": "submit_scores",
    "description": "후보별 관련도 점수를 제출한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "description": "[후보번호, 점수(0~3)] 쌍의 목록",
                "items": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
            }
        },
        "required": ["scores"],
    },
}


def _body(h: SearchHit, limit: int) -> str:
    body = h.text.split("\n", 1)[-1]  # 청크 헤더 줄 제외 (제목은 따로 줌)
    return body[:limit].replace("\n", " ")


class LLMReranker:
    def __init__(self, api_key: str, model: str, min_relevance: int = 2, snippet_chars: int = 200, client=None):
        self.client = client or Anthropic(api_key=api_key)
        self.model = model
        self.min_relevance = min_relevance
        self.snippet_chars = snippet_chars
        # 파싱 실패로 벡터 순서에 기댄 횟수. 폴백이 조용히 일어나면 '리랭킹이 효과 없음'으로 오판하게 된다
        self.calls = 0
        self.fallbacks = 0
        self.last_raw: str | None = None

    def rerank(self, query: str, hits: list[SearchHit], k: int) -> list[SearchHit]:
        if not hits:
            return []
        lines = [f"[{i}] {h.title} ({h.metadata.get('status', '')}, {h.metadata.get('decided_at', '')}) — {_body(h, self.snippet_chars)}"
                 for i, h in enumerate(hits, 1)]
        with usage_log.timer() as elapsed:
            msg = self.client.messages.create(
                model=self.model, max_tokens=512, system=PROMPT,
                tools=[SCORE_TOOL], tool_choice={"type": "tool", "name": "submit_scores"},
                messages=[{"role": "user", "content": f"질문: {query}\n\n후보:\n" + "\n".join(lines)}],
            )
        usage_log.from_anthropic("rerank", self.model, msg, elapsed())
        self.calls += 1
        tool_input = next((b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        if tool_input is not None:
            scores = self._from_pairs(tool_input.get("scores"), len(hits))
            text = text or json.dumps(tool_input, ensure_ascii=False)
        else:
            scores = self._parse(text, len(hits))
        if scores is None or getattr(msg, "stop_reason", None) == "max_tokens" and not scores:
            # 파싱 실패 시 검색을 막지 않고 벡터 순서로 대체하되, 원인 분석용으로 원문을 남긴다
            self.fallbacks += 1
            self.last_raw = text
            log.warning("리랭커 응답 파싱 실패 → 벡터 순서 사용: %r", text[:200])
            return hits[:k]
        ranked = sorted(
            ((scores.get(i, 0), h) for i, h in enumerate(hits, 1)),
            key=lambda x: (x[0], x[1].score), reverse=True,  # 동점이면 벡터 점수 순
        )
        out = []
        for s, h in ranked:
            if s < self.min_relevance:
                break
            h.metadata = {**h.metadata, "rerank": s}
            out.append(h)
        return out[:k]

    @staticmethod
    def _from_pairs(pairs, n: int) -> dict[int, int] | None:
        try:
            return {int(i): int(s) for i, s in pairs if 1 <= int(i) <= n}
        except (ValueError, TypeError):
            return None

    @classmethod
    def _parse(cls, text: str, n: int) -> dict[int, int] | None:
        m = re.search(r"\[\s*\[.*\]\s*\]|\[\s*\]", text, re.DOTALL)
        if not m:
            return None
        try:
            return cls._from_pairs(json.loads(m.group(0)), n)
        except ValueError:
            return None
