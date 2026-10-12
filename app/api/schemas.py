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
    area: str | None = None
    status: str | None = None
    k: int = Field(default=6, ge=1, le=20)


class AnswerResponse(BaseModel):
    question: str
    answer: str
    sources: list[PolicyHit]


class SyncJobCreate(BaseModel):
    force: bool = False   # True면 last_edited 무시하고 전부 재임베딩


class SyncJob(BaseModel):
    id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: str
    finished_at: str | None = None
    result: dict | None = None
    error: str | None = None


class ErrorResponse(BaseModel):
    detail: str
