"""봇 쪽 설정 이름표 + 도메인 상수.

환경변수 값은 직접 읽지 않고 app.config.Settings 하나에서 가져온다 (ARCHITECTURE_REVIEW A-1).
여기서는 기존 코드의 `from config import X`가 그대로 동작하도록 같은 이름으로 다시 내보내기만 한다.
새 설정은 app/config.py의 Settings에 필드로 추가한다.
"""
from app.config import get_settings
# 도메인 상수 (환경변수가 아님) — 정의는 app/domain.py
from app.domain import AGREE_EMOJIS, AREAS, KST, STATUSES  # noqa: F401

_s = get_settings()

DISCORD_TOKEN = _s.discord_token
ANTHROPIC_API_KEY = _s.anthropic_api_key
CLAUDE_MODEL = _s.llm_model   # CLAUDE_MODEL 또는 LLM_MODEL

GUILD_ID = _s.guild_id

# Notion — NOTION_DATA_SOURCE_ID가 비어 있으면 로컬 JSON에만 저장
NOTION_TOKEN = _s.notion_token
NOTION_PARENT_PAGE_ID = _s.notion_parent_page_id
NOTION_DATABASE_ID = _s.notion_database_id
NOTION_DATA_SOURCE_ID = _s.notion_data_source_id

DATA_DIR = _s.data_dir

# 원천 정책서(기능명세서) 반영 제안 — {기능영역: 정책서 페이지}. 비우면 반영 단계를 건너뛴다
NOTION_SPEC_PAGES = _s.spec_pages_by_area()
SPEC_REFLECT_STATUSES = _s.spec_reflect_status_list()
NOTION_PM_USER_IDS = _s.pm_user_id_list()
NOTION_SPEC_INBOX_PAGE = _s.notion_spec_inbox_page
SPEC_MATCH_MODEL = _s.spec_match_model or CLAUDE_MODEL
SPEC_MATCH_MAX_TOKENS = _s.spec_match_max_tokens

MAX_MESSAGES = _s.max_messages
MAX_TRANSCRIPT_CHARS = _s.max_transcript_chars
SUMMARY_MAX_TOKENS = _s.summary_max_tokens
APPROVAL_TIMEOUT = _s.approval_timeout
