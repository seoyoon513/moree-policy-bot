"""FastAPI 앱 진입점.

실행: uvicorn app.api.main:app --reload
문서: http://localhost:8000/docs
"""
from fastapi import Depends, FastAPI

from app.api.deps import get_store
from app.api.routers import policies, sync, usage
from app.rag.store import VectorStore

app = FastAPI(
    title="Policy Bot API",
    version="0.1.0",
    description="Discord 정책 봇용 정책 검색/질의응답 API",
)

app.include_router(policies.router)
app.include_router(sync.router)
app.include_router(usage.router)


@app.get("/health", tags=["ops"], summary="헬스체크 (Docker/Nginx에서 사용 예정)")
def health(store: VectorStore = Depends(get_store)):
    return {"status": "ok", "indexed_chunks": store.count()}
