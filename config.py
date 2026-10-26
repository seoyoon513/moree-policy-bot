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


def _area_pages(raw: str) -> dict[str, str]:
    """"북마크=<페이지 URL/ID>,리뷰=<...>" → {기능영역: 페이지 값}. 쉼표·줄바꿈으로 구분."""
    out = {}
    for part in raw.replace("\n", ",").split(","):
        if "=" in part:
            area, page = part.split("=", 1)
            if area.strip() and page.strip():
                out[area.strip()] = page.strip()
    return out


# 원천 정책서(기능명세서) 반영 제안 — 기능영역별 정책서 페이지. 비우면 반영 단계를 건너뛴다
NOTION_SPEC_PAGES = _area_pages(os.environ.get("NOTION_SPEC_PAGES", ""))
# 반영 제안을 낼 상태. 논의중까지 올리면 PM에게 확정 안 된 제안이 쌓인다
SPEC_REFLECT_STATUSES = [s.strip() for s in os.environ.get("SPEC_REFLECT_STATUSES", "최종결정").split(",") if s.strip()]
# 반영 제안 댓글에서 멘션할 노션 사용자 ID (PM). 비우면 멘션 없이 댓글만
NOTION_PM_USER_IDS = [s.strip() for s in os.environ.get("NOTION_PM_USER_IDS", "").split(",") if s.strip()]
# 맞는 정책서를 못 찾은 제안을 모으는 페이지 (scripts/setup_spec_inbox.py로 생성). 비우면 디스코드에만 보고
NOTION_SPEC_INBOX_PAGE = os.environ.get("NOTION_SPEC_INBOX_PAGE", "").strip()
SPEC_MATCH_MODEL = os.environ.get("SPEC_MATCH_MODEL", "").strip() or CLAUDE_MODEL
SPEC_MATCH_MAX_TOKENS = int(os.environ.get("SPEC_MATCH_MAX_TOKENS", "4096"))

KST = ZoneInfo("Asia/Seoul")

# 스레드에서 읽어올 최대 메시지 수 / LLM에 넣을 최대 글자 수
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES", "500"))
MAX_TRANSCRIPT_CHARS = int(os.environ.get("MAX_TRANSCRIPT_CHARS", "100000"))
# 요약 응답 최대 토큰. 결정이 많은 긴 스레드는 4096에서 잘려 JSON이 깨진 적이 있음
SUMMARY_MAX_TOKENS = int(os.environ.get("SUMMARY_MAX_TOKENS", "8192"))

# 동의로 인정하는 리액션. 작성자가 아닌 사람이 남기면 대화록에 [동의 리액션: 이름]으로 표시 → 확정 신호
AGREE_EMOJIS = {"👍", "✅", "👌", "🙆", "🙆‍♀️", "🙆‍♂️", "⭕", "💯", "🆗"}

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
    "계정·프로필",   # 로그인 외 계정 관리, 탈퇴, 프로필·공유
    "앱 공통",       # 업데이트 팝업, 공통 컴포넌트 등 특정 화면에 속하지 않는 것
    "기타",
]

STATUSES = ["제안", "논의중", "최종결정"]
