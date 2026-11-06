"""정책 리소스 API.

RESTful 설계 메모
- GET  /policies/search : 조회는 GET + 쿼리 파라미터. 부작용 없음(안전·멱등) → 캐시 가능.
- POST /policies/answers: LLM 답변은 '새 결과물을 만드는' 행위이고 매번 결과가 달라질 수 있어
  (비멱등) GET이 아니라 POST. 질문이 길어질 수 있어 body로 받는 것도 이유.
"""
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.api.deps import get_generator, get_retriever, require_api_key
from app import limits
from app.api.schemas import AnswerRequest, AnswerResponse, PolicyHit, QuotaInfo, SearchResponse, UsageInfo
from app.config import Settings, get_settings
from app.llm import LLMError
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
        doc_type=h.metadata.get("doc_type"), policy_id=h.metadata.get("policy_id") or None,
    )


# 검색어에 붙일 맥락 길이. "이거 정책이랑 맞아?"처럼 질문만으로는 주제가 없을 때를 위한 것이라
# 길게 붙이면 오히려 질문의 핵심이 묻힌다 (맥락은 최근 것이 끝에 오도록 만든다)
def _upstream_error(e: Exception) -> HTTPException:
    """LLM 호출 실패 → HTTP 오류. 본문(detail)은 사용자에게 그대로 보여 줘도 되는 문구.
    - 예산 초과: 429 (요청은 맞지만 허용량 초과)
    - 잠시 뒤 나아질 수 있는 실패(혼잡·레이트리밋·타임아웃): 503 Service Unavailable
    - 설정·한도 문제(키·지출 한도·요청 형식): 502 Bad Gateway (상위 서비스가 거절)"""
    if isinstance(e, limits.LimitExceeded):
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e))
    assert isinstance(e, LLMError)
    code = status.HTTP_503_SERVICE_UNAVAILABLE if e.retryable else status.HTTP_502_BAD_GATEWAY
    return HTTPException(code, e.user_message)


SEARCH_CONTEXT_CHARS = 300


def search_query(question: str, context: str | None) -> str:
    return f"{question}\n{context[-SEARCH_CONTEXT_CHARS:]}" if context else question


@router.get("/search", response_model=SearchResponse, summary="정책 의미 검색")
def search_policies(
    q: str = Query(min_length=2, max_length=200, description="검색어 (자연어 가능)"),
    k: int = Query(5, ge=1, le=20),
    area: str | None = Query(None, description="기능영역 필터, 예: 리뷰"),
    status: str | None = Query(None, description="상태 필터, 예: 최종결정"),
    retriever: Retriever = Depends(get_retriever),
):
    try:
        hits = retriever.search(q, k=k, area=area, status=status)  # 리랭크가 켜져 있으면 LLM 호출
    except (LLMError, limits.LimitExceeded) as e:
        raise _upstream_error(e) from e
    return SearchResponse(query=q, count=len(hits), items=[_to_hit(h) for h in hits])


@router.post("/answers", response_model=AnswerResponse, summary="RAG 기반 정책 질의응답")
def answer_policy_question(
    body: AnswerRequest,
    retriever: Retriever = Depends(get_retriever),
    generator: AnswerGenerator = Depends(get_generator),
    s: Settings = Depends(get_settings),
    x_user_id: str | None = Header(default=None, description="호출한 사용자 ID (봇이 Discord 사용자 ID를 넣음)"),
):
    # 검색(리랭크)도 돈이 들므로 제한 확인을 가장 먼저 한다
    try:
        quota = limits.check_answer_allowed(x_user_id, s)
    except limits.LimitExceeded as e:
        # 429 Too Many Requests: 요청 자체는 올바르지만 허용량을 넘었다는 뜻
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e)) from e

    try:
        hits = retriever.search(search_query(body.question, body.context), k=body.k, area=body.area,
                                status=body.status)
        result = generator.answer(body.question, hits, user=x_user_id, context=body.context)
    except (LLMError, limits.LimitExceeded) as e:
        # 실패 로그·운영 알림은 app/llm.py가 이미 남겼다. 여기서는 HTTP 응답으로만 바꾼다
        raise _upstream_error(e) from e
    if result.usage:  # LLM을 실제로 불렀을 때만 횟수·비용이 늘어난다
        quota["used_today"] += 1
        quota["month_spend_usd"] = round(quota["month_spend_usd"] + result.usage.cost_usd, 4)
        limits.maybe_alert(s)
    return AnswerResponse(
        quota=QuotaInfo(**quota),
        question=body.question,
        answer=result.text,
        sources=[_to_hit(h, 150) for h in hits],
        usage=UsageInfo(
            model=result.usage.model, input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens, latency_ms=result.usage.latency_ms,
            cost_usd=result.usage.cost_usd,
        ) if result.usage else None,
    )
