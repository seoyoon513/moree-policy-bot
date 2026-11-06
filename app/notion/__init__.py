"""노션 접근은 이 패키지만 한다 (ARCHITECTURE_REVIEW A-3).

- client.py    : 클라이언트 생성 한 곳 (재시도·타임아웃·로거 설정), ID 파싱
- schema.py    : '정책 결정 히스토리' DB 속성 이름·타입·값 변환 (쓰기·읽기 공용)
- decisions.py : 결정 기록 저장소 (DecisionRepository) — 저장·갱신·조회·스키마 관리
정책서 읽기(app/rag/spec_loader.py)와 반영 제안(spec_reflect.py)도 클라이언트는 여기서 받는다.
"""
