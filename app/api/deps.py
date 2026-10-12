"""의존성 주입(DI). 라우터는 '무엇이 필요한지'만 선언하고, 생성은 여기서 한 번만.
테스트에서는 app.dependency_overrides로 가짜 객체를 끼워 넣는다.
"""
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings
from app.rag.embedder import Embedder, build_embedder
from app.rag.generator import AnswerGenerator
from app.rag.retriever import Retriever
from app.rag.store import VectorStore


@lru_cache
def get_store() -> VectorStore:
    s = get_settings()
    return VectorStore(s.chroma_path, s.chroma_collection)


@lru_cache
def get_embedder() -> Embedder:
    return build_embedder(get_settings())


def get_retriever(
    embedder: Embedder = Depends(get_embedder),
    store: VectorStore = Depends(get_store),
    s: Settings = Depends(get_settings),
) -> Retriever:
    return Retriever(embedder, store, min_score=s.rag_min_score)


def get_generator(s: Settings = Depends(get_settings)) -> AnswerGenerator:
    if not s.anthropic_api_key:
        # 기능이 설정되지 않은 건 클라이언트 잘못이 아니므로 503
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "답변 생성이 설정되지 않았습니다 (ANTHROPIC_API_KEY 없음)")
    return AnswerGenerator(s.anthropic_api_key, s.llm_model, s.llm_max_tokens)


def require_admin(x_admin_token: str | None = Header(default=None), s: Settings = Depends(get_settings)) -> None:
    if x_admin_token != s.admin_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "관리자 토큰이 올바르지 않습니다")
