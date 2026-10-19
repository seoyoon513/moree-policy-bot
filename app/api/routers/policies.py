"""정책 리소스 API.

RESTful 설계 메모
- GET  /policies/search : 조회는 GET + 쿼리 파라미터. 부작용 없음(안전·멱등) → 캐시 가능.
- POST /policies/answers: LLM 답변은 '새 결과물을 만드는' 행위이고 매번 결과가 달라질 수 있어
  (비멱등) GET이 아니라 POST. 질문이 길어질 수 있어 body로 받는 것도 이유.
"""
import logging

import anthropic
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_generator, get_retriever, require_api_key
from app.api.schemas import AnswerRequest, AnswerResponse, PolicyHit, SearchResponse, UsageInfo
from app.rag.generator import AnswerGenerator
from app.rag.models import SearchHit
from app.rag.retriever import Retriever

log = logging.getLogger("policy-api")
router = APIRouter(prefix="/policies", tags=["policies"], dependencies=[Depends(require_api_key)])


def _to_hit(h: SearchHit, snippet_len: int = 300) -> PolicyHit:
    body = h.text.split("\n", 1)[-1]  # 헤더 줄 제외
    return PolicyHit(
        page_id=h.page_id, title=h.title, url=h.url, score=h.score,
        snippet=body[:snippet_len] + ("…" if len(body) > snippet_len else ""),
        area=h.metadata.get("area"), status=h.metadata.get("status"),
        decided_at=h.metadata.get("decided_at"), decider=h.metadata.get("decider"),
    )


@router.get("/search", response_model=SearchResponse, summary="정책 의미 검색")
def search_policies(
    q: str = Query(min_length=2, max_length=200, description="검색어 (자연어 가능)"),
    k: int = Query(5, ge=1, le=20),
    area: str | None = Query(None, description="기능영역 필터, 예: 리뷰"),
    status: str | None = Query(None, description="상태 필터, 예: 최종결정"),
    retriever: Retriever = Depends(get_retriever),
):
    hits = retriever.search(q, k=k, area=area, status=status)
    return SearchResponse(query=q, count=len(hits), items=[_to_hit(h) for h in hits])


@router.post("/answers", response_model=AnswerResponse, summary="RAG 기반 정책 질의응답")
def answer_policy_question(
    body: AnswerRequest,
    retriever: Retriever = Depends(get_retriever),
    generator: AnswerGenerator = Depends(get_generator),
):
    hits = retriever.search(body.question, k=body.k, area=body.area, status=body.status)
    try:
        result = generator.answer(body.question, hits)
    except anthropic.APIError as e:
        # 우리 서버가 아니라 상위(upstream) 서비스가 실패한 것 → 502 Bad Gateway
        log.exception("Claude 호출 실패")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Claude 호출 실패: {type(e).__name__}: {e}") from e
    return AnswerResponse(
        question=body.question,
        answer=result.text,
        sources=[_to_hit(h, 150) for h in hits],
        usage=UsageInfo(
            model=result.usage.model, input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens, latency_ms=result.usage.latency_ms,
            cost_usd=result.usage.cost_usd,
        ) if result.usage else None,
    )
