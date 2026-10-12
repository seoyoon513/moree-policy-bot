"""RAG/API 설정. 봇과 같은 .env를 읽는다 (NOTION_TOKEN, NOTION_DATA_SOURCE_ID, ANTHROPIC_API_KEY, CLAUDE_MODEL 공유)."""
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- 문서 소스 ---
    # "auto": Notion이 설정돼 있으면 Notion, 아니면 data/history.jsonl
    # "notion" | "jsonl" 로 강제 가능
    rag_source: str = "auto"
    data_dir: str = "data"

    # --- Notion (봇의 notion_store.py 스키마와 동일) ---
    notion_token: str = ""
    notion_data_source_id: str = ""
    notion_prop_title: str = "주제"
    notion_prop_area: str = "기능영역"
    notion_prop_status: str = "상태"
    notion_prop_decider: str = "결정자"
    notion_prop_decided_at: str = "승인일"
    notion_prop_scope: str = "적용범위"
    # 본문으로 쓸 속성들 (순서대로 "라벨: 값" 형태로 이어붙임)
    notion_body_props: list[str] = ["결정", "근거", "적용범위", "외부참조", "확인필요", "스레드명"]

    # --- 임베딩 ---
    # "openai" (가볍고 한국어 품질 좋음) | "local" (무료, sentence-transformers + torch 설치 필요)
    embedding_backend: str = "openai"
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"
    local_embedding_model: str = "intfloat/multilingual-e5-small"

    # --- 벡터 DB (data/ 는 .gitignore 대상) ---
    chroma_path: str = "data/chroma"
    chroma_collection: str = "policies"

    # --- 검색 ---
    # 이 점수(cosine 유사도) 미만인 결과는 버린다. 결과가 너무 적으면 낮추고, 엉뚱하면 높이기
    rag_min_score: float = 0.3

    # --- 청킹 ---
    chunk_size: int = 800
    chunk_overlap: int = 100

    # --- 답변 생성 (봇과 같은 키/모델 사용) ---
    anthropic_api_key: str = ""
    llm_model: str = Field("claude-sonnet-5-5", validation_alias=AliasChoices("LLM_MODEL", "CLAUDE_MODEL"))
    llm_max_tokens: int = 1500

    # --- API ---
    admin_token: str = "change-me"

    def resolved_source(self) -> str:
        if self.rag_source != "auto":
            return self.rag_source
        return "notion" if (self.notion_token and self.notion_data_source_id) else "jsonl"


@lru_cache
def get_settings() -> Settings:
    return Settings()
