"""환경 변수와 상수."""
import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")

# 지정하면 해당 서버에만 슬래시 커맨드를 즉시 등록 (개발용). 비우면 전역 등록(반영까지 시간 걸림).
GUILD_ID = os.environ.get("GUILD_ID", "").strip()

# Notion — NOTION_DATA_SOURCE_ID가 비어 있으면 로컬 JSON에만 저장
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "").strip()
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()  # setup_notion.py에서만 사용
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "").strip()
NOTION_DATA_SOURCE_ID = os.environ.get("NOTION_DATA_SOURCE_ID", "").strip()

DATA_DIR = os.environ.get("DATA_DIR", "data")

KST = ZoneInfo("Asia/Seoul")

# 스레드에서 읽어올 최대 메시지 수 / LLM에 넣을 최대 글자 수
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES", "500"))
MAX_TRANSCRIPT_CHARS = int(os.environ.get("MAX_TRANSCRIPT_CHARS", "100000"))

# 승인 버튼 유효 시간(초)
APPROVAL_TIMEOUT = int(os.environ.get("APPROVAL_TIMEOUT", "900"))

# 기능 영역(폴더). 문서 구조가 바뀌면 여기만 수정. (Notion select 옵션이라 쉼표 금지)
AREAS = [
    "회원가입·온보딩",
    "지도·탐색",
    "행사 상세",
    "행사 등록",
    "리뷰",
    "알림",
    "도감·발도장",
    "북마크",
    "기타",
]

STATUSES = ["제안", "논의중", "최종결정"]
