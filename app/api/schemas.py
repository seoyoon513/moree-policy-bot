"""요청/응답 스키마. FastAPI가 이걸로 검증 + /docs(Swagger) 자동 생성."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    doc_type: str | None = None    # "정책서"면 노션 기능명세서의 한 행, 비면 결정 기록
    policy_id: str | None = None


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


# --- 정리(요약)·승인 (A-6: 봇 → API) ---
class ThreadMessageIn(BaseModel):
    index: int = Field(ge=1)
    author: str
    created_at: datetime
    content: str
    jump_url: str
    agreed_by: list[str] = []


class SummaryCreate(BaseModel):
    thread_id: str = Field(min_length=1, max_length=32)
    thread_name: str
    thread_url: str
    messages: list[ThreadMessageIn] = Field(min_length=1, max_length=2000)
    user_id: str | None = None   # 정리를 요청한 디스코드 사용자 ID (사용량 기록용)


class SummaryResult(BaseModel):
    """status: nothing_new | no_decisions | unchanged | ready. ready일 때만 summary_id·decisions가 있다."""
    status: Literal["nothing_new", "no_decisions", "unchanged", "ready"]
    message: str | None = None
    summary_id: str | None = None
    decisions: list[dict] = []
    previous: list[dict] = []
    unchanged: int = 0


class DecisionIn(BaseModel):
    """승인 화면에서 사람이 고친 결정. 노션 select 옵션에 없는 값은 저장 단계에서 400이 나므로 여기서 막는다.
    나머지 필드(relation·prior_id·edited_fields 등)는 그대로 통과시킨다."""
    model_config = ConfigDict(extra="allow")
    topic: str = Field(min_length=1)
    area: str
    status: str
    decision: str

    @field_validator("area")
    @classmethod
    def _area(cls, v):
        from app.domain import AREAS
        if v not in AREAS:
            raise ValueError(f"기능영역은 {AREAS} 중 하나여야 해요")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        from app.domain import STATUSES
        if v not in STATUSES:
            raise ValueError(f"상태는 {STATUSES} 중 하나여야 해요")
        return v


class ApprovalCreate(BaseModel):
    decisions: list[DecisionIn] = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    user_id: str | None = None   # 디스코드 사용자 ID (사용량 기록용)


class SavedDecision(BaseModel):
    id: str
    topic: str
    version: int
    updated: bool
    reopens: str | None = None
    notion_url: str | None = None
    notion_error: str | None = None
    notion_pending: bool = False   # 노션 저장 실패 → 원장에는 있고 장부(outbox)에 남아 다시 시도된다


class ApprovalResult(BaseModel):
    saved: list[SavedDecision]
    reflect_lines: list[str] = []
    replayed: bool = False   # True면 이미 승인된 건이라 다시 저장하지 않고 이전 결과를 돌려준 것
