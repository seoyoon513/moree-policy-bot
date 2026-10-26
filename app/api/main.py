"""FastAPI 앱 진입점.

실행: uvicorn app.api.main:app --reload
문서: http://localhost:8000/docs
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.api.deps import get_embedder, get_store
from app.api.routers import policies, sync, usage
from app.config import Settings, get_settings
from app.rag.store import VectorStore


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    task = None
    if s.sync_interval_minutes > 0:
        task = asyncio.create_task(sync.run_periodically(s, get_embedder(), get_store()))
    yield
    if task:
        task.cancel()


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
def health(store: VectorStore = Depends(get_store), s: Settings = Depends(get_settings)):
    return {"status": "ok", "indexed_chunks": store.count(), "last_sync": sync.last_success(s)}
