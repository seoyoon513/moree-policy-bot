"""설정 — 봇·API·스크립트가 모두 이 Settings 하나로 .env/환경변수를 읽는다 (ARCHITECTURE_REVIEW A-1).

- 환경변수를 os.environ으로 직접 읽지 않는다. 새 설정은 여기에 필드로 추가한다.
- 우선순위: 환경변수(Docker env_file 포함) > .env 파일 > 기본값.
- 읽을 .env 파일은 ENV_FILE로 바꿀 수 있다 (빈 값 = 파일을 읽지 않음, 테스트용).
- 봇 코드의 `from config import X`는 루트 config.py가 이 값을 같은 이름으로 다시 내보낸다.
"""
import os
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _csv(raw: str) -> list[str]:
    return [s.strip() for s in raw.replace("\n", ",").split(",") if s.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=os.environ.get("ENV_FILE", ".env") or None, extra="ignore",
                                      str_strip_whitespace=True)

    # --- 디스코드 봇 ---
    discord_token: str = ""
    # 지정하면 해당 서버에만 슬래시 커맨드를 즉시 등록 (개발용). 비우면 전역 등록(반영까지 시간 걸림)
    guild_id: str = ""
    # 봇 → API 서버 주소
    policy_api_url: str = "http://localhost:8000"
    # 스레드에서 읽어올 최대 메시지 수 / LLM에 넣을 최대 글자 수
    max_messages: int = 500
    max_transcript_chars: int = 100000
    # 요약 응답 최대 토큰. 결정이 많은 긴 스레드는 4096에서 잘려 JSON이 깨진 적이 있음
    summary_max_tokens: int = 8192
    # 승인 버튼 유효 시간(초)
    approval_timeout: int = 900

    # --- 문서 소스 ---
    # "auto": Notion이 설정돼 있으면 Notion, 아니면 data/history.jsonl
    # "notion" | "jsonl" 로 강제 가능
    rag_source: str = "auto"
    data_dir: str = "data"

    # --- Notion (봇의 notion_store.py 스키마와 동일) ---
    # NOTION_DATA_SOURCE_ID가 비어 있으면 봇은 로컬 JSON에만 저장
    notion_token: str = ""
    notion_data_source_id: str = ""
    notion_database_id: str = ""
    notion_parent_page_id: str = ""   # setup_notion.py · setup_spec_inbox.py에서만 사용
    notion_prop_title: str = "주제"
    notion_prop_area: str = "기능영역"
    notion_prop_status: str = "상태"
    notion_prop_decider: str = "결정자"
    notion_prop_decided_at: str = "결정일"
    notion_prop_scope: str = "적용범위"
    # 본문으로 쓸 속성들 (순서대로 "라벨: 값" 형태로 이어붙임)
    notion_body_props: list[str] = ["결정", "근거", "적용범위", "외부참조", "확인필요", "스레드명"]

    # --- 정책서 인덱싱 (EXPERIMENTS 4-1) ---
    # 봇의 NOTION_SPEC_PAGES와 같은 값: "북마크=<페이지 URL/ID>,리뷰=<...>" ("*"는 기본 정책서). 비우면 결정 DB만 인덱싱
    notion_spec_pages: str = ""
    spec_index_enabled: bool = True

    # --- 원천 정책서 반영 제안 (spec_reflect.py) ---
    # 반영 제안을 낼 상태(쉼표 구분). 논의중까지 올리면 PM에게 확정 안 된 제안이 쌓인다
    spec_reflect_statuses: str = "최종결정"
    # 반영 제안 댓글에서 멘션할 노션 사용자 ID (PM, 쉼표 구분). 비우면 멘션 없이 댓글만
    notion_pm_user_ids: str = ""
    # 맞는 정책서를 못 찾은 제안을 모으는 페이지. 비우면 디스코드에만 보고
    notion_spec_inbox_page: str = ""
    spec_match_model: str = ""        # 비우면 llm_model
    spec_match_max_tokens: int = 4096

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

    # --- 하이브리드 검색: 벡터 + BM25 키워드, RRF 결합 (기본 꺼짐: 평가로 효과를 확인한 뒤 켠다, 4-2) ---
    hybrid_enabled: bool = False

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

    # --- 로그·운영 (app/logging_setup.py) ---
    log_format: str = "text"          # text | json (서버에서는 json)
    log_level: str = "INFO"
    # 운영 알림(봇 시작 등)을 보낼 Discord 웹훅. 비우면 budget_alert_webhook_url, 그것도 비우면 로그만
    ops_webhook_url: str = ""

    def resolved_ops_webhook_url(self) -> str:
        return self.ops_webhook_url or self.budget_alert_webhook_url

    # --- 토큰 사용량 기록 (app/usage.py) ---
    usage_log_path: str = ""          # 비우면 <data_dir>/usage.jsonl
    usage_log_disabled: bool = False  # 테스트에서 실제 파일에 기록하지 않게
    # 단가: USD / 100만 토큰. 기본값은 작성 시점 공개 가격이라 모델·가격이 바뀌면 다시 확인
    price_llm_input_per_mtok: float = 3.0
    price_llm_output_per_mtok: float = 15.0
    # 리랭커 등에 쓰는 작은 모델(이름에 haiku 포함)
    price_small_llm_input_per_mtok: float = 1.0
    price_small_llm_output_per_mtok: float = 5.0
    price_embed_per_mtok: float = 0.02

    # --- 쉼표 구분 값 ---
    def spec_reflect_status_list(self) -> list[str]:
        return _csv(self.spec_reflect_statuses)

    def pm_user_id_list(self) -> list[str]:
        return _csv(self.notion_pm_user_ids)

    def spec_pages_by_area(self) -> dict[str, str]:
        """"북마크=<페이지 URL/ID>,리뷰=<...>" → {기능영역: 페이지 값} (봇의 반영 제안용, "*"는 기본 정책서)."""
        out = {}
        for part in _csv(self.notion_spec_pages):
            area, _, page = part.partition("=")
            if area.strip() and page.strip():
                out[area.strip()] = page.strip()
        return out

    def resolved_usage_log_path(self) -> str:
        return self.usage_log_path or os.path.join(self.data_dir, "usage.jsonl")

    # --- 시작 시 점검 ---
    def problems(self, role: str) -> list[str]:
        """role("bot" | "api")로 실행할 때 빠졌거나 위험한 설정. 비어 있으면 OK.
        실행 중에 처음 쓰는 순간 터지는 대신, 시작할 때 한 번에 알려 주려고 둔다."""
        out = []
        if role == "bot":
            if not self.discord_token:
                out.append("DISCORD_TOKEN이 비어 있어요.")
            if not self.anthropic_api_key:
                out.append("ANTHROPIC_API_KEY가 비어 있어요 (/정책정리 요약에 필요).")
        if bool(self.notion_token) != bool(self.notion_data_source_id):
            out.append("NOTION_TOKEN과 NOTION_DATA_SOURCE_ID는 둘 다 넣거나 둘 다 비워야 해요.")
        if role == "api" and self.embedding_backend == "openai" and not self.openai_api_key:
            out.append("EMBEDDING_BACKEND=openai인데 OPENAI_API_KEY가 비어 있어요.")
        if self.log_format not in ("text", "json"):
            out.append(f"LOG_FORMAT은 text|json 중 하나여야 해요 (지금: {self.log_format}).")
        if self.rag_source not in ("auto", "notion", "jsonl"):
            out.append(f"RAG_SOURCE는 auto|notion|jsonl 중 하나여야 해요 (지금: {self.rag_source}).")
        return out

    def warnings(self) -> list[str]:
        """실행은 되지만 확인이 필요한 설정."""
        out = []
        if self.admin_token == "change-me":
            out.append("ADMIN_TOKEN이 기본값(change-me)이에요. 외부에 열기 전에 바꿔 주세요.")
        return out

    def spec_pages(self) -> dict[str, list[str]]:
        """정책서 페이지 ID → 그 정책서를 쓰는 기능영역들 (검색 인덱스용, "*"는 기본 정책서라 영역에서 뺀다).
        같은 NOTION_SPEC_PAGES 값을 spec_pages_by_area()와 반대 방향으로 본 것."""
        from app.rag.spec_loader import notion_id
        out: dict[str, list[str]] = {}
        for area, page in self.spec_pages_by_area().items():
            areas = out.setdefault(notion_id(page), [])
            if area != "*":
                areas.append(area)
        return out

    def resolved_source(self) -> str:
        if self.rag_source != "auto":
            return self.rag_source
        return "notion" if (self.notion_token and self.notion_data_source_id) else "jsonl"


@lru_cache
def get_settings() -> Settings:
    return Settings()
