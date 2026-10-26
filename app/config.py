"""RAG/API 설정. 봇과 같은 .env를 읽는다 (NOTION_TOKEN, NOTION_DATA_SOURCE_ID, ANTHROPIC_API_KEY, CLAUDE_MODEL 공유)."""
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Settings 외에 os.environ을 직접 읽는 곳(app/usage.py 단가 등)도 .env 값을 보도록.
# Docker에서는 env_file로 이미 환경변수가 들어오고, 이미 있는 값은 덮어쓰지 않는다.
load_dotenv()


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

    # --- 리랭킹 (기본 꺼짐: 평가로 효과를 확인한 뒤 켠다) ---
    rerank_enabled: bool = False
    rerank_model: str = "claude-haiku-5-5"
    rerank_candidates: int = 10      # 벡터 검색에서 리랭커로 넘길 후보 수
    rerank_min_relevance: int = 2    # 0~3점 중 이 점수 이상만 남김 (전부 미만이면 거절)
    rerank_min_score: float = 0.2    # 리랭킹 시 벡터 하한선 (후보를 넓게 받기 위해 낮게)

    # --- 청킹 ---
    chunk_size: int = 800
    chunk_overlap: int = 100

    # --- 답변 생성 (봇과 같은 키/모델 사용) ---
    anthropic_api_key: str = ""
    llm_model: str = Field("claude-sonnet-5-5", validation_alias=AliasChoices("LLM_MODEL", "CLAUDE_MODEL"))
    llm_max_tokens: int = 1500

    # --- API ---
    admin_token: str = "change-me"
    # 정책 조회 API(/policies/*) 인증 키. 비어 있으면 인증 없음(로컬 개발용).
    # 터널 등으로 외부에 공개할 때는 반드시 설정: 키 없이 호출되면 누구나 Claude 비용을 쓰게 된다
    api_key: str = ""

    # --- 사용량 제한 (app/limits.py) ---
    # 사용자별 하루 답변 횟수. LLM을 실제로 부른 답변만 센다 (근거가 없어 거절한 답은 무료라 제외). 0 = 제한 없음
    daily_answer_limit: int = 30
    # 월 예산(USD). 이번 달 전체 비용(요약·리랭크·답변·임베딩)이 기준. 0 = 예산 없음
    monthly_budget_usd: float = 0.0
    budget_alert_ratio: float = 0.8   # 이 비율을 넘으면 알림 (1회)
    budget_hard_stop: bool = True     # 100% 넘으면 답변 차단
    # 알림을 보낼 Discord 웹훅 URL (채널 설정 → 연동 → 웹후크). 비우면 로그만 남김
    budget_alert_webhook_url: str = ""

    def resolved_source(self) -> str:
        if self.rag_source != "auto":
            return self.rag_source
        return "notion" if (self.notion_token and self.notion_data_source_id) else "jsonl"


@lru_cache
def get_settings() -> Settings:
    return Settings()
