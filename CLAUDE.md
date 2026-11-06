# policy-bot — 작업 안내 (Claude Code용)

Discord 스레드에 흩어진 정책 결정을 정리해 Notion에 쌓고, 쌓인 결정을 근거로 질문에 답하는 디스코드 봇.
기능·구조·실행 방법은 `README.md`, 배포는 `docs/DEPLOY.md`를 본다. 이 파일은 **작업할 때 지켜야 할 것**만 적는다.

## 세션 시작 시 먼저 읽을 것
1. `docs/DIRECTION.md` — 앞으로의 방향과 이미 내린 설계 결정 (왜 이렇게 하는지)
2. `docs/EXPERIMENTS.md` — 시도할 작업 목록과 진행 상태 (지금 무엇을 할 차례인지)
3. 작업이 끝나면 `docs/EXPERIMENTS.md`의 상태와 "작업 로그"를 갱신한다. 다음 세션은 이 파일로 이어간다.

## 구조 한 줄 요약
- `bot.py` + `views.py` + `policy_search.py`: 디스코드 봇 = **UI만**. 정리·승인·검색·답변 모두 FastAPI를 HTTP로 호출한다. 봇은 LLM·노션·원장 모듈을 import하지 않는다(테스트로 확인).
- `app/`: FastAPI 정책 서비스 (정리·승인·검색·답변·재인덱싱·사용량). `app/services/`에 흐름(요약·승인 저장·정책서 반영), `app/rag/`에 로더→청킹→임베딩→Chroma→검색(+리랭킹)→생성.
- 사용량은 `data/usage.jsonl` 하나가 기준 (토큰·비용·지연·사용자). 한도(`app/limits.py`)도 이 파일을 센다.
- `app/services/spec_reflect.py`: 승인된 결정을 정책서와 매칭해 댓글·콜아웃으로 반영 제안 (정책서 원문은 수정하지 않음).
- 정리 결과 저장: Notion DB(원본) + `data/history.jsonl` + `data/decisions/<영역>/`. 각 레코드에 `source.thread_id`가 있다.

## 지켜야 할 원칙
- **노션 정책 원문은 봇이 직접 덮어쓰지 않는다.** 변경은 댓글 또는 색 표시된 "제안"으로만 넣고, 반영은 PM이 한다.
- **쓰기 작업(노션 저장·수정)은 명시적 트리거로만.** 멘션 질문처럼 자유 입력에서 Claude가 "정리하라는 뜻인가"를 추측해 쓰지 않는다. 명령어나 버튼으로만 실행한다.
- **답변은 근거 기반.** 근거 없는 질문은 거절하고, 출처(노션/디스코드 링크)를 붙인다. 상태(제안/논의중/최종결정)를 구분한다.
- **설정은 `app/config.py`의 `Settings` 한 곳.** `os.environ`을 직접 읽지 않고 필드를 추가한다. 봇 코드는 루트 `config.py`의 같은 이름으로 쓴다. 테스트는 `.env`를 읽지 않는다(`ENV_FILE=""`).
- **노션 쓰기는 후속 작업으로.** 결정 저장은 원장 줄(+`ops`)이 먼저이고, 노션 저장·정책서 반영은 `app.services.followups`가 실행해 `outbox.jsonl`에 결과를 남긴다. 노션 쓰기는 다시 실행해도 결과가 같게(멱등) 만든다. 노션 날짜·`last_edited`는 **분 단위**다.
- **노션 API는 `app/notion/`만 부른다.** 클라이언트는 `app.notion.client`(재시도·로그 설정), 속성 이름은 `app.notion.schema`, 결정 기록 저장·조회는 `DecisionRepository`. 원장(`history.jsonl`)은 `app.ledger.Ledger`로만 읽고 쓴다.
- **로그는 `logging.getLogger(...)` + 필요하면 `extra={...}`.** 설정은 `app/logging_setup.setup_logging()` 한 곳. 봇의 새 진입점(이벤트·명령·버튼)은 `new_request_id()`로 시작하고, API 호출은 `policy_search.call_api`를 거쳐 `X-Request-ID`가 이어지게 한다.
- **비용을 기록하지 않는 LLM 호출을 만들지 않는다.** Claude 호출은 `app.llm.LLMClient`(`create`/`acreate`)만 쓴다 — 예산 확인·사용량 기록·오류 분류가 자동이다. `messages.create`를 직접 부르면 `tests/test_llm.py`가 실패한다.
- **가장 단순한 것부터, 필요할 때만 복잡성 추가.** 새 구성요소는 평가셋으로 효과를 보인 뒤 기본값으로 켠다.
- **변경 전후는 숫자로 비교한다.** 검색·답변 품질은 `python -m scripts.eval_rag`, 비용은 `/admin/usage`.

## 퍼블릭 레포 주의
- 이 레포는 GitHub 퍼블릭이다. **팀 정책 내용(결정 본문, 디스코드 링크·서버 ID, 팀원 이름)을 커밋하지 않는다.**
- `data/`, `eval/golden.jsonl`, `eval/results/`, `.env`는 gitignore 대상. 문서·테스트·예시에는 가짜 데이터만 쓴다.

## 자주 쓰는 명령
```bash
source .venv/bin/activate
python -m pytest -q                         # API 키 없이 도는 테스트
uvicorn app.api.main:app --reload           # API (http://localhost:8000/docs)
python bot.py                               # 봇
python -m scripts.eval_rag --sweep          # 검색 평가
python -m scripts.eval_rag --rerank --answers   # 답변 평가 (토큰 사용)
docker compose up -d --build                # api + bot
```

## 작업 방식
- 사용자는 이 프로젝트를 학습하며 직접 다시 짜보는 중이다. 큰 변경 전에는 접근 방식을 먼저 설명하고, 요청이 있으면 코드 대신 리뷰·개념 설명만 한다.
- 새 기능에는 테스트를 같이 추가한다 (가짜 임베더·메모리 Chroma 패턴은 `tests/conftest.py` 참고).
- 커밋 메시지는 한국어, 무엇을 왜 바꿨는지와 측정 결과가 있으면 숫자를 적는다.
