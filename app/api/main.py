"""FastAPI 앱 진입점.

실행: uvicorn app.api.main:app --reload
문서: http://localhost:8000/docs
"""
import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request

from app.api.deps import get_store
from app.api.routers import policies, sync, usage
from app import ops
from app.config import Settings, get_settings
from app.logging_setup import new_request_id, set_request_id, setup_logging
from app.rag.store import VectorStore

log = logging.getLogger("policy-api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # uvicorn이 자기 로거를 설정한 뒤라 여기서 단다. 전에는 핸들러가 없어 policy-api의 INFO 로그가 버려졌다
    setup_logging("api")
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

_RID_OK = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@app.middleware("http")
async def request_context(request: Request, call_next):
    """요청마다 request_id를 정하고(봇이 보낸 X-Request-ID가 있으면 그대로) 요청 한 줄 로그를 남긴다.
    헤더 값은 외부 입력이라 형식이 이상하면 쓰지 않고 새로 만든다 (로그 위조 방지)."""
    rid = request.headers.get("x-request-id", "")
    if _RID_OK.match(rid):
        set_request_id(rid)
    else:
        rid = new_request_id("a-")
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("요청 처리 중 예외 %s %s", request.method, request.url.path)
        raise
    ms = int((time.perf_counter() - start) * 1000)
    if request.url.path != "/health":  # 30초마다 오는 헬스체크는 남기지 않는다
        log.info("%s %s → %s (%dms)", request.method, request.url.path, response.status_code, ms,
                 extra={"status": response.status_code, "latency_ms": ms})
    response.headers["X-Request-ID"] = rid
    return response


app.include_router(policies.router)
app.include_router(sync.router)
app.include_router(usage.router)


@app.get("/health", tags=["ops"], summary="헬스체크: 응답하면 200, 내용(status)은 ok | degraded")
def health(store: VectorStore = Depends(get_store), s: Settings = Depends(get_settings)):
    return ops.health_report(s, store.count())
