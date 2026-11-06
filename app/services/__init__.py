"""유스케이스(흐름) 계층 — 정리(요약) · 승인 저장 · 정책서 반영 제안 (ARCHITECTURE_REVIEW A-6).

봇이 아니라 API 프로세스가 이 흐름을 돌린다. 봇은 디스코드 UI(메시지 수집·승인 화면)만 맡고 HTTP로 부른다.
- summarizer.py   : 스레드 메시지 → Claude 요약 → 정규화된 결정 (재정리 판정 포함)
- storage.py      : 승인된 결정 저장 (원장 → 결정 기록 저장소)
- spec_reflect.py : 승인된 최종결정을 정책서와 매칭해 댓글·콜아웃·미분류 제안
- prompts.py      : 위 둘의 프롬프트
- approvals.py    : 정리 결과 보관(승인 대기) + 승인 처리 — API 엔드포인트가 부른다
"""
