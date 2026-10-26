"""요청/응답 스키마. FastAPI가 이걸로 검증 + /docs(Swagger) 자동 생성."""
from typing import Literal

from pydantic import BaseModel, Field


class PolicyHit(BaseModel):
    page_id: str
    title: str
    url: str
    snippet: str
    score: float
    area: str | None = None
    status: str | None = None
    decided_at: str | None = None
    decider: str | None = None


class SearchResponse(BaseModel):
    query: str
    count: int
    items: list[PolicyHit]


class AnswerRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    # 멘션 질문의 대화 맥락 (스레드 최근 메시지 · 답장한 이전 Q/A). 지시어 해석용이고 근거로 쓰지 않는다
    context: str | None = Field(default=None, max_length=6000)
    area: str | None = None
    status: str | None = None
    k: int = Field(default=6, ge=1, le=20)


class UsageInfo(BaseModel):
    """이번 답변 생성(LLM 호출) 1회의 토큰/비용. 검색 결과가 없으면 LLM을 부르지 않아 null."""
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float


class QuotaInfo(BaseModel):
    """사용량 현황. 제한이 꺼져 있으면 해당 값은 null."""
    used_today: int
    daily_limit: int | None = None
    month_spend_usd: float
    monthly_budget_usd: float | None = None


class AnswerResponse(BaseModel):
    question: str
    answer: str
    sources: list[PolicyHit]
    usage: UsageInfo | None = None
    quota: QuotaInfo | None = None


class SyncJobCreate(BaseModel):
    force: bool = False   # True면 내용 해시 비교 없이 전부 재임베딩


class SyncJob(BaseModel):
    id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: str
    finished_at: str | None = None
    result: dict | None = None
    error: str | None = None


class ErrorResponse(BaseModel):
    detail: str
