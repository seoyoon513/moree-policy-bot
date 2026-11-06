"""봇과 API가 **함께 쓰는 것만** 둔다 (폴더 경계: bot/ → shared/ ← app/).

- config.py        : 설정 단일 출처 Settings (.env·환경변수)
- domain.py        : 도메인 상수(기능영역·상태 등)와 주고받는 단위(ThreadMessage)
- logging_setup.py : 로그 설정·request_id
- alerts.py        : 운영 채널 알림 (디스코드 웹훅)

여기에는 LLM·노션·벡터DB·원장 코드를 두지 않는다 — 그건 API 프로세스(app/)만 가진다.
봇이 app/을 import하지 않는지는 tests/test_loaders_and_bot.py가 확인한다.
"""
