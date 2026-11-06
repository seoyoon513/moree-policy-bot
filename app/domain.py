"""도메인 상수 — 환경변수가 아니라 코드로 정하는 값. 봇·API·노션 스키마가 같이 쓴다.
루트 config.py가 같은 이름으로 다시 내보낸다 (`from config import AREAS`도 그대로 동작)."""
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

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

# 동의로 인정하는 리액션. 작성자가 아닌 사람이 남기면 대화록에 [동의 리액션: 이름]으로 표시 → 확정 신호
AGREE_EMOJIS = {"👍", "✅", "👌", "🙆", "🙆‍♀️", "🙆‍♂️", "⭕", "💯", "🆗"}


# 스레드 메시지 한 개 — 봇(디스코드에서 수집)과 API(요약)가 주고받는 단위
@dataclass
class ThreadMessage:
    index: int          # 1부터 시작하는 메시지 번호 (#N)
    author: str
    created_at: datetime
    content: str
    jump_url: str
    agreed_by: list[str] = field(default_factory=list)  # 동의 리액션을 남긴 사람 (작성자 제외)

    def to_line(self, new: bool = False) -> str:
        ts = self.created_at.astimezone(KST).strftime("%m-%d %H:%M")
        line = f"[#{self.index}] [{ts}]{' [새 메시지]' if new else ''} {self.author}: {self.content}"
        if self.agreed_by:
            line += f" [동의 리액션: {', '.join(self.agreed_by)}]"
        return line
