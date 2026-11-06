"""FastAPI 앱 진입점.

실행: uvicorn app.api.main:app --reload
문서: http://localhost:8000/docs
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.api.deps import get_store
from app.api.routers import policies, sync, usage
from app.config import get_settings
from app.rag.store import VectorStore

log = logging.getLogger("policy-api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 설정 문제를 시작 로그에 한 번에 남긴다. API는 일부 기능만 못 쓰는 경우가 많아 멈추지는 않는다
    s = get_settings()
    for p in s.problems("api"):
        log.error("설정 문제: %s", p)
    for w in s.warnings():
        log.warning("설정 확인: %s", w)
    yield


app = FastAPI(
    title="Policy Bot API",
    version="0.1.0",
    description="Discord 정책 봇용 정책 검색/질의응답 API",
    lifespan=lifespan,
)

app.include_router(policies.router)
app.include_router(sync.router)
app.include_router(usage.router)


@app.get("/health", tags=["ops"], summary="헬스체크 (Docker/Nginx에서 사용 예정)")
def health(store: VectorStore = Depends(get_store)):
    return {"status": "ok", "indexed_chunks": store.count()}
