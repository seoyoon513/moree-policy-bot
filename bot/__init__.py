"""디스코드 봇 프로세스 — UI만 맡는다 (폴더 경계: bot/ → shared/ ← app/).

- main.py          : 진입점, /정책정리 (메시지 수집 → API POST /summaries)
- views.py         : 승인 화면 (결정별 수정·제외·연결 해제) → POST /summaries/{id}/approval
- mention.py       : @봇 멘션 질문 → POST /policies/answers
- policy_search.py : /정책검색, API 호출 공용 함수
- thread_reader.py : 디스코드 메시지 → ThreadMessage (동의 리액션 표시)

이 패키지는 shared/만 import하고 app/(LLM·노션·원장·벡터DB)은 import하지 않는다. 정리·저장·검색은 전부 HTTP로 API에.
실행: python -m bot
"""
