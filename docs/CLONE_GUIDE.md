# 정책 봇 클론코딩 가이드

> ⚠️ 이 가이드는 2026-10-19 시점 코드 기준이다. `/정책질문` 대신 `@봇` 멘션, 정책서 인덱싱·반영 제안, 재정리, 하이브리드 검색은 반영되어 있지 않다. 현재 구조는 `README.md`를 본다.

원본 `~/policy-bot`을 새 폴더 `~/policy-bot-clone`에 처음부터 직접 타이핑해서 다시 만든다. 코드 블록은 실제로 돌아가는 원본 파일을 그대로 옮긴 것이다.

## 이 가이드 쓰는 법

1. VS Code에서 이 파일을 열고 `Cmd+Shift+V`로 미리보기를 연다. 미리보기 탭을 오른쪽으로 끌어 **왼쪽은 내 코드, 오른쪽은 가이드**로 둔다.
2. **복사·붙여넣기 하지 말고 직접 친다.** 치면서 각 파일 위의 "볼 포인트"를 생각한다.
3. 단계 끝의 **체크포인트**가 통과해야 다음 단계로 간다. 단계마다 `git commit`을 한다.
4. 막히면 원본과 비교한다. VS Code 탐색기에서 원본 파일과 내 파일을 `Cmd`로 둘 다 선택 → 우클릭 → **Compare Selected**.

| 단계 | 만드는 것 | 확인 방법 |
| --- | --- | --- |
| 0 | VS Code, 가상환경, 설정 파일 | `python --version`, `pip list` |
| 1 | 스레드 요약 + 토큰 기록 | 터미널에서 요약 결과 JSON 출력 |
| 2 | Discord 봇 + 승인 + 저장 | Discord에서 `/정책정리` |
| 3 | RAG 인덱싱 (Chroma) | `python -m scripts.ingest --search ...` |
| 4 | 검색·답변 API + 사용량 제한 | `localhost:8000/docs`, Discord `/정책질문` |
| 5 | 리랭커 | API 검색 결과가 줄고 정확해짐 |
| 6 | 테스트와 골든셋 평가 | `pytest`, `eval_rag --sweep` |
| 7 | 일괄 정리 스크립트 (선택) | `--limit 1` 실행 |
| 8 | Docker | `docker compose up` |

---

## 0단계 — VS Code와 프로젝트 준비

### 0-1. VS Code 기본 조작 (이것만 알면 된다)

| 하고 싶은 것 | 방법 |
| --- | --- |
| 명령 팔레트 (모든 기능 검색) | `Cmd+Shift+P` |
| 파일 이름으로 열기 | `Cmd+P` |
| 터미널 열기/닫기 | `` Ctrl+` `` (숫자 1 왼쪽 키) |
| 새 파일 / 새 폴더 | 왼쪽 탐색기 상단의 📄+ / 📁+ 아이콘. `app/rag/models.py`처럼 경로째 입력하면 폴더도 같이 생긴다 |
| 저장 | `Cmd+S` (설정 → "Auto Save"를 `afterDelay`로 바꾸면 자동 저장) |
| 화면 나누기 | `Cmd+\` |
| 정의로 이동 / 되돌아오기 | 함수 이름에서 `F12` / `Ctrl+-` |
| 줄 주석 | `Cmd+/` |

### 0-2. 확장 설치

왼쪽 세로 막대의 블록 모양 아이콘(Extensions, `Cmd+Shift+X`)에서 검색해 설치한다.

- **Python**, **Pylance** (Microsoft) — 실행·자동완성·오류 표시
- **Python Debugger** (Microsoft) — 중단점 디버깅
- **Ruff** — 코드 스타일·실수 검사
- **Container Tools** 또는 **Docker** (Microsoft) — 8단계에서 사용
- **REST Client** (Huachao Mao) — 4단계에서 API 호출용

터미널에서 `code` 명령을 쓰려면 명령 팔레트에서 **"Shell Command: Install 'code' command in PATH"**를 한 번 실행한다.

### 0-3. 폴더 만들고 열기

Mac 터미널(또는 VS Code 터미널)에서:

```bash
mkdir ~/policy-bot-clone
code ~/policy-bot-clone
```

### 0-4. 가상환경 (venv)

VS Code 터미널(`` Ctrl+` ``)에서:

```bash
python3 -m venv .venv
```

명령 팔레트 → **"Python: Select Interpreter"** → `./.venv/bin/python` 선택. 터미널을 휴지통 아이콘으로 닫고 다시 열면 프롬프트 앞에 `(.venv)`가 붙는다. 이제 이 프로젝트에서는 `python`, `pip`가 이 가상환경 것이다.

> 왜: 프로젝트마다 라이브러리 버전을 따로 두기 위해서다. 어제 `python: command not found`는 가상환경을 안 켜서 났다. VS Code는 인터프리터를 고르면 새 터미널마다 자동으로 켜 준다.

### 0-5. 설정 파일

#### `requirements.txt` (19줄)

설치할 라이브러리 목록. 봇 / RAG·API / 테스트로 나뉜다. `chromadb`는 인덱스 파일 형식 때문에 버전을 고정했다.

````text
# --- 봇 ---
discord.py>=2.4
anthropic>=0.40
python-dotenv>=1.0
notion-client>=3.0

# --- RAG + API ---
fastapi>=0.115
uvicorn[standard]>=0.30
pydantic-settings>=2.4
# 인덱스(data/chroma) 파일 형식이 버전에 묶여 있어서 Mac과 Docker가 같은 버전을 쓰도록 고정
chromadb==1.5.9
openai>=1.40
# 로컬 임베딩을 쓸 때만 (EMBEDDING_BACKEND=local). torch가 같이 설치되어 무거움
# sentence-transformers>=3.0

# --- 테스트 ---
pytest>=8
httpx>=0.27
````

```bash
pip install -r requirements.txt
```

#### `.gitignore` (23줄)

git에 올리지 않을 것들. 비밀값(`.env`)과 팀 데이터(`data/`)가 핵심이다.

````text
# 비밀값 — 절대 커밋 금지
.env

# 저장된 정책 데이터 (팀 내부 정보)
data/

# Python
.venv/
__pycache__/
*.pyc

# IDE / OS
.idea/
.vscode/
.DS_Store

# 테스트 캐시
.pytest_cache/

# 평가 결과 (답변 본문에 팀 정책 내용이 들어감)
eval/results/
# 실제 골든셋은 팀 정책 내용이 들어 있어 퍼블릭 레포에서 제외 (형식은 golden.example.jsonl 참고)
eval/golden.jsonl
````

#### `.env.example` (48줄)

필요한 환경변수 목록. 실제 값은 `.env`에 넣고 `.env`는 git에 올리지 않는다.

````text
DISCORD_TOKEN=
ANTHROPIC_API_KEY=
# 개발 중에는 테스트 서버 ID를 넣으면 슬래시 커맨드가 즉시 반영됨
GUILD_ID=
CLAUDE_MODEL=claude-sonnet-5-5

# Notion (비워두면 로컬 JSON에만 저장)
NOTION_TOKEN=
# DB를 만들 페이지 URL 또는 ID — setup_notion.py에서만 사용
NOTION_PARENT_PAGE_ID=
# 아래 두 개는 setup_notion.py가 자동으로 채움
NOTION_DATABASE_ID=
NOTION_DATA_SOURCE_ID=

# --- RAG / API (app/) ---
# 문서 소스: auto(Notion 설정 시 Notion, 아니면 data/history.jsonl) | notion | jsonl
RAG_SOURCE=auto
# 임베딩: openai | local
EMBEDDING_BACKEND=openai
OPENAI_API_KEY=
# /admin/sync-jobs 호출용 토큰
ADMIN_TOKEN=change-me
# 봇 -> API 서버 주소
POLICY_API_URL=http://localhost:8000
# 검색 결과 최소 점수 (엉뚱한 결과가 나오면 올리고, 결과가 너무 없으면 내리기)
RAG_MIN_SCORE=0.3

# --- 토큰 사용량 측정 (data/usage.jsonl) ---
# 단가: USD / 100만 토큰. 모델을 바꾸면 공식 가격표 보고 수정
PRICE_LLM_INPUT_PER_MTOK=3
PRICE_LLM_OUTPUT_PER_MTOK=15
PRICE_EMBED_PER_MTOK=0.02

# --- 외부 공개 시 필수 ---
# /policies/* 인증 키. 봇도 같은 값을 써서 호출한다. 예: python3 -c "import secrets; print(secrets.token_urlsafe(24))"
API_KEY=
# 리랭킹 (평가 후 켬)
RERANK_ENABLED=false

# --- 사용량 제한 ---
# 사용자별 하루 /정책질문 횟수 (LLM을 실제로 부른 것만). 0 = 제한 없음
DAILY_ANSWER_LIMIT=30
# 월 예산 USD (요약·리랭크·답변·임베딩 합계). 0 = 예산 없음
MONTHLY_BUDGET_USD=0
BUDGET_ALERT_RATIO=0.8
BUDGET_HARD_STOP=true
# 예산 알림을 받을 Discord 웹훅 URL (채널 설정 → 연동 → 웹후크 → URL 복사)
BUDGET_ALERT_WEBHOOK_URL=
````

```bash
cp .env.example .env
```

`.env`를 열어 원본 `~/policy-bot/.env`의 값을 옮겨 적는다. **클론 단계에서는 이렇게 바꾼다:**

- `NOTION_TOKEN`, `NOTION_DATABASE_ID`, `NOTION_DATA_SOURCE_ID`는 **비워 둔다.** 테스트하다 진짜 팀 Notion DB에 중복 결정이 쌓이지 않게 하려는 것이다. 비우면 로컬 `data/history.jsonl`에만 저장된다.
- `RAG_SOURCE=jsonl`, `RERANK_ENABLED=false` (5단계에서 켠다).
- 원본의 결정 83건을 가져와 RAG 실습에 쓴다:

```bash
mkdir -p data && cp ~/policy-bot/data/history.jsonl data/
```

> 주의: 원본 봇이 Docker로 돌고 있으면 같은 토큰의 봇이 두 개가 된다. 2단계 전에 원본 봇을 멈춘다: `cd ~/policy-bot && docker compose stop bot` (끝나면 `docker compose start bot`). 4단계 전에는 원본 api도 멈춘다(8000번 포트가 겹침): `docker compose stop api`.

```bash
git init && git add -A && git commit -m "0단계: 프로젝트 준비"
```

**체크포인트:** `python --version`이 venv의 파이썬을, `pip list | grep -i discord`가 discord.py를 보여 준다.

---

## 1단계 — 스레드 요약과 토큰 기록

**목표:** 대화 메시지 목록을 Claude에 보내 정책 결정 JSON으로 받고, 호출마다 토큰·비용을 기록한다. 아직 Discord는 없다.

**배우는 것:** 시스템 프롬프트로 출력 형식 정하기, 모델 응답에서 JSON 뽑기, 잘못된 값 보정, `max_tokens`와 응답 잘림, 토큰 비용 계산.

#### `app/__init__.py`

`app` 폴더를 패키지로 만든다.

> 빈 파일로 만들기만 하면 된다. 이 폴더를 파이썬 패키지로 인식시키는 표시다.

#### `app/usage.py` (166줄)

**토큰 기록.** 모든 LLM·임베딩 호출의 토큰과 비용을 `data/usage.jsonl`에 한 줄씩 남긴다.

- 볼 포인트: `compute_cost` — 입력·출력 단가가 다르고(출력이 5배), 작은 모델(haiku)은 단가표가 따로다.
- 볼 포인트: `record`가 실패해도 예외를 삼킨다. 기록 실패가 본 기능을 막으면 안 되기 때문이다.

````python
"""토큰 사용량 측정.

LLM/임베딩 API를 부를 때마다 응답의 usage(토큰 수)를 한 줄씩 data/usage.jsonl에 남긴다.
봇(요약)과 API 서버(임베딩·답변)가 같은 파일에 쓰므로, 기능별·일별 비용을 한 곳에서 본다.

    record("answer", model, input_tokens=..., output_tokens=..., latency_ms=...)
    summarize(days=7)  # 기능별 합계

단가는 .env로 바꿀 수 있다 (USD / 100만 토큰). 기본값은 작성 시점 공개 가격이라
모델을 바꾸거나 가격이 바뀌면 반드시 다시 확인할 것.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

_lock = threading.Lock()


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except ValueError:
        return default


# USD per 1M tokens. (input, output)
def price_table() -> dict[str, tuple[float, float]]:
    return {
        "llm": (_env_float("PRICE_LLM_INPUT_PER_MTOK", 3.0), _env_float("PRICE_LLM_OUTPUT_PER_MTOK", 15.0)),
        # 리랭커 등에 쓰는 작은 모델(이름에 haiku 포함). 단가가 큰 모델과 달라 따로 둔다
        "llm_small": (_env_float("PRICE_SMALL_LLM_INPUT_PER_MTOK", 1.0), _env_float("PRICE_SMALL_LLM_OUTPUT_PER_MTOK", 5.0)),
        "embedding": (_env_float("PRICE_EMBED_PER_MTOK", 0.02), 0.0),
    }


def _price_key(kind: str, model: str) -> str:
    if kind == "llm" and "haiku" in model.lower():
        return "llm_small"
    return kind


def usage_path() -> Path:
    return Path(os.environ.get("USAGE_LOG_PATH", os.path.join(os.environ.get("DATA_DIR", "data"), "usage.jsonl")))


@dataclass
class Usage:
    feature: str            # summarize | answer | embed_query | embed_index | judge
    model: str
    kind: str = "llm"       # llm | embedding
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    user: str | None = None  # 호출한 사람 (Discord 사용자 ID). 사용자별 일일 제한에 쓴다
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def compute_cost(self) -> "Usage":
        inp, out = price_table()[_price_key(self.kind, self.model)]
        # 캐시 읽기는 입력 단가의 10%, 캐시 쓰기는 125% (Anthropic 프롬프트 캐싱 기준)
        self.cost_usd = round(
            (self.input_tokens * inp + self.output_tokens * out
             + self.cache_read_tokens * inp * 0.1 + self.cache_write_tokens * inp * 1.25) / 1_000_000,
            6,
        )
        return self


def record(u: Usage) -> Usage:
    """비용 계산 후 파일에 append. 기록 실패가 본 기능을 막으면 안 되므로 예외는 삼킨다."""
    u.compute_cost()
    if os.environ.get("USAGE_LOG_DISABLED") == "1":
        return u
    try:
        p = usage_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock, p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(u), ensure_ascii=False) + "\n")
    except OSError:
        pass
    return u


def from_anthropic(feature: str, model: str, resp, latency_ms: int, user: str | None = None) -> Usage:
    """anthropic Message 응답의 usage → Usage 기록."""
    us = getattr(resp, "usage", None)
    return record(Usage(
        feature=feature, model=model, kind="llm",
        input_tokens=getattr(us, "input_tokens", 0) or 0,
        output_tokens=getattr(us, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(us, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(us, "cache_creation_input_tokens", 0) or 0,
        latency_ms=latency_ms,
        user=user,
    ))


@contextmanager
def timer():
    """with timer() as t: ...  →  t() 로 경과 ms"""
    start = time.perf_counter()
    yield lambda: int((time.perf_counter() - start) * 1000)


def load(days: int | None = None) -> list[dict]:
    p = usage_path()
    if not p.exists():
        return []
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since and datetime.fromisoformat(r["ts"]) < since:
            continue
        rows.append(r)
    return rows


def summarize(days: int | None = 7) -> dict:
    """기능별 호출 수 / 토큰 / 비용 / 평균 지연 + 일별 비용."""
    rows = load(days)
    by_feature: dict[str, dict] = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                                       "cost_usd": 0.0, "latency_ms_sum": 0})
    by_day: dict[str, float] = defaultdict(float)
    for r in rows:
        f = by_feature[r["feature"]]
        f["calls"] += 1
        f["input_tokens"] += r.get("input_tokens", 0) + r.get("cache_read_tokens", 0) + r.get("cache_write_tokens", 0)
        f["output_tokens"] += r.get("output_tokens", 0)
        f["cost_usd"] += r.get("cost_usd", 0.0)
        f["latency_ms_sum"] += r.get("latency_ms", 0)
        by_day[r["ts"][:10]] += r.get("cost_usd", 0.0)

    features = {}
    for name, f in by_feature.items():
        calls = f["calls"]
        features[name] = {
            "calls": calls,
            "input_tokens": f["input_tokens"],
            "output_tokens": f["output_tokens"],
            "avg_input_tokens": round(f["input_tokens"] / calls),
            "avg_output_tokens": round(f["output_tokens"] / calls),
            "cost_usd": round(f["cost_usd"], 4),
            "avg_cost_usd": round(f["cost_usd"] / calls, 6),
            "avg_latency_ms": round(f["latency_ms_sum"] / calls),
        }
    return {
        "days": days,
        "total_calls": len(rows),
        "total_cost_usd": round(sum(r.get("cost_usd", 0.0) for r in rows), 4),
        "features": features,
        "daily_cost_usd": {d: round(c, 4) for d, c in sorted(by_day.items())},
    }
````

#### `config.py` (50줄)

**봇 설정.** `.env`를 읽고, 기능 영역·상태 목록 같은 상수를 둔다. 영역을 바꾸면 이 파일만 고친다.

````python
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
# 요약 응답 최대 토큰. 결정이 많은 긴 스레드는 4096에서 잘려 JSON이 깨진 적이 있음
SUMMARY_MAX_TOKENS = int(os.environ.get("SUMMARY_MAX_TOKENS", "8192"))

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
````

#### `prompts.py` (53줄)

**요약 프롬프트.** 결정을 어떻게 나누고, 상태를 어떻게 판단하고, 무엇을 빼는지 규칙을 정한다. 출력 JSON 스키마도 여기 있다.

- 볼 포인트: 규칙 5(구현 지시는 제외), 규칙 6(모르면 null) — 환각을 막는 장치다.

````python
"""요약 프롬프트. 스키마/규칙을 바꿀 때는 이 파일만 수정."""
from config import AREAS, STATUSES

SYSTEM_PROMPT = f"""너는 앱 서비스 팀의 정책 결정 기록 담당자다.
디스코드 스레드 대화를 읽고, 그 안에서 나온 '기획/정책 결정'을 구조화된 JSON으로 정리한다.

## 정리 규칙
1. 하나의 스레드에 결정이 여러 개면 주제별로 나눠서 각각 항목으로 만든다.
2. 같은 주제에서 의견이 바뀌었다면 **가장 마지막 합의**를 decision으로 쓰고, 뒤집힌 이전 의견은 rationale에 "초기에는 ~였으나 ~ 때문에 변경"처럼 남긴다.
3. 상태(status) 판단 기준:
   - "제안": 누군가 제안했지만 반응/합의가 없음
   - "논의중": 의견이 오갔지만 결론이 안 남, 또는 확인이 필요하다고 끝남
   - "최종결정": 결정 권한자(PM/기획/리드 등)가 확정했거나, 참여자 전원이 명시적으로 합의함
4. "피그마 참고", "디자인 시안대로" 처럼 외부 참조만 있는 결정도 항목으로 기록하고 external_refs에 넣는다.
5. 서버/클라이언트 구현 작업 지시(예: "API 필드 추가해주세요", "이거 제가 오늘 PR 올릴게요")는 정책이 아니므로 **제외**한다.
6. 대화에 없는 내용은 추측해서 채우지 않는다. 모르면 null.
7. 확정되지 않은 부분은 open_questions에 담당자에게 물어볼 질문으로 만든다. 누구에게 물어야 할지 대화에서 보이면 ask_to에 이름을 쓴다.
8. source_messages에는 근거가 된 메시지 번호(#N의 N)를 넣는다.
9. 정책 결정이나 논의가 전혀 없으면 decisions를 빈 배열로 둔다.

## 기능 영역(area) — 반드시 이 중 하나
{", ".join(AREAS)}

## 출력 형식 — 이 JSON 하나만 출력하고, 설명이나 코드펜스는 붙이지 않는다
{{
  "decisions": [
    {{
      "area": "기능 영역",
      "topic": "한 줄 주제 (예: 비회원 북마크 허용 여부)",
      "status": "{'" | "'.join(STATUSES)}",
      "decision": "결정 내용. 사람이 읽고 바로 이해되게, 상황별 UX가 드러나게 (예: 비로그인 상태에서 북마크 탭 시 → 로그인 유도 바텀시트)",
      "rationale": "결정 근거 또는 null",
      "decided_by": "결정자 이름 또는 null",
      "scope": "적용 범위 (플랫폼, 화면, 사용자 유형, 적용 버전 등) 또는 null",
      "external_refs": ["피그마 등 외부 참조"],
      "open_questions": [{{"question": "확인 질문", "ask_to": "담당자 또는 null"}}],
      "source_messages": [1, 4]
    }}
  ]
}}
"""


def build_user_prompt(thread_name: str, transcript: str) -> str:
    return f"""스레드 제목: {thread_name}

아래는 스레드 대화다. 각 줄은 [#번호] [시각] 작성자: 내용 형식이다.

<thread>
{transcript}
</thread>

위 규칙대로 JSON만 출력해라."""
````

#### `summarizer.py` (154줄)

**요약 실행.** 메시지 → 프롬프트 → Claude → JSON 파싱 → 값 보정.

- 볼 포인트: `build_transcript` — 너무 길면 첫 메시지(주제)는 남기고 오래된 중간부터 뺀다.
- 볼 포인트: `stop_reason == "max_tokens"` 확인 — 어제 요약 실패 원인이 출력 잘림이었다.
- 볼 포인트: `normalize` — 모델이 목록에 없는 영역을 쓰면 '기타'로 바꾸는 등, 모델 출력을 그대로 믿지 않는다.

````python
"""스레드 메시지 → Claude 요약 → 정규화된 결정 목록."""
import json
import re
from dataclasses import dataclass
from datetime import datetime

from anthropic import AsyncAnthropic

from app import usage as usage_log

from config import ANTHROPIC_API_KEY, AREAS, CLAUDE_MODEL, KST, MAX_TRANSCRIPT_CHARS, STATUSES, SUMMARY_MAX_TOKENS
from prompts import SYSTEM_PROMPT, build_user_prompt


class SummaryError(Exception):
    def __init__(self, msg: str, raw: str | None = None):
        super().__init__(msg)
        self.raw = raw  # 실패 원인 분석용 모델 원문 응답


@dataclass
class ThreadMessage:
    index: int          # 1부터 시작하는 메시지 번호 (#N)
    author: str
    created_at: datetime
    content: str
    jump_url: str

    def to_line(self) -> str:
        ts = self.created_at.astimezone(KST).strftime("%m-%d %H:%M")
        return f"[#{self.index}] [{ts}] {self.author}: {self.content}"


_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def build_transcript(messages: list[ThreadMessage]) -> str:
    """글자 수 제한을 넘으면 첫 메시지(주제)는 남기고 오래된 중간부터 생략."""
    lines = [m.to_line() for m in messages]
    if sum(len(l) + 1 for l in lines) <= MAX_TRANSCRIPT_CHARS or len(lines) <= 2:
        return "\n".join(lines)

    head = lines[0]
    budget = MAX_TRANSCRIPT_CHARS - len(head) - 50
    tail: list[str] = []
    for line in reversed(lines[1:]):
        if budget - (len(line) + 1) < 0:
            break
        tail.append(line)
        budget -= len(line) + 1
    tail.reverse()
    skipped = len(lines) - 1 - len(tail)
    return "\n".join([head, f"... (중간 메시지 {skipped}개 생략) ...", *tail])


def parse_json(text: str) -> dict:
    """모델 응답에서 JSON 객체만 뽑아낸다 (코드펜스/앞뒤 설명이 붙어도 처리)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise SummaryError("모델 응답에 JSON이 없어요.")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise SummaryError(f"JSON 파싱 실패: {e}") from e


def _str_or_none(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def normalize(data: dict, max_index: int) -> list[dict]:
    """필드 누락/잘못된 값을 보정해서 저장·표시 코드가 안전하게 쓰게 한다."""
    out = []
    for d in data.get("decisions") or []:
        if not isinstance(d, dict):
            continue
        decision = _str_or_none(d.get("decision"))
        topic = _str_or_none(d.get("topic"))
        if not decision and not topic:
            continue

        area = d.get("area") if d.get("area") in AREAS else "기타"
        status = d.get("status") if d.get("status") in STATUSES else "논의중"

        questions = []
        for q in d.get("open_questions") or []:
            if isinstance(q, dict) and _str_or_none(q.get("question")):
                questions.append({"question": q["question"].strip(), "ask_to": _str_or_none(q.get("ask_to"))})
            elif isinstance(q, str) and q.strip():
                questions.append({"question": q.strip(), "ask_to": None})

        sources = []
        for s in d.get("source_messages") or []:
            try:
                n = int(str(s).lstrip("#"))
            except ValueError:
                continue
            if 1 <= n <= max_index and n not in sources:
                sources.append(n)

        out.append(
            {
                "area": area,
                "topic": topic or "(주제 없음)",
                "status": status,
                "decision": decision or "",
                "rationale": _str_or_none(d.get("rationale")),
                "decided_by": _str_or_none(d.get("decided_by")),
                "scope": _str_or_none(d.get("scope")),
                "external_refs": [str(r).strip() for r in d.get("external_refs") or [] if str(r).strip()],
                "open_questions": questions,
                "source_messages": sources,
            }
        )
    return out


async def summarize_thread(thread_name: str, messages: list[ThreadMessage]) -> list[dict]:
    transcript = build_transcript(messages)
    try:
        with usage_log.timer() as elapsed:
            resp = await _get_client().messages.create(
                model=CLAUDE_MODEL,
                max_tokens=SUMMARY_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_user_prompt(thread_name, transcript)}],
            )
    except Exception as e:  # 네트워크/인증/레이트리밋 등
        raise SummaryError(f"Claude API 호출 실패: {e}") from e

    usage_log.from_anthropic("summarize", CLAUDE_MODEL, resp, elapsed())
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    if getattr(resp, "stop_reason", None) == "max_tokens":
        # 출력이 한도에서 잘리면 JSON이 닫히지 않아 파싱이 엉뚱한 위치에서 실패한다 → 원인을 그대로 알려줌
        raise SummaryError(f"출력이 max_tokens({SUMMARY_MAX_TOKENS})에서 잘림 — 스레드가 길거나 응답이 반복됨", raw=text)
    try:
        return normalize(parse_json(text), max_index=len(messages))
    except SummaryError as e:
        e.raw = text
        raise
````

**체크포인트** — 터미널에 그대로 친다:

```bash
python - <<'EOF'
import asyncio
from datetime import datetime, timezone
from summarizer import ThreadMessage, summarize_thread

now = datetime.now(timezone.utc)
msgs = [
    ThreadMessage(1, "PM", now, "북마크 폴더명은 최대 18자로 제한하죠", ""),
    ThreadMessage(2, "서버", now, "네, 서버에서도 18자로 검증할게요", ""),
    ThreadMessage(3, "PM", now, "좋아요 그걸로 확정합니다", ""),
]
for d in asyncio.run(summarize_thread("북마크 폴더명 글자수", msgs)):
    print(d["area"], d["status"], d["topic"], "-", d["decision"])
EOF
tail -1 data/usage.jsonl
```

`북마크 최종결정 ...`이 나오고, `usage.jsonl` 마지막 줄에 `"feature": "summarize"`와 토큰 수가 보이면 성공이다.

```bash
git add -A && git commit -m "1단계: 스레드 요약 + 토큰 기록"
```

---

## 2단계 — Discord 봇, 승인, 저장

**목표:** 스레드에서 `/정책정리`를 치면 요약을 보여 주고, 요청자가 승인하면 저장한다.

**배우는 것:** 봇이 Discord Gateway(WebSocket)로 먼저 연결하는 구조, 슬래시 커맨드, 3초 안에 응답하기 위한 `defer`, 버튼(View), 사람 승인(human-in-the-loop).

#### `notion_store.py` (146줄)

**Notion 저장.** 결정 1건을 Notion DB 페이지로 만든다. 지금은 `.env`에 Notion 값이 비어 있어서 `enabled()`가 False라 실행되지 않지만, 구조를 보며 친다.

- 볼 포인트: Notion은 속성 타입(title, select, rich_text, date, url)마다 JSON 모양이 다르다.
- 볼 포인트: rich_text 한 덩어리는 2000자 제한이 있어 잘라서 넣는다.

````python
"""Notion '정책 결정 히스토리' DB 연동 (Notion API 2025-09-03, data source 기반).

- DB 속성 스키마는 PROPERTIES 한 곳에서 관리
- setup_notion.py가 이 스키마로 DB를 생성
- 승인 시 save_record()가 결정 1건 = 페이지 1개로 추가 (append-only, 덮어쓰지 않음)
"""
import re

from notion_client import AsyncClient

from config import AREAS, NOTION_DATA_SOURCE_ID, NOTION_TOKEN, STATUSES

DB_TITLE = "정책 결정 히스토리"

STATUS_COLORS = {"제안": "gray", "논의중": "yellow", "최종결정": "green"}
_AREA_COLORS = ["blue", "purple", "pink", "orange", "brown", "red", "green", "yellow", "gray"]

PROPERTIES = {
    "주제": {"title": {}},
    "기능영역": {"select": {"options": [{"name": a, "color": _AREA_COLORS[i % len(_AREA_COLORS)]} for i, a in enumerate(AREAS)]}},
    "상태": {"select": {"options": [{"name": s, "color": STATUS_COLORS[s]} for s in STATUSES]}},
    "결정": {"rich_text": {}},
    "근거": {"rich_text": {}},
    "결정자": {"rich_text": {}},
    "적용범위": {"rich_text": {}},
    "외부참조": {"rich_text": {}},
    "확인필요": {"rich_text": {}},
    "스레드": {"url": {}},
    "스레드명": {"rich_text": {}},
    "승인자": {"rich_text": {}},
    "승인일": {"date": {}},
    "기록ID": {"rich_text": {}},
}

_client: AsyncClient | None = None


def enabled() -> bool:
    return bool(NOTION_TOKEN and NOTION_DATA_SOURCE_ID)


def get_client() -> AsyncClient:
    global _client
    if _client is None:
        _client = AsyncClient(auth=NOTION_TOKEN)
    return _client


def extract_id(value: str) -> str:
    """페이지 URL이나 ID 문자열에서 32자리 ID를 뽑는다."""
    path = value.split("?")[0].split("#")[0]
    uuid_form = re.findall(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", path)
    if uuid_form:
        return uuid_form[-1].replace("-", "")
    # 'Moree-1a2b...' 처럼 제목 뒤에 붙은 32자리: 앞이 hex가 아닌 위치에서만 매칭
    found = re.findall(r"(?<![0-9a-fA-F])([0-9a-fA-F]{32})(?![0-9a-fA-F])", path)
    if not found:
        raise ValueError(f"Notion ID를 찾을 수 없어요: {value}")
    return found[-1]


def _rt(text: str | None) -> list[dict]:
    """rich_text 배열. Notion 제한: 조각당 2000자, 최대 100조각."""
    if not text:
        return []
    return [{"type": "text", "text": {"content": text[i : i + 2000]}} for i in range(0, min(len(text), 2000 * 100), 2000)]


def _questions_text(questions: list[dict]) -> str:
    return "\n".join(f"• {q['question']}" + (f" → {q['ask_to']}" if q.get("ask_to") else "") for q in questions)


def build_properties(record: dict) -> dict:
    src = record["source"]
    return {
        "주제": {"title": _rt(record["topic"])},
        "기능영역": {"select": {"name": record["area"]}},
        "상태": {"select": {"name": record["status"]}},
        "결정": {"rich_text": _rt(record["decision"])},
        "근거": {"rich_text": _rt(record.get("rationale"))},
        "결정자": {"rich_text": _rt(record.get("decided_by"))},
        "적용범위": {"rich_text": _rt(record.get("scope"))},
        "외부참조": {"rich_text": _rt("\n".join(record.get("external_refs") or []))},
        "확인필요": {"rich_text": _rt(_questions_text(record.get("open_questions") or []))},
        "스레드": {"url": src.get("thread_url") or None},
        "스레드명": {"rich_text": _rt(src.get("thread_name"))},
        "승인자": {"rich_text": _rt(record.get("approved_by"))},
        "승인일": {"date": {"start": record["approved_at"]}},
        "기록ID": {"rich_text": _rt(record["id"])},
    }


def _heading(text: str) -> dict:
    return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": _rt(text)}}


def _paragraph(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rt(text)}}


def _bullet(text: str, url: str | None = None) -> dict:
    rich = [{"type": "text", "text": {"content": text[:2000], "link": {"url": url} if url else None}}]
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich}}


def build_children(record: dict) -> list[dict]:
    """페이지 본문: 사람이 읽기 쉬운 형태 + 근거 메시지 링크."""
    blocks = [_heading("결정"), _paragraph(record["decision"] or "-")]
    if record.get("rationale"):
        blocks += [_heading("근거"), _paragraph(record["rationale"])]
    if record.get("open_questions"):
        blocks.append(_heading("확인 필요"))
        blocks += [
            _bullet(q["question"] + (f" → {q['ask_to']}" if q.get("ask_to") else "")) for q in record["open_questions"]
        ]
    urls = record["source"].get("message_urls") or []
    if urls:
        blocks.append(_heading("근거 메시지"))
        blocks += [_bullet(f"디스코드 메시지 {i}", u) for i, u in enumerate(urls, start=1)]
    return blocks[:100]


async def save_record(record: dict) -> str:
    """결정 1건을 Notion 페이지로 추가하고 페이지 URL을 돌려준다."""
    page = await get_client().pages.create(
        parent={"type": "data_source_id", "data_source_id": NOTION_DATA_SOURCE_ID},
        properties=build_properties(record),
        children=build_children(record),
    )
    return page.get("url", "")


async def create_database(parent_page_id: str) -> dict:
    """부모 페이지 아래에 히스토리 DB를 만든다. setup_notion.py에서 1회 실행."""
    db = await get_client().databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=_rt(DB_TITLE),
        initial_data_source={"properties": PROPERTIES},
    )
    sources = db.get("data_sources") or []
    if not sources:  # 응답에 없으면 다시 조회
        db = await get_client().databases.retrieve(database_id=db["id"])
        sources = db.get("data_sources") or []
    if not sources:
        raise RuntimeError("DB는 만들었지만 data source ID를 찾지 못했어요.")
    return {"database_id": db["id"], "data_source_id": sources[0]["id"], "url": db.get("url", "")}
````

#### `storage.py` (73줄)

**저장 통합.** 로컬 JSON은 항상, Notion은 설정됐을 때만 저장한다.

- 볼 포인트: `approved_at` 인자 — 일괄 정리 때 과거 스레드 시각을 승인일로 넣기 위해 추가했다.

````python
"""승인된 결정 저장.

1) 로컬 JSON에 항상 저장 (백업 겸 오프라인 확인용)
   data/decisions/<기능영역>/<YYYYMMDD-HHMMSS>_<id>.json   결정 1건 = 파일 1개
   data/history.jsonl                                      전체 히스토리 (append-only)
2) Notion이 설정돼 있으면 '정책 결정 히스토리' DB에 페이지로 추가
"""
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import notion_store
from config import DATA_DIR, KST

log = logging.getLogger("policy-bot")


@dataclass
class SaveResult:
    record: dict
    path: Path
    notion_url: str | None = None
    notion_error: str | None = None


def _safe_dirname(name: str) -> str:
    return "".join(c if c not in '\\/:*?"<>|' else "_" for c in name)


def _build_record(d: dict, source: dict, approved_by: str, now: datetime) -> dict:
    urls_by_index = source.get("message_urls_by_index", {})
    src = {k: v for k, v in source.items() if k != "message_urls_by_index"}
    src["message_urls"] = [urls_by_index[n] for n in d.get("source_messages", []) if n in urls_by_index]
    return {
        "id": uuid.uuid4().hex[:8],
        **{k: v for k, v in d.items() if k != "source_messages"},
        "source": src,
        "approved_by": approved_by,
        "approved_at": now.isoformat(timespec="seconds"),
    }


def _write_local(record: dict, now: datetime) -> Path:
    base = Path(DATA_DIR)
    folder = base / "decisions" / _safe_dirname(record["area"])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{now.strftime('%Y%m%d-%H%M%S')}_{record['id']}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    with (base / "history.jsonl").open("a", encoding="utf-8") as hf:
        hf.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


async def save_decisions(decisions: list[dict], *, source: dict, approved_by: str,
                         approved_at: datetime | None = None) -> list[SaveResult]:
    """approved_at: 지난 스레드를 일괄 저장할 때 '결정이 난 시점'(스레드 마지막 메시지 시각)을 넣는다.
    비우면 지금 시각. 이 값이 Notion '승인일'이 되고, 검색·답변의 시간순 정렬 기준이 된다."""
    now = approved_at.astimezone(KST) if approved_at else datetime.now(KST)
    results = []
    for d in decisions:
        record = _build_record(d, source, approved_by, now)
        result = SaveResult(record=record, path=_write_local(record, now))
        if notion_store.enabled():
            try:
                result.notion_url = await notion_store.save_record(record)
            except Exception as e:  # 로컬은 저장됐으니 Notion 실패만 보고
                log.exception("Notion 저장 실패 (id=%s)", record["id"])
                result.notion_error = str(e)[:300]
        results.append(result)
    return results
````

#### `policy_search.py` (153줄)

**`/정책검색`, `/정책질문`.** 봇은 RAG를 직접 하지 않고 API 서버에 HTTP로 묻는다. 4단계 전까지는 "API 서버에 연결할 수 없어요"가 정상이다.

- 볼 포인트: `_call`의 헤더 — `X-API-Key`(인증), `X-User-Id`(사용량 제한).
- 볼 포인트: `schedule_reindex` — 승인 직후 재인덱싱을 백그라운드로 요청한다. Notion에 막 만든 페이지가 조회되기까지 몇 초 걸려서 3초 기다린다.

````python
"""/정책검색, /정책질문 슬래시 커맨드 + 승인 후 자동 재인덱싱.
봇은 RAG를 직접 돌리지 않고 FastAPI(app/api)만 호출한다.

bot.py에서:
    from policy_search import register_policy_commands
    register_policy_commands(bot.tree)
views.py 승인 직후:
    schedule_reindex()

API 서버 주소: .env 의 POLICY_API_URL (기본 http://localhost:8000)
"""
import asyncio
import logging
import os

import aiohttp  # discord.py 의존성이라 이미 설치되어 있음
import discord
from discord import app_commands

from config import AREAS, STATUSES

log = logging.getLogger("policy-bot")

API_URL = os.environ.get("POLICY_API_URL", "http://localhost:8000").rstrip("/")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
API_KEY = os.environ.get("API_KEY", "")  # API 서버가 키를 요구할 때 (X-API-Key)
TIMEOUT = aiohttp.ClientTimeout(total=90)

# Notion은 방금 만든 페이지가 query 결과에 뜨기까지 몇 초 걸릴 수 있어서 잠깐 기다렸다가 동기화
REINDEX_INITIAL_DELAY = 3.0
REINDEX_RETRY_DELAY = 5.0
REINDEX_RETRIES = 6

AREA_CHOICES = [app_commands.Choice(name=a, value=a) for a in AREAS]
STATUS_CHOICES = [app_commands.Choice(name=s, value=s) for s in STATUSES]

_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=TIMEOUT)
    return _session


async def _call(method: str, path: str, **kwargs) -> tuple[int, dict]:
    try:
        headers = {**({"X-API-Key": API_KEY} if API_KEY else {}), **kwargs.pop("headers", {})}
        async with _get_session().request(method, f"{API_URL}{path}", headers=headers, **kwargs) as r:
            try:
                data = await r.json()
            except aiohttp.ContentTypeError:
                data = {"detail": await r.text()}
            return r.status, data
    except aiohttp.ClientConnectionError:
        return 0, {"detail": f"정책 API 서버({API_URL})에 연결할 수 없어요. uvicorn이 떠 있는지 확인해 주세요."}


async def _reindex() -> None:
    """동기화 작업 생성. 이미 돌고 있으면(409) 그 작업이 이번 결정을 못 읽었을 수 있으니 끝날 때까지 기다렸다 다시 요청."""
    await asyncio.sleep(REINDEX_INITIAL_DELAY)
    for _ in range(REINDEX_RETRIES):
        code, data = await _call("POST", "/admin/sync-jobs", json={}, headers={"X-Admin-Token": ADMIN_TOKEN})
        if code == 202:
            log.info("재인덱싱 시작 (job=%s)", data.get("id"))
            return
        if code == 409:
            await asyncio.sleep(REINDEX_RETRY_DELAY)
            continue
        log.warning("재인덱싱 요청 실패 (%s): %s", code, data.get("detail"))
        return
    log.warning("재인덱싱: 진행 중인 작업이 계속 있어 이번 요청은 건너뜀")


_background: set[asyncio.Task] = set()


def schedule_reindex() -> None:
    """승인 응답을 막지 않도록 백그라운드로 실행. 실패해도 저장에는 영향 없음."""
    task = asyncio.create_task(_reindex())
    _background.add(task)  # 참조를 들고 있어야 GC로 중간에 사라지지 않음
    task.add_done_callback(_background.discard)


def _tag(it: dict) -> str:
    return " · ".join(x for x in (it.get("area"), it.get("status"), it.get("decided_at"), it.get("decider")) if x)


def register_policy_commands(tree: app_commands.CommandTree) -> None:
    @tree.command(name="정책검색", description="저장된 정책 결정을 의미 기반으로 검색해요")
    @app_commands.describe(검색어="찾고 싶은 내용", 영역="기능영역 필터 (선택)", 상태="상태 필터 (선택)")
    @app_commands.choices(영역=AREA_CHOICES, 상태=STATUS_CHOICES)
    async def search(
        interaction: discord.Interaction,
        검색어: str,
        영역: app_commands.Choice[str] | None = None,
        상태: app_commands.Choice[str] | None = None,
    ):
        await interaction.response.defer(thinking=True)
        params = {"q": 검색어, "k": 5}
        if 영역:
            params["area"] = 영역.value
        if 상태:
            params["status"] = 상태.value
        code, data = await _call("GET", "/policies/search", params=params)
        if code != 200:
            await interaction.followup.send(f"검색 실패 ({code}): {data.get('detail')}")
            return
        if not data["items"]:
            await interaction.followup.send("관련 정책을 찾지 못했어요.")
            return

        embed = discord.Embed(title=f"🔎 {검색어}", color=0x5865F2)
        for it in data["items"]:
            link = f"\n[원문 보기]({it['url']})" if it.get("url") else ""
            embed.add_field(
                name=f"{it['title'][:200]}  ({it['score']:.2f})",
                value=f"{_tag(it)}\n{it['snippet'][:700]}{link}"[:1024],
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    @tree.command(name="정책질문", description="저장된 정책 결정을 근거로 질문에 답해요")
    @app_commands.describe(질문="예: 오프라인으로 앱 실행하면 어디로 진입하기로 했지?", 영역="기능영역 필터 (선택)")
    @app_commands.choices(영역=AREA_CHOICES)
    async def ask(interaction: discord.Interaction, 질문: str, 영역: app_commands.Choice[str] | None = None):
        await interaction.response.defer(thinking=True)
        payload = {"question": 질문}
        if 영역:
            payload["area"] = 영역.value
        # 사용자별 일일 제한을 위해 Discord 사용자 ID를 함께 보낸다 (헤더는 ASCII만 가능해서 이름 대신 ID)
        code, data = await _call("POST", "/policies/answers", json=payload,
                                 headers={"X-User-Id": str(interaction.user.id)})
        if code == 429:
            await interaction.followup.send(f"⏸️ {data.get('detail')}", ephemeral=True)
            return
        if code != 200:
            await interaction.followup.send(f"답변 실패 ({code}): {data.get('detail')}")
            return

        sources = "\n".join(
            f"[{i}] [{s['title']}]({s['url']})" if s.get("url") else f"[{i}] {s['title']}"
            for i, s in enumerate(data["sources"], 1)
        )
        u = data.get("usage")
        q = data.get("quota") or {}
        quota = f" · 오늘 {q['used_today']}/{q['daily_limit']}회" if q.get("daily_limit") else ""
        footer = (f"\n-# 토큰 in {u['input_tokens']:,} / out {u['output_tokens']:,} · ${u['cost_usd']:.4f} · {u['latency_ms'] / 1000:.1f}s{quota}"
                  if u else "")
        text = f"**Q. {질문}**\n\n{data['answer']}\n\n**근거 문서**\n{sources}{footer}"
        for i in range(0, len(text), 1900):  # Discord 메시지 2000자 제한
            await interaction.followup.send(text[i : i + 1900], suppress_embeds=True)
````

#### `views.py` (113줄)

**요약 임베드와 승인·취소 버튼.**

- 볼 포인트: `interaction_check` — 요청한 사람만 승인할 수 있다.
- 볼 포인트: 승인 즉시 버튼을 잠근다(중복 클릭 방지). Discord 임베드는 메시지당 10개·합계 6000자 제한이 있다.

````python
"""요약 결과 임베드 + 승인/취소 버튼."""
import logging

import discord

from config import APPROVAL_TIMEOUT
from policy_search import schedule_reindex
from storage import save_decisions
from summarizer import ThreadMessage

log = logging.getLogger("policy-bot")

STATUS_COLOR = {"제안": 0x95A5A6, "논의중": 0xF1C40F, "최종결정": 0x2ECC71}
STATUS_EMOJI = {"제안": "💡", "논의중": "💬", "최종결정": "✅"}


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_embeds(decisions: list[dict], messages: list[ThreadMessage]) -> list[discord.Embed]:
    url_by_index = {m.index: m.jump_url for m in messages}
    embeds = []
    for i, d in enumerate(decisions[:10], start=1):  # 디스코드: 메시지당 임베드 최대 10개
        e = discord.Embed(
            title=_cut(f"{STATUS_EMOJI[d['status']]} [{d['area']}] {d['topic']}", 256),
            description=_cut(d["decision"] or "(결정 내용 없음)", 2000),
            color=STATUS_COLOR[d["status"]],
        )
        e.add_field(name="상태", value=d["status"], inline=True)
        e.add_field(name="결정자", value=d["decided_by"] or "미확인", inline=True)
        e.add_field(name="적용 범위", value=_cut(d["scope"] or "미확인", 1024), inline=True)
        if d["rationale"]:
            e.add_field(name="근거", value=_cut(d["rationale"], 1024), inline=False)
        if d["external_refs"]:
            e.add_field(name="외부 참조", value=_cut("\n".join(f"• {r}" for r in d["external_refs"]), 1024), inline=False)
        if d["open_questions"]:
            qs = "\n".join(
                f"• {q['question']}" + (f" → **{q['ask_to']}**" if q["ask_to"] else "") for q in d["open_questions"]
            )
            e.add_field(name="❓ 확인 필요", value=_cut(qs, 1024), inline=False)
        if d["source_messages"]:
            links = " ".join(f"[#{n}]({url_by_index[n]})" for n in d["source_messages"] if n in url_by_index)
            if links:
                e.add_field(name="근거 메시지", value=_cut(links, 1024), inline=False)
        e.set_footer(text=f"{i}/{len(decisions)}")
        embeds.append(e)

    # 전체 임베드 6000자 제한 대응: 넘으면 뒤에서부터 줄임
    while embeds and sum(len(e) for e in embeds) > 6000:
        embeds.pop()
    return embeds


class ApprovalView(discord.ui.View):
    def __init__(self, *, owner_id: int, decisions: list[dict], source: dict):
        super().__init__(timeout=APPROVAL_TIMEOUT)
        self.owner_id = owner_id
        self.decisions = decisions
        self.source = source
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("정리를 요청한 사람만 승인/취소할 수 있어요.", ephemeral=True)
            return False
        return True

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="승인하고 저장", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 중복 클릭 방지: 먼저 버튼을 잠그고 응답을 미룸 (Notion 저장이 3초를 넘길 수 있음)
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(content="💾 저장 중…", view=self)
        try:
            results = await save_decisions(
                self.decisions, source=self.source, approved_by=interaction.user.display_name
            )
        except Exception:
            log.exception("저장 실패")
            await interaction.edit_original_response(content="❌ 로컬 저장 중 오류가 났어요. 로그를 확인해 주세요.")
            return

        lines = [f"✅ {interaction.user.mention} 님이 승인 — 결정 {len(results)}건 저장됨"]
        for r in results:
            if r.notion_url:
                lines.append(f"• [{r.record['topic']}]({r.notion_url})")
            elif r.notion_error:
                lines.append(f"• {r.record['topic']} — ⚠️ Notion 저장 실패 (로컬에는 저장됨)")
        if not any(r.notion_url or r.notion_error for r in results):
            lines.append("(Notion 미설정 — 로컬 JSON에만 저장)")
        await interaction.edit_original_response(content="\n".join(lines)[:2000])
        schedule_reindex()  # 새 결정을 검색 인덱스에 반영 (백그라운드)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(
            content="🚫 취소됨 — 저장하지 않았어요. 내용을 보완한 뒤 다시 `/정책정리` 해주세요.", view=self
        )

    async def on_timeout(self):
        self._disable_all()
        if self.message:
            try:
                await self.message.edit(content="⌛ 승인 시간이 지나 저장하지 않았어요. 다시 `/정책정리` 해주세요.", view=self)
            except discord.HTTPException:
                pass
````

#### `bot.py` (146줄)

**봇 진입점.** 로그인, 커맨드 등록, `/정책정리` 처리.

- 볼 포인트: `intents.message_content = True` — 개발자 포털에서도 켜야 메시지 내용을 읽을 수 있다.
- 볼 포인트: `collect_thread_messages` — 텍스트 채널 스레드는 첫 메시지가 부모 채널에 있다.
- 볼 포인트: `GUILD_ID`가 있으면 그 서버에만 커맨드를 즉시 등록한다(전역 등록은 반영이 느리다).

````python
"""정책 관리 디스코드 봇 — MVP.

/정책정리 : 현재 스레드의 논의를 정책 결정으로 요약 → 승인 시 저장
"""
import logging

import discord
from discord import app_commands

from config import ANTHROPIC_API_KEY, DISCORD_TOKEN, GUILD_ID, MAX_MESSAGES
from summarizer import SummaryError, ThreadMessage, summarize_thread
from policy_search import register_policy_commands
from views import ApprovalView, build_embeds

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("policy-bot")

intents = discord.Intents.default()
intents.message_content = True  # 개발자 포털에서 Message Content Intent도 켜야 함


class PolicyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("길드 %s에 커맨드 %d개 등록", GUILD_ID, len(synced))
        else:
            synced = await self.tree.sync()
            log.info("전역 커맨드 %d개 등록 (반영까지 시간 걸릴 수 있음)", len(synced))

    async def on_ready(self):
        log.info("로그인: %s (%s)", self.user, self.user.id if self.user else "-")


bot = PolicyBot()
register_policy_commands(bot.tree)  # /정책검색, /정책질문 (FastAPI 서버 필요)


def _format_content(m: discord.Message) -> str:
    parts = [m.content.strip()] if m.content.strip() else []
    parts += [f"[첨부: {a.filename}]" for a in m.attachments]
    parts += [f"[링크: {e.title or e.url}]" for e in m.embeds if e.title or e.url]
    return " ".join(parts).replace("\n", " / ")


async def collect_thread_messages(thread: discord.Thread) -> list[ThreadMessage]:
    raw: list[discord.Message] = []

    # 텍스트 채널 스레드는 시작 메시지가 부모 채널에 있음 (포럼 스레드는 history에 포함됨)
    starter = thread.starter_message
    if starter is None and isinstance(thread.parent, discord.TextChannel):
        try:
            starter = await thread.parent.fetch_message(thread.id)
        except discord.HTTPException:
            starter = None
    if starter:
        raw.append(starter)

    async for m in thread.history(limit=MAX_MESSAGES, oldest_first=True):
        if starter and m.id == starter.id:
            continue
        raw.append(m)

    result = []
    for m in raw:
        if m.author.bot:
            continue
        content = _format_content(m)
        if not content:
            continue
        result.append(
            ThreadMessage(
                index=len(result) + 1,
                author=m.author.display_name,
                created_at=m.created_at,
                content=content,
                jump_url=m.jump_url,
            )
        )
    return result


@bot.tree.command(name="정책정리", description="이 스레드의 논의를 정책 결정으로 정리해요")
async def summarize_command(interaction: discord.Interaction):
    thread = interaction.channel
    if not isinstance(thread, discord.Thread):
        await interaction.response.send_message("스레드 안에서만 쓸 수 있어요.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    messages = await collect_thread_messages(thread)
    if not messages:
        await interaction.followup.send("정리할 메시지가 없어요.")
        return

    try:
        decisions = await summarize_thread(thread.name, messages)
    except SummaryError as e:
        log.warning("요약 실패: %s", e)
        await interaction.followup.send(f"요약에 실패했어요: {e}")
        return

    if not decisions:
        await interaction.followup.send("이 스레드에서 정책 결정이나 논의를 찾지 못했어요.")
        return

    source = {
        "thread_id": str(thread.id),
        "thread_name": thread.name,
        "thread_url": thread.jump_url,
        "message_urls_by_index": {m.index: m.jump_url for m in messages},
    }
    embeds = build_embeds(decisions, messages)
    shown = len(embeds)
    note = f" (화면에는 {shown}건만 표시, 승인 시 {len(decisions)}건 모두 저장)" if shown < len(decisions) else ""

    view = ApprovalView(owner_id=interaction.user.id, decisions=decisions, source=source)
    view.message = await interaction.followup.send(
        content=f"📋 메시지 {len(messages)}개에서 결정 {len(decisions)}건을 정리했어요{note}. 확인 후 승인해 주세요.",
        embeds=embeds,
        view=view,
        wait=True,
    )


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.exception("커맨드 오류", exc_info=error)
    msg = "처리 중 오류가 났어요. 봇에 이 스레드를 읽을 권한이 있는지 확인해 주세요."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


if __name__ == "__main__":
    if not DISCORD_TOKEN or not ANTHROPIC_API_KEY:
        raise SystemExit(".env에 DISCORD_TOKEN, ANTHROPIC_API_KEY를 설정해 주세요.")
    bot.run(DISCORD_TOKEN, log_handler=None)
````

#### `setup_notion.py` (58줄)

**Notion DB 최초 생성(1회).** 클론에서는 실행하지 않아도 된다. 새 테스트 Notion 페이지를 연결해 보고 싶을 때만 쓴다.

````python
"""Notion '정책 결정 히스토리' DB를 한 번만 생성하고 .env에 ID를 기록한다.

사전 준비:
  1. https://www.notion.so/profile/integrations 에서 Internal integration 생성 → 토큰 복사
  2. DB를 넣을 빈 페이지를 만들고, 페이지 우측 상단 ··· → 연결(Connections) → 만든 integration 추가
  3. .env에 NOTION_TOKEN, NOTION_PARENT_PAGE_ID(페이지 URL 그대로 붙여도 됨) 입력

실행:
  python setup_notion.py
"""
import asyncio
import re
from pathlib import Path

import notion_store
from config import NOTION_DATA_SOURCE_ID, NOTION_PARENT_PAGE_ID, NOTION_TOKEN

ENV_PATH = Path(".env")


def update_env(values: dict[str, str]) -> None:
    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    for key, value in values.items():
        line = f"{key}={value}"
        if re.search(rf"^{key}=.*$", text, flags=re.M):
            text = re.sub(rf"^{key}=.*$", line, text, flags=re.M)
        else:
            text = text.rstrip("\n") + f"\n{line}\n"
    ENV_PATH.write_text(text, encoding="utf-8")


async def main() -> None:
    if not NOTION_TOKEN:
        raise SystemExit(".env에 NOTION_TOKEN을 넣어 주세요.")
    if not NOTION_PARENT_PAGE_ID:
        raise SystemExit(".env에 NOTION_PARENT_PAGE_ID(DB를 넣을 페이지 URL 또는 ID)를 넣어 주세요.")
    if NOTION_DATA_SOURCE_ID:
        raise SystemExit("이미 NOTION_DATA_SOURCE_ID가 있어요. 새로 만들려면 .env에서 그 줄을 지우고 다시 실행하세요.")

    parent_id = notion_store.extract_id(NOTION_PARENT_PAGE_ID)
    try:
        info = await notion_store.create_database(parent_id)
    except Exception as e:
        msg = str(e)
        if "Could not find" in msg or "object_not_found" in msg:
            raise SystemExit(
                "페이지를 찾을 수 없어요. 페이지 ··· → 연결(Connections)에서 integration을 추가했는지 확인해 주세요."
            ) from e
        raise SystemExit(f"DB 생성 실패: {msg}") from e

    update_env({"NOTION_DATABASE_ID": info["database_id"], "NOTION_DATA_SOURCE_ID": info["data_source_id"]})
    print("✅ DB 생성 완료")
    print(f"   URL: {info['url']}")
    print("   .env에 NOTION_DATABASE_ID, NOTION_DATA_SOURCE_ID를 기록했어요. 봇을 재시작하세요.")


if __name__ == "__main__":
    asyncio.run(main())
````

**체크포인트:**

1. 원본 봇을 멈춘다: `cd ~/policy-bot && docker compose stop bot`
2. 클론 폴더 터미널에서 `python bot.py` → 로그에 `길드 ...에 커맨드 3개 등록`, `로그인: ...`
3. Discord 테스트 스레드에서 `/정책정리` → 요약 임베드 → **승인하고 저장**
4. `tail -1 data/history.jsonl`에 방금 결정이 있으면 성공이다. `Ctrl+C`로 봇을 끈다.

```bash
git add -A && git commit -m "2단계: Discord 봇 + 승인 + 저장"
```

---

## 3단계 — RAG 인덱싱 (Notion/JSONL → 청킹 → 임베딩 → Chroma)

**목표:** 저장된 결정을 조각으로 나누고 벡터로 바꿔 Chroma에 넣는다. 바뀐 것만 다시 넣는다.

**배우는 것:** 원본(Notion)과 색인(Chroma) 분리, 청킹과 헤더, 임베딩 교체 구조(Protocol), 벡터 저장소 래퍼(Repository 패턴), 증분 인덱싱.

#### `app/config.py` (89줄)

**RAG·API 설정.** pydantic-settings가 `.env`를 읽어 타입까지 검증한다. 3~5단계 설정이 모두 여기 있다(검색 하한선, 리랭킹, 사용량 제한).

````python
"""RAG/API 설정. 봇과 같은 .env를 읽는다 (NOTION_TOKEN, NOTION_DATA_SOURCE_ID, ANTHROPIC_API_KEY, CLAUDE_MODEL 공유)."""
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Settings 외에 os.environ을 직접 읽는 곳(app/usage.py 단가 등)도 .env 값을 보도록.
# Docker에서는 env_file로 이미 환경변수가 들어오고, 이미 있는 값은 덮어쓰지 않는다.
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- 문서 소스 ---
    # "auto": Notion이 설정돼 있으면 Notion, 아니면 data/history.jsonl
    # "notion" | "jsonl" 로 강제 가능
    rag_source: str = "auto"
    data_dir: str = "data"

    # --- Notion (봇의 notion_store.py 스키마와 동일) ---
    notion_token: str = ""
    notion_data_source_id: str = ""
    notion_prop_title: str = "주제"
    notion_prop_area: str = "기능영역"
    notion_prop_status: str = "상태"
    notion_prop_decider: str = "결정자"
    notion_prop_decided_at: str = "승인일"
    notion_prop_scope: str = "적용범위"
    # 본문으로 쓸 속성들 (순서대로 "라벨: 값" 형태로 이어붙임)
    notion_body_props: list[str] = ["결정", "근거", "적용범위", "외부참조", "확인필요", "스레드명"]

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

    def resolved_source(self) -> str:
        if self.rag_source != "auto":
            return self.rag_source
        return "notion" if (self.notion_token and self.notion_data_source_id) else "jsonl"


@lru_cache
def get_settings() -> Settings:
    return Settings()
````

#### `app/rag/__init__.py`

`app/rag`를 패키지로 만든다.

> 빈 파일로 만들기만 하면 된다. 이 폴더를 파이썬 패키지로 인식시키는 표시다.

#### `app/rag/models.py` (31줄)

**데이터 구조 3개.** 문서(PolicyDocument) → 조각(Chunk) → 검색 결과(SearchHit). 파이프라인 단계 사이를 오가는 모양이다.

````python
"""RAG 파이프라인에서 주고받는 데이터 구조."""
from dataclasses import dataclass, field


@dataclass
class PolicyDocument:
    """Notion 페이지 하나 = 정책 문서 하나."""
    page_id: str
    title: str
    url: str
    body: str
    metadata: dict[str, str] = field(default_factory=dict)  # area, status, decider, decided_at, scope, last_edited


@dataclass
class Chunk:
    """벡터DB에 저장되는 단위."""
    chunk_id: str        # "{page_id}:{index}"
    page_id: str
    text: str            # 임베딩 대상 (헤더 + 본문 조각)
    metadata: dict[str, str]


@dataclass
class SearchHit:
    page_id: str
    title: str
    url: str
    text: str
    score: float         # 0~1, 높을수록 유사
    metadata: dict[str, str]
````

#### `app/rag/notion_loader.py` (139줄)

**로더.** Notion 페이지(또는 `history.jsonl`)를 문서로 바꾼다. 속성들을 "라벨: 값" 본문으로 합치고, 영역·상태·승인일은 메타데이터로 따로 둔다.

- 볼 포인트: `build_loader` — Notion 설정이 없으면 jsonl로 자동 전환. 클론은 지금 jsonl을 쓴다.

````python
"""[인덱싱 1] 정책 결정을 읽어 PolicyDocument로 변환.

소스 두 가지:
- NotionLoader: '정책 결정 히스토리' DB (notion-client 3.x, data source API)
- JsonlLoader : 봇이 로컬에 항상 남기는 data/history.jsonl (Notion 없이 테스트할 때)

봇은 결정 1건 = 페이지 1개로 저장하고, 결정/근거 등 핵심 내용을 '속성'에 넣는다.
그래서 본문 블록을 따로 읽지 않고 속성만으로 문서를 만든다 → 페이지당 API 호출 0회 추가.
"""
import json
from pathlib import Path

from notion_client import Client

from app.config import Settings
from app.rag.models import PolicyDocument


def _rich_text_to_str(rich: list[dict]) -> str:
    return "".join(r.get("plain_text") or r.get("text", {}).get("content", "") for r in rich)


def prop_to_str(prop: dict | None) -> str:
    """Notion 속성 타입별로 사람이 읽을 수 있는 문자열로 변환."""
    if not prop:
        return ""
    t = prop.get("type")
    v = prop.get(t)
    if t in ("title", "rich_text"):
        return _rich_text_to_str(v or [])
    if t in ("select", "status"):
        return (v or {}).get("name", "")
    if t == "multi_select":
        return ", ".join(o["name"] for o in v or [])
    if t == "date":
        return (v or {}).get("start", "") or ""
    if t == "people":
        return ", ".join(p.get("name", "") for p in v or [])
    if t in ("created_time", "last_edited_time", "url"):
        return v or ""
    return ""


def _body_from_fields(fields: list[tuple[str, str]]) -> str:
    return "\n".join(f"{label}: {value}" for label, value in fields if value and value.strip())


def page_to_document(page: dict, s: Settings) -> PolicyDocument:
    props = page["properties"]
    p = lambda name: prop_to_str(props.get(name))
    return PolicyDocument(
        page_id=page["id"],
        title=p(s.notion_prop_title) or "(제목 없음)",
        url=page.get("url", ""),
        body=_body_from_fields([(name, p(name)) for name in s.notion_body_props]),
        metadata={
            "area": p(s.notion_prop_area),
            "status": p(s.notion_prop_status),
            "decider": p(s.notion_prop_decider),
            "decided_at": p(s.notion_prop_decided_at)[:10],
            "scope": p(s.notion_prop_scope),
            "thread_url": p("스레드"),
            "last_edited": page.get("last_edited_time", ""),
        },
    )


class NotionLoader:
    def __init__(self, settings: Settings):
        self.s = settings
        self.client = Client(auth=settings.notion_token)  # 기본 API 버전 = data source 기반

    def _iter_pages(self):
        cursor = None
        while True:
            kwargs = {"data_source_id": self.s.notion_data_source_id, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            res = self.client.data_sources.query(**kwargs)
            for item in res["results"]:
                if item.get("object") == "page" and not item.get("in_trash") and not item.get("archived"):
                    yield item
            if not res.get("has_more"):
                break
            cursor = res["next_cursor"]

    def load(self) -> list[PolicyDocument]:
        return [page_to_document(page, self.s) for page in self._iter_pages()]


def record_to_document(r: dict) -> PolicyDocument:
    """storage.py가 만든 record(dict) → PolicyDocument."""
    questions = "\n".join(
        f"• {q['question']}" + (f" → {q['ask_to']}" if q.get("ask_to") else "") for q in r.get("open_questions") or []
    )
    src = r.get("source") or {}
    body = _body_from_fields([
        ("결정", r.get("decision", "")),
        ("근거", r.get("rationale", "")),
        ("적용범위", r.get("scope", "")),
        ("외부참조", "\n".join(r.get("external_refs") or [])),
        ("확인필요", questions),
        ("스레드명", src.get("thread_name", "")),
    ])
    approved_at = r.get("approved_at", "")
    return PolicyDocument(
        page_id=f"local-{r['id']}",
        title=r.get("topic") or "(제목 없음)",
        url=src.get("thread_url", ""),  # 로컬 기록은 Notion URL이 없어서 디스코드 스레드 링크 사용
        body=body,
        metadata={
            "area": r.get("area", ""),
            "status": r.get("status", ""),
            "decider": r.get("decided_by") or "",
            "decided_at": approved_at[:10],
            "scope": r.get("scope") or "",
            "thread_url": src.get("thread_url", ""),
            "last_edited": approved_at,  # append-only라 승인 시각이 곧 버전
        },
    )


class JsonlLoader:
    def __init__(self, settings: Settings):
        self.path = Path(settings.data_dir) / "history.jsonl"

    def load(self) -> list[PolicyDocument]:
        if not self.path.exists():
            return []
        docs: dict[str, PolicyDocument] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = record_to_document(json.loads(line))
                docs[d.page_id] = d  # 같은 id가 두 번 있으면 뒤의 것 사용
        return list(docs.values())


def build_loader(settings: Settings):
    return NotionLoader(settings) if settings.resolved_source() == "notion" else JsonlLoader(settings)
````

#### `app/rag/chunker.py` (56줄)

**청킹.** 800자 단위로 자르고 100자 겹친다. 조각마다 `[정책] 제목 | 영역 | 상태` 헤더를 붙인다.

- 볼 포인트: 헤더가 없으면 조각만 봐서는 어느 정책 이야기인지 모른다(contextual chunking).

````python
"""[R 단계 준비 2] 문서를 검색 단위(청크)로 자르기.

포인트: 각 청크 앞에 '제목/영역/상태/결정일' 헤더를 붙인다 (contextual chunking).
본문 조각만 임베딩하면 "이게 어느 정책 얘기인지"가 사라져서 검색 품질이 떨어진다.
"""
from app.rag.models import Chunk, PolicyDocument


def _header(doc: PolicyDocument) -> str:
    m = doc.metadata
    parts = [f"[정책] {doc.title}"]
    for label, key in (("영역", "area"), ("상태", "status"), ("결정일", "decided_at"), ("결정자", "decider"), ("적용범위", "scope")):
        if m.get(key):
            parts.append(f"{label}: {m[key]}")
    return " | ".join(parts)


def _split(text: str, size: int, overlap: int) -> list[str]:
    """문단 단위로 모으다가 size를 넘으면 자르고, 앞 조각 끝부분을 overlap만큼 이어붙인다."""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if not paragraphs:
        return []
    pieces: list[str] = []
    buf = ""
    for para in paragraphs:
        # 문단 하나가 너무 길면 강제로 자름
        while len(para) > size:
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.append(para[:size])
            para = para[size - overlap:]
        if len(buf) + len(para) + 1 > size and buf:
            pieces.append(buf)
            buf = buf[-overlap:] + "\n" + para if overlap else para
        else:
            buf = f"{buf}\n{para}" if buf else para
    if buf:
        pieces.append(buf)
    return pieces


def chunk_document(doc: PolicyDocument, size: int = 800, overlap: int = 100) -> list[Chunk]:
    header = _header(doc)
    bodies = _split(doc.body, size, overlap) or [""]  # 본문 없으면 헤더만이라도 저장
    meta = {k: v for k, v in doc.metadata.items() if v}
    meta.update({"page_id": doc.page_id, "title": doc.title, "url": doc.url})
    return [
        Chunk(
            chunk_id=f"{doc.page_id}:{i}",
            page_id=doc.page_id,
            text=f"{header}\n{body}".strip(),
            metadata=meta,
        )
        for i, body in enumerate(bodies)
    ]
````

#### `app/rag/embedder.py` (55줄)

**임베딩.** 텍스트 → 숫자 1536개. `Embedder` Protocol로 OpenAI와 로컬 모델을 바꿔 끼울 수 있다.

- 볼 포인트: Kotlin의 interface와 같다. 호출하는 쪽은 `embed_documents`, `embed_query`만 안다.
- 볼 포인트: 호출마다 `usage_log.record` — 임베딩 비용도 기록한다.

````python
"""[R 단계 준비 3] 텍스트 → 벡터. 백엔드를 교체할 수 있게 인터페이스로 분리."""
from typing import Protocol

from app import usage as usage_log
from app.config import Settings


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def _embed(self, texts: list[str], feature: str) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 100):  # 배치
            with usage_log.timer() as elapsed:
                res = self.client.embeddings.create(model=self.model, input=texts[i:i + 100])
            usage_log.record(usage_log.Usage(
                feature=feature, model=self.model, kind="embedding",
                input_tokens=getattr(res.usage, "prompt_tokens", 0) or 0, latency_ms=elapsed(),
            ))
            out.extend(d.embedding for d in res.data)
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "embed_index")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "embed_query")[0]


class LocalEmbedder:
    """multilingual-e5 계열은 'query: ' / 'passage: ' 접두어를 붙여야 성능이 나온다."""

    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode([f"passage: {t}" for t in texts], normalize_embeddings=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.model.encode(f"query: {text}", normalize_embeddings=True).tolist()


def build_embedder(s: Settings) -> Embedder:
    if s.embedding_backend == "local":
        return LocalEmbedder(s.local_embedding_model)
    return OpenAIEmbedder(s.openai_api_key, s.openai_embedding_model)
````

#### `app/rag/store.py` (51줄)

**VectorStore.** Chroma를 감싼 창구 클래스(Android의 Repository). 나머지 코드는 Chroma를 직접 모른다.

- 볼 포인트: `hnsw:space: cosine`, 점수 = 1 − 거리.
- 볼 포인트: `replace_page` — 페이지 단위로 지우고 다시 넣는다(조각 수가 바뀔 수 있어서).

````python
"""[R 단계 준비 4] 벡터DB(Chroma) 래퍼. 로컬 파일로 저장되므로 별도 서버 불필요."""
import chromadb

from app.rag.models import Chunk, SearchHit


class VectorStore:
    def __init__(self, path: str | None, collection: str):
        client = chromadb.PersistentClient(path=path) if path else chromadb.EphemeralClient()
        # cosine 거리 사용 → score = 1 - distance
        self.col = client.get_or_create_collection(collection, metadata={"hnsw:space": "cosine"})

    def replace_page(self, page_id: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """페이지가 수정되면 청크 개수가 달라질 수 있으니, 기존 청크를 지우고 다시 넣는다."""
        self.col.delete(where={"page_id": page_id})
        if not chunks:
            return
        self.col.add(
            ids=[c.chunk_id for c in chunks],
            documents=[c.text for c in chunks],
            embeddings=vectors,
            metadatas=[c.metadata for c in chunks],
        )

    def delete_missing(self, alive_page_ids: set[str]) -> int:
        """Notion에서 삭제된 페이지의 청크 정리."""
        got = self.col.get(include=["metadatas"])
        stale = [cid for cid, m in zip(got["ids"], got["metadatas"]) if m.get("page_id") not in alive_page_ids]
        if stale:
            self.col.delete(ids=stale)
        return len(stale)

    def query(self, vector: list[float], k: int, where: dict | None = None) -> list[SearchHit]:
        if self.col.count() == 0:
            return []
        res = self.col.query(
            query_embeddings=[vector],
            n_results=min(k, self.col.count()),
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
        hits: list[SearchHit] = []
        for text, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
            hits.append(SearchHit(
                page_id=meta["page_id"], title=meta.get("title", ""), url=meta.get("url", ""),
                text=text, score=round(1 - dist, 4), metadata=meta,
            ))
        return hits

    def count(self) -> int:
        return self.col.count()
````

#### `app/rag/ingest.py` (49줄)

**인덱싱 파이프라인.** 로드 → 바뀐 페이지만 청킹·임베딩 → 저장 → 지워진 페이지 정리.

````python
"""인덱싱 파이프라인: Notion → 청킹 → 임베딩 → Chroma.

last_edited 시간이 바뀐 페이지만 다시 임베딩해서 비용을 아낀다.
"""
from dataclasses import dataclass

from app.config import Settings
from app.rag.chunker import chunk_document
from app.rag.embedder import Embedder
from app.rag.models import PolicyDocument
from app.rag.store import VectorStore


@dataclass
class IngestResult:
    total_pages: int
    updated_pages: int
    skipped_pages: int
    chunks_written: int
    removed_chunks: int


def _indexed_versions(store: VectorStore) -> dict[str, str]:
    got = store.col.get(include=["metadatas"])
    return {m["page_id"]: m.get("last_edited", "") for m in got["metadatas"]}


def ingest_documents(docs: list[PolicyDocument], embedder: Embedder, store: VectorStore,
                     settings: Settings, force: bool = False) -> IngestResult:
    indexed = {} if force else _indexed_versions(store)
    updated = skipped = written = 0
    for doc in docs:
        if not force and indexed.get(doc.page_id) == doc.metadata.get("last_edited") and doc.metadata.get("last_edited"):
            skipped += 1
            continue
        chunks = chunk_document(doc, settings.chunk_size, settings.chunk_overlap)
        vectors = embedder.embed_documents([c.text for c in chunks])
        store.replace_page(doc.page_id, chunks, vectors)
        updated += 1
        written += len(chunks)
    removed = store.delete_missing({d.page_id for d in docs})
    return IngestResult(len(docs), updated, skipped, written, removed)


def ingest_from_source(settings: Settings, embedder: Embedder, store: VectorStore, force: bool = False) -> IngestResult:
    """설정에 따라 Notion 또는 data/history.jsonl에서 읽어 인덱싱."""
    from app.rag.notion_loader import build_loader
    docs = build_loader(settings).load()
    return ingest_documents(docs, embedder, store, settings, force)
````

#### `app/rag/retriever.py` (40줄)

**검색기.** 질문 임베딩 → Chroma 검색 → 필터·하한선·결정당 1개. 리랭커가 있으면 후보를 넓게 뽑아 넘긴다(5단계).

> 3단계 체크포인트(`scripts/ingest.py --search`)가 이 파일을 쓰므로 여기서 같이 친다.

````python
"""[R] Retrieval: 질문 → 관련 청크 검색. 메타데이터 필터(영역/상태)를 같이 걸 수 있다."""
from app.rag.embedder import Embedder
from app.rag.models import SearchHit
from app.rag.store import VectorStore


class Retriever:
    def __init__(self, embedder: Embedder, store: VectorStore, min_score: float = 0.0,
                 reranker=None, rerank_candidates: int = 15):
        self.embedder = embedder
        self.store = store
        # 리랭커가 있으면 벡터 검색으로 후보를 넉넉히 뽑고(rerank_candidates), 최종 순위·거절은 리랭커가 정한다
        self.reranker = reranker
        self.rerank_candidates = rerank_candidates
        # 벡터 검색은 '가장 가까운 것'을 무조건 돌려준다. 관련 없는 결과를 막으려면 하한선이 필요
        self.min_score = min_score

    def search(self, query: str, k: int = 5, area: str | None = None, status: str | None = None,
               one_per_page: bool = True) -> list[SearchHit]:
        if self.reranker:
            candidates = self._vector_search(query, self.rerank_candidates, area, status, one_per_page)
            return self.reranker.rerank(query, candidates, k)
        return self._vector_search(query, k, area, status, one_per_page)

    def _vector_search(self, query: str, k: int, area: str | None, status: str | None,
                       one_per_page: bool) -> list[SearchHit]:
        filters = [{key: val} for key, val in (("area", area), ("status", status)) if val]
        where = filters[0] if len(filters) == 1 else ({"$and": filters} if filters else None)

        # 페이지당 1개로 줄일 거라 넉넉히 가져온다
        raw = self.store.query(self.embedder.embed_query(query), k=k * 3 if one_per_page else k, where=where)
        raw = [h for h in raw if h.score >= self.min_score]
        if not one_per_page:
            return raw[:k]

        best: dict[str, SearchHit] = {}
        for h in raw:
            if h.page_id not in best or h.score > best[h.page_id].score:
                best[h.page_id] = h
        return sorted(best.values(), key=lambda h: h.score, reverse=True)[:k]
````

#### `scripts/__init__.py`

`scripts`를 패키지로 만든다(`python -m scripts.xxx`로 실행하기 위해).

> 빈 파일로 만들기만 하면 된다. 이 폴더를 파이썬 패키지로 인식시키는 표시다.

#### `scripts/ingest.py` (40줄)

**CLI 인덱싱.** API 서버 없이 인덱싱하고 바로 검색해 본다.

````python
"""CLI로 인덱싱 돌리기 (API 서버 없이).

    python -m scripts.ingest           # 바뀐 페이지만
    python -m scripts.ingest --force   # 전부 재임베딩
    python -m scripts.ingest --search "리뷰 작성 조건"   # 인덱싱 후 바로 검색 확인
"""
import argparse

from app.config import get_settings
from app.rag.embedder import build_embedder
from app.rag.ingest import ingest_from_source
from app.rag.retriever import Retriever
from app.rag.store import VectorStore


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--search", default=None)
    args = ap.parse_args()

    s = get_settings()
    embedder = build_embedder(s)
    store = VectorStore(s.chroma_path, s.chroma_collection)

    print(f"소스: {s.resolved_source()} | 임베딩: {s.embedding_backend}")
    r = ingest_from_source(s, embedder, store, force=args.force)
    print(f"페이지 {r.total_pages}개 | 갱신 {r.updated_pages} | 건너뜀 {r.skipped_pages} | "
          f"청크 {r.chunks_written}개 저장 | 삭제 {r.removed_chunks}개")

    if args.search:
        hits = Retriever(embedder, store, min_score=s.rag_min_score).search(args.search)
        if not hits:
            print(f"  (점수 {s.rag_min_score} 이상인 결과 없음)")
        for h in hits:
            print(f"  {h.score:.3f}  {h.title}  [{h.metadata.get('status', '-')}]  {h.url}")


if __name__ == "__main__":
    main()
````

**체크포인트:**

```bash
python -m scripts.ingest --search "탈퇴하면 데이터 바로 지워져?"
```

`페이지 83개 | 갱신 83 ...`이 나오고 탈퇴 관련 결정이 점수와 함께 보이면 성공이다. 한 번 더 실행하면 `갱신 0 | 건너뜀 83` — 바뀐 것만 다시 하는 증분 인덱싱이다.

```bash
git add -A && git commit -m "3단계: RAG 인덱싱"
```

---

## 4단계 — 검색·답변 API와 사용량 제한

**목표:** FastAPI 서버로 검색(`GET /policies/search`)과 답변(`POST /policies/answers`)을 제공하고, 봇이 이를 호출한다.

**배우는 것:** 생성(G) 단계 프롬프트, FastAPI 라우터·스키마·의존성 주입, REST 설계(GET vs POST, 202 + 작업 리소스), 상태 코드(401·422·429·502·503), API 키, 사용자별 제한과 월 예산.

#### `app/rag/generator.py` (58줄)

**답변 생성.** 검색된 조각을 `<context>`로 묶어 Claude에 보내고, 근거만으로 답하게 한다.

- 볼 포인트: 검색 결과가 없으면 LLM을 부르지 않는다(비용 0).
- 볼 포인트: 시스템 프롬프트의 "문서에 없으면 '문서에서 확인되지 않음'" — 환각 방지.

````python
"""[A + G] Augmentation + Generation: 검색된 청크를 프롬프트에 붙여 LLM이 답하게 한다.

시스템 프롬프트는 정책 조회 프로세스(시간순 정리 / 상태 구분 / 충돌 탐지 /
최신 결정+근거+결정자+범위 / 미확정 사항 확인 질문)를 그대로 반영했다.
"""
from dataclasses import dataclass

from anthropic import Anthropic

from app import usage as usage_log
from app.rag.models import SearchHit

SYSTEM_PROMPT = """너는 팀의 제품 정책 조회 도우미다. 반드시 <context> 안의 정책 문서만 근거로 답한다.
문서에 없는 내용은 추측하지 말고 "문서에서 확인되지 않음"이라고 쓴다.

답변 형식:
1. **최신 결론**: 가장 최근의 '최종결정' 기준 결론. 결정 근거, 결정자, 적용 범위, 결정일 포함.
2. **히스토리**: 같은 주제의 관련 정책을 시간순으로 (상태: 제안/논의중/최종결정 표시).
3. **충돌/불일치**: 문서끼리 내용이 다르면 무엇이 어떻게 다른지. 없으면 생략.
4. **확인 필요**: 최종결정이 아니거나 근거가 비어 있으면, 담당자에게 물어볼 질문을 1~3개.
각 항목 끝에 근거 문서 번호를 [1], [2]처럼 표시한다."""


NO_HITS_MESSAGE = "관련 정책 문서를 찾지 못했습니다. 키워드를 바꾸거나 영역 필터를 빼고 다시 시도해 주세요."


def build_context(hits: list[SearchHit]) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        blocks.append(f'<doc id="{i}" title="{h.title}" url="{h.url}">\n{h.text}\n</doc>')
    return "<context>\n" + "\n".join(blocks) + "\n</context>"


@dataclass
class GeneratedAnswer:
    text: str
    usage: usage_log.Usage | None = None   # 검색 결과가 없어 LLM을 안 불렀으면 None


class AnswerGenerator:
    def __init__(self, api_key: str, model: str, max_tokens: int):
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def answer(self, question: str, hits: list[SearchHit], user: str | None = None) -> GeneratedAnswer:
        if not hits:
            # 검색이 비면 LLM을 부르지 않는다 → 토큰 0 (하한선이 비용도 줄이는 이유)
            return GeneratedAnswer(NO_HITS_MESSAGE)
        with usage_log.timer() as elapsed:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"{build_context(hits)}\n\n질문: {question}"}],
            )
        u = usage_log.from_anthropic("answer", self.model, msg, elapsed(), user=user)
        return GeneratedAnswer("".join(b.text for b in msg.content if b.type == "text"), u)
````

#### `app/limits.py` (99줄)

**사용량 제한.** 사용자별 하루 답변 횟수, 월 예산 알림·차단. 별도 카운터 없이 `usage.jsonl`을 센다.

````python
"""사용량 제한: 사용자별 하루 답변 횟수 + 월 예산.

왜 필요한가 (2026-10-19 객관적 평가)
- 비용 측정과 절감은 했지만 '상한선'이 없었다. 한 사람이 /정책질문을 연달아 치거나
  스크립트가 반복 호출해도 막을 장치가 없었다. API 키는 외부인만 막는다.

기준 데이터는 data/usage.jsonl 하나다 (봇·API가 같이 기록). 별도 카운터를 두지 않아서
서버를 재시작해도 횟수가 유지되고, 실제로 돈이 나간 호출만 센다.
규모가 커지면(수만 줄 이상) 매 요청 파일 스캔이 느려지므로 그때 DB나 캐시로 옮긴다.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app import usage as usage_log
from app.config import Settings

KST = ZoneInfo("Asia/Seoul")
log = logging.getLogger("policy-api")


class LimitExceeded(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason  # "daily" | "budget"


def _kst(ts: str) -> datetime:
    return datetime.fromisoformat(ts).astimezone(KST)


def answers_today(user: str, now: datetime | None = None) -> int:
    today = (now or datetime.now(KST)).astimezone(KST).date()
    return sum(1 for r in usage_log.load(days=2)
               if r.get("feature") == "answer" and r.get("user") == user and _kst(r["ts"]).date() == today)


def month_spend(now: datetime | None = None) -> float:
    n = (now or datetime.now(KST)).astimezone(KST)
    return round(sum(r.get("cost_usd", 0.0) for r in usage_log.load(days=32)
                     if (t := _kst(r["ts"])).year == n.year and t.month == n.month), 4)


def check_answer_allowed(user: str | None, s: Settings) -> dict:
    """답변 전에 호출. 막아야 하면 LimitExceeded, 아니면 현재 사용량을 돌려준다."""
    spend = month_spend()
    if s.monthly_budget_usd and s.budget_hard_stop and spend >= s.monthly_budget_usd:
        raise LimitExceeded("budget", f"이번 달 예산(${s.monthly_budget_usd:.2f})을 모두 사용해서 답변을 멈췄어요. 관리자에게 문의해 주세요.")
    used = answers_today(user) if user else 0
    if user and s.daily_answer_limit and used >= s.daily_answer_limit:
        raise LimitExceeded("daily", f"오늘 질문 한도({s.daily_answer_limit}회)를 다 썼어요. 내일 다시 시도하거나 /정책검색을 이용해 주세요.")
    return {"used_today": used, "daily_limit": s.daily_answer_limit or None,
            "month_spend_usd": spend, "monthly_budget_usd": s.monthly_budget_usd or None}


def _alert_state_path(s: Settings) -> Path:
    return Path(s.data_dir) / "budget_alerts.json"


def maybe_alert(s: Settings, now: datetime | None = None) -> str | None:
    """예산 비율을 처음 넘었을 때 한 번만 알린다 (월·단계별). 보낸 단계를 돌려준다."""
    if not s.monthly_budget_usd:
        return None
    n = (now or datetime.now(KST)).astimezone(KST)
    spend = month_spend(n)
    ratio = round(spend / s.monthly_budget_usd, 4)  # 0.02/0.025=0.7999… 같은 부동소수점 오차 방지
    level = "100" if ratio >= 1 else ("alert" if ratio >= s.budget_alert_ratio else None)
    if not level:
        return None
    key = f"{n:%Y-%m}:{level}"
    path = _alert_state_path(s)
    try:
        sent = json.loads(path.read_text()) if path.exists() else []
    except (OSError, ValueError):
        sent = []
    if key in sent:
        return None
    msg = (f"⚠️ 정책 봇 이번 달 비용 ${spend:.2f} / 예산 ${s.monthly_budget_usd:.2f} ({ratio:.0%})"
           + (" — 예산 소진으로 답변을 멈췄어요." if level == "100" and s.budget_hard_stop else ""))
    log.warning(msg)
    if s.budget_alert_webhook_url:
        try:
            req = urllib.request.Request(s.budget_alert_webhook_url, method="POST",
                                         data=json.dumps({"content": msg}).encode(),
                                         headers={"Content-Type": "application/json", "User-Agent": "policy-bot"})
            urllib.request.urlopen(req, timeout=5).close()
        except OSError as e:  # 알림 실패가 답변을 막으면 안 된다
            log.warning("예산 알림 전송 실패: %s", e)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sent + [key]))
    except OSError:
        pass
    return level
````

#### `app/api/__init__.py`

패키지 표시.

> 빈 파일로 만들기만 하면 된다. 이 폴더를 파이썬 패키지로 인식시키는 표시다.

#### `app/api/schemas.py` (71줄)

**요청·응답 모양.** pydantic 모델이 입력 검증과 `/docs` 문서를 자동으로 만든다. Kotlin의 data class + 직렬화와 비슷하다.

````python
"""요청/응답 스키마. FastAPI가 이걸로 검증 + /docs(Swagger) 자동 생성."""
from typing import Literal

from pydantic import BaseModel, Field


class PolicyHit(BaseModel):
    page_id: str
    title: str
    url: str
    snippet: str
    score: float
    area: str | None = None
    status: str | None = None
    decided_at: str | None = None
    decider: str | None = None


class SearchResponse(BaseModel):
    query: str
    count: int
    items: list[PolicyHit]


class AnswerRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    area: str | None = None
    status: str | None = None
    k: int = Field(default=6, ge=1, le=20)


class UsageInfo(BaseModel):
    """이번 답변 생성(LLM 호출) 1회의 토큰/비용. 검색 결과가 없으면 LLM을 부르지 않아 null."""
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float


class QuotaInfo(BaseModel):
    """사용량 현황. 제한이 꺼져 있으면 해당 값은 null."""
    used_today: int
    daily_limit: int | None = None
    month_spend_usd: float
    monthly_budget_usd: float | None = None


class AnswerResponse(BaseModel):
    question: str
    answer: str
    sources: list[PolicyHit]
    usage: UsageInfo | None = None
    quota: QuotaInfo | None = None


class SyncJobCreate(BaseModel):
    force: bool = False   # True면 last_edited 무시하고 전부 재임베딩


class SyncJob(BaseModel):
    id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: str
    finished_at: str | None = None
    result: dict | None = None
    error: str | None = None


class ErrorResponse(BaseModel):
    detail: str
````

#### `app/api/deps.py` (58줄)

**의존성 주입(DI).** 라우터는 "무엇이 필요한지"만 선언하고 생성은 여기서 한다. 테스트에서 가짜 객체로 바꿔 끼울 수 있다(Hilt의 Module과 같은 역할).

- 볼 포인트: `get_reranker`는 리랭킹을 켰을 때만 import한다. 그래서 5단계 전에도 서버가 뜬다.

````python
"""의존성 주입(DI). 라우터는 '무엇이 필요한지'만 선언하고, 생성은 여기서 한 번만.
테스트에서는 app.dependency_overrides로 가짜 객체를 끼워 넣는다.
"""
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings
from app.rag.embedder import Embedder, build_embedder
from app.rag.generator import AnswerGenerator
from app.rag.retriever import Retriever
from app.rag.store import VectorStore


@lru_cache
def get_store() -> VectorStore:
    s = get_settings()
    return VectorStore(s.chroma_path, s.chroma_collection)


@lru_cache
def get_embedder() -> Embedder:
    return build_embedder(get_settings())


def get_retriever(
    embedder: Embedder = Depends(get_embedder),
    store: VectorStore = Depends(get_store),
    s: Settings = Depends(get_settings),
) -> Retriever:
    if s.rerank_enabled and s.anthropic_api_key:
        return Retriever(embedder, store, min_score=s.rerank_min_score,
                         reranker=get_reranker(s.anthropic_api_key, s.rerank_model, s.rerank_min_relevance),
                         rerank_candidates=s.rerank_candidates)
    return Retriever(embedder, store, min_score=s.rag_min_score)


@lru_cache
def get_reranker(api_key: str, model: str, min_relevance: int):
    from app.rag.reranker import LLMReranker
    return LLMReranker(api_key, model, min_relevance)


def get_generator(s: Settings = Depends(get_settings)) -> AnswerGenerator:
    if not s.anthropic_api_key:
        # 기능이 설정되지 않은 건 클라이언트 잘못이 아니므로 503
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "답변 생성이 설정되지 않았습니다 (ANTHROPIC_API_KEY 없음)")
    return AnswerGenerator(s.anthropic_api_key, s.llm_model, s.llm_max_tokens)


def require_api_key(x_api_key: str | None = Header(default=None), s: Settings = Depends(get_settings)) -> None:
    if s.api_key and x_api_key != s.api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API 키가 필요합니다 (X-API-Key 헤더)")


def require_admin(x_admin_token: str | None = Header(default=None), s: Settings = Depends(get_settings)) -> None:
    if x_admin_token != s.admin_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "관리자 토큰이 올바르지 않습니다")
````

#### `app/api/routers/__init__.py`

패키지 표시.

> 빈 파일로 만들기만 하면 된다. 이 폴더를 파이썬 패키지로 인식시키는 표시다.

#### `app/api/routers/policies.py` (83줄)

**검색·답변 엔드포인트.**

- 볼 포인트: 사용량 확인을 검색보다 먼저 한다(리랭크도 돈이 들어서).
- 볼 포인트: Claude 장애는 우리 잘못이 아니라 상위 서비스 실패라 502.

````python
"""정책 리소스 API.

RESTful 설계 메모
- GET  /policies/search : 조회는 GET + 쿼리 파라미터. 부작용 없음(안전·멱등) → 캐시 가능.
- POST /policies/answers: LLM 답변은 '새 결과물을 만드는' 행위이고 매번 결과가 달라질 수 있어
  (비멱등) GET이 아니라 POST. 질문이 길어질 수 있어 body로 받는 것도 이유.
"""
import logging

import anthropic
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.api.deps import get_generator, get_retriever, require_api_key
from app import limits
from app.api.schemas import AnswerRequest, AnswerResponse, PolicyHit, QuotaInfo, SearchResponse, UsageInfo
from app.config import Settings, get_settings
from app.rag.generator import AnswerGenerator
from app.rag.models import SearchHit
from app.rag.retriever import Retriever

log = logging.getLogger("policy-api")
router = APIRouter(prefix="/policies", tags=["policies"], dependencies=[Depends(require_api_key)])


def _to_hit(h: SearchHit, snippet_len: int = 300) -> PolicyHit:
    body = h.text.split("\n", 1)[-1]  # 헤더 줄 제외
    return PolicyHit(
        page_id=h.page_id, title=h.title, url=h.url, score=h.score,
        snippet=body[:snippet_len] + ("…" if len(body) > snippet_len else ""),
        area=h.metadata.get("area"), status=h.metadata.get("status"),
        decided_at=h.metadata.get("decided_at"), decider=h.metadata.get("decider"),
    )


@router.get("/search", response_model=SearchResponse, summary="정책 의미 검색")
def search_policies(
    q: str = Query(min_length=2, max_length=200, description="검색어 (자연어 가능)"),
    k: int = Query(5, ge=1, le=20),
    area: str | None = Query(None, description="기능영역 필터, 예: 리뷰"),
    status: str | None = Query(None, description="상태 필터, 예: 최종결정"),
    retriever: Retriever = Depends(get_retriever),
):
    hits = retriever.search(q, k=k, area=area, status=status)
    return SearchResponse(query=q, count=len(hits), items=[_to_hit(h) for h in hits])


@router.post("/answers", response_model=AnswerResponse, summary="RAG 기반 정책 질의응답")
def answer_policy_question(
    body: AnswerRequest,
    retriever: Retriever = Depends(get_retriever),
    generator: AnswerGenerator = Depends(get_generator),
    s: Settings = Depends(get_settings),
    x_user_id: str | None = Header(default=None, description="호출한 사용자 ID (봇이 Discord 사용자 ID를 넣음)"),
):
    # 검색(리랭크)도 돈이 들므로 제한 확인을 가장 먼저 한다
    try:
        quota = limits.check_answer_allowed(x_user_id, s)
    except limits.LimitExceeded as e:
        # 429 Too Many Requests: 요청 자체는 올바르지만 허용량을 넘었다는 뜻
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e)) from e

    hits = retriever.search(body.question, k=body.k, area=body.area, status=body.status)
    try:
        result = generator.answer(body.question, hits, user=x_user_id)
    except anthropic.APIError as e:
        # 우리 서버가 아니라 상위(upstream) 서비스가 실패한 것 → 502 Bad Gateway
        log.exception("Claude 호출 실패")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Claude 호출 실패: {type(e).__name__}: {e}") from e
    if result.usage:  # LLM을 실제로 불렀을 때만 횟수·비용이 늘어난다
        quota["used_today"] += 1
        quota["month_spend_usd"] = round(quota["month_spend_usd"] + result.usage.cost_usd, 4)
        limits.maybe_alert(s)
    return AnswerResponse(
        quota=QuotaInfo(**quota),
        question=body.question,
        answer=result.text,
        sources=[_to_hit(h, 150) for h in hits],
        usage=UsageInfo(
            model=result.usage.model, input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens, latency_ms=result.usage.latency_ms,
            cost_usd=result.usage.cost_usd,
        ) if result.usage else None,
    )
````

#### `app/api/routers/sync.py` (67줄)

**재인덱싱 작업.** 오래 걸리는 작업은 `POST`로 작업을 만들고 `202 Accepted` + 상태 조회 URL을 준다.

````python
"""Notion(또는 history.jsonl) → 벡터DB 동기화 '작업(job)' 리소스.

RESTful 설계 메모
- 오래 걸리는 작업은 동사형 URL(/sync, /reindex) 대신 '작업'을 리소스로 만든다.
- POST /admin/sync-jobs        → 202 Accepted + Location 헤더 (아직 안 끝났다는 뜻)
- GET  /admin/sync-jobs/{id}   → 진행 상태 폴링
- 작업 저장소는 메모리 dict라 서버 재시작 시 사라짐 (MVP 한정).
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status

from app.api.deps import get_embedder, get_store, require_admin
from app.api.schemas import SyncJob, SyncJobCreate
from app.config import Settings, get_settings
from app.rag.embedder import Embedder
from app.rag.ingest import ingest_from_source
from app.rag.store import VectorStore

router = APIRouter(prefix="/admin/sync-jobs", tags=["admin"], dependencies=[Depends(require_admin)])

_jobs: dict[str, SyncJob] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(job_id: str, force: bool, settings: Settings, embedder: Embedder, store: VectorStore) -> None:
    job = _jobs[job_id]
    job.status = "running"
    try:
        result = ingest_from_source(settings, embedder, store, force=force)
        job.result = result.__dict__
        job.status = "succeeded"
    except Exception as e:  # noqa: BLE001 — 작업 상태로 노출
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"
    finally:
        job.finished_at = _now()


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=SyncJob, summary="동기화 작업 생성")
def create_sync_job(
    body: SyncJobCreate,
    background: BackgroundTasks,
    response: Response,
    settings: Settings = Depends(get_settings),
    embedder: Embedder = Depends(get_embedder),
    store: VectorStore = Depends(get_store),
):
    if any(j.status in ("queued", "running") for j in _jobs.values()):
        raise HTTPException(status.HTTP_409_CONFLICT, "이미 진행 중인 동기화 작업이 있습니다")
    job = SyncJob(id=uuid.uuid4().hex[:12], status="queued", created_at=_now())
    _jobs[job.id] = job
    background.add_task(_run, job.id, body.force, settings, embedder, store)
    response.headers["Location"] = f"/admin/sync-jobs/{job.id}"
    return job


@router.get("/{job_id}", response_model=SyncJob, summary="동기화 작업 상태 조회")
def get_sync_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "작업을 찾을 수 없습니다")
    return job
````

#### `app/api/routers/usage.py` (25줄)

**사용량 조회(관리자).** 기능별 토큰·비용, 사용자별 오늘 횟수, 이번 달 비용.

````python
"""토큰 사용량 집계 조회 (관리자 전용).

GET /admin/usage?days=7 → 기능별(summarize/answer/embed_query/embed_index) 호출 수·토큰·비용·지연
"""
from fastapi import APIRouter, Depends, Query

from app import limits
from app import usage as usage_log
from app.api.deps import require_admin
from app.config import Settings, get_settings

router = APIRouter(prefix="/admin/usage", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("", summary="토큰 사용량/비용 집계")
def get_usage(days: int = Query(7, ge=1, le=365), s: Settings = Depends(get_settings)):
    out = usage_log.summarize(days)
    today = {}
    for r in usage_log.load(days=2):
        if r.get("feature") == "answer" and r.get("user"):
            if limits._kst(r["ts"]).date() == limits.datetime.now(limits.KST).date():
                today[r["user"]] = today.get(r["user"], 0) + 1
    out["budget"] = {"month_spend_usd": limits.month_spend(), "monthly_budget_usd": s.monthly_budget_usd or None,
                     "daily_answer_limit": s.daily_answer_limit or None, "answers_today_by_user": today}
    return out
````

#### `app/api/main.py` (25줄)

**API 진입점.** 라우터를 묶고 `/health`를 둔다.

````python
"""FastAPI 앱 진입점.

실행: uvicorn app.api.main:app --reload
문서: http://localhost:8000/docs
"""
from fastapi import Depends, FastAPI

from app.api.deps import get_store
from app.api.routers import policies, sync, usage
from app.rag.store import VectorStore

app = FastAPI(
    title="Policy Bot API",
    version="0.1.0",
    description="Discord 정책 봇용 정책 검색/질의응답 API",
)

app.include_router(policies.router)
app.include_router(sync.router)
app.include_router(usage.router)


@app.get("/health", tags=["ops"], summary="헬스체크 (Docker/Nginx에서 사용 예정)")
def health(store: VectorStore = Depends(get_store)):
    return {"status": "ok", "indexed_chunks": store.count()}
````

**체크포인트:**

1. 원본 api를 멈춘다: `cd ~/policy-bot && docker compose stop api`
2. 터미널 A: `uvicorn app.api.main:app --reload`
3. 브라우저에서 `http://localhost:8000/docs` → `GET /policies/search` → Try it out → `q`에 "북마크 폴더명" → Execute. `.env`에 `API_KEY`가 있으면 오른쪽 위 자물쇠가 없으니 401이 나온다 — 정상이다. 아래 REST Client로 키를 넣어 확인한다.
4. 프로젝트에 `api.http` 파일을 만들고 아래를 친 뒤, 각 요청 위의 **Send Request**를 누른다(REST Client 확장):

```http
@key = 여기에_.env의_API_KEY_값

GET http://localhost:8000/health

###
GET http://localhost:8000/policies/search?q=북마크 폴더명 글자수
X-API-Key: {{key}}

###
POST http://localhost:8000/policies/answers
Content-Type: application/json
X-API-Key: {{key}}
X-User-Id: me

{"question": "탈퇴하면 데이터 바로 지워져?"}
```

5. 터미널 B: `python bot.py` → Discord에서 `/정책질문 탈퇴하면 데이터 바로 지워져?` → 답변 하단에 토큰·비용·`오늘 1/30회`

> `api.http`에는 키가 들어가니 `.gitignore`에 `api.http`를 추가한다.

```bash
git add -A && git commit -m "4단계: 검색·답변 API + 사용량 제한"
```

---

## 5단계 — 리랭커

**목표:** 벡터 검색 상위 10개를 작은 모델이 질문과 함께 읽고 다시 채점한다. 순위가 정확해지고, 관련 없으면 거절한다.

**배우는 것:** 2단계 검색(1차 넓게 → 2차 꼼꼼히), tool use로 출력 형식 강제, 실패를 숨기지 않는 폴백.

#### `app/rag/reranker.py` (121줄)

**LLM 리랭커.**

- 볼 포인트: `SCORE_TOOL` + `tool_choice` — 모델이 반드시 이 도구 형식으로만 답한다. 텍스트로 "JSON만 써"라고 했을 때는 38번 중 22번 실패했다.
- 볼 포인트: `fallbacks`, `last_raw` — 폴백 횟수와 실패 원문을 남긴다. 조용한 폴백 때문에 효과를 오판할 뻔했다.

````python
"""[R 보강] LLM 리랭커: 벡터 검색 후보를 작은 모델이 '질문에 실제로 답하는 문서인가'로 다시 채점.

왜 필요한가 (기준선 측정 결과, 2026-10-19)
- Hit@5는 100%인데 Top1은 83%: 정답은 찾지만 비슷한 결정들(점수 차 0.01~0.04) 사이에서 순서를 틀린다.
- 거절 정확도 12.5%: 문서에 없는 질문도 코사인 유사도 0.3~0.45의 '그럴듯한' 결과가 나온다.
  정답 문서 최저 점수(≈0.32)와 겹쳐서 하한선만으로는 분리가 안 된다.
임베딩은 질문과 문서를 '따로' 벡터로 만들어 비교하지만, 리랭커는 질문과 문서를 '같이' 읽고 판단한다
(cross-encoder 방식). 그래서 순위와 관련 없음 판단이 더 정확한 대신 호출 비용과 지연이 든다.

채점 기준 (0~3)
  3 = 질문에 직접 답하는 결정   2 = 답의 일부/조건을 담은 결정
  1 = 같은 기능이지만 질문과 다른 내용   0 = 무관
min_relevance(기본 2) 이상만 남긴다 → 전부 미만이면 빈 결과 = 거절.
"""
from __future__ import annotations

import json
import logging
import re

from anthropic import Anthropic

from app import usage as usage_log
from app.rag.models import SearchHit

log = logging.getLogger("policy-api")

PROMPT = """질문과 정책 결정 후보 목록이 주어진다. 각 후보가 질문에 답하는 데 얼마나 쓸모 있는지 0~3점으로 채점한다.
3: 질문에 직접 답한다 / 2: 답의 일부나 조건을 담고 있다 / 1: 같은 기능이지만 질문과 다른 내용이다 / 0: 무관하다
단어가 겹쳐도 질문이 묻는 대상(화면, 기능, 상황)이 다르면 1 이하다.
모든 후보를 빠짐없이 채점해서 submit_scores 도구로 제출한다."""

# 자유 텍스트로 JSON을 받으면 모델이 설명을 붙이거나 길게 써서 잘리는 일이 생긴다(측정: 38회 중 22회 실패).
# 도구 호출을 강제하면 응답이 스키마에 맞는 구조로만 나온다.
SCORE_TOOL = {
    "name": "submit_scores",
    "description": "후보별 관련도 점수를 제출한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scores": {
                "type": "array",
                "description": "[후보번호, 점수(0~3)] 쌍의 목록",
                "items": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
            }
        },
        "required": ["scores"],
    },
}


def _body(h: SearchHit, limit: int) -> str:
    body = h.text.split("\n", 1)[-1]  # 청크 헤더 줄 제외 (제목은 따로 줌)
    return body[:limit].replace("\n", " ")


class LLMReranker:
    def __init__(self, api_key: str, model: str, min_relevance: int = 2, snippet_chars: int = 200, client=None):
        self.client = client or Anthropic(api_key=api_key)
        self.model = model
        self.min_relevance = min_relevance
        self.snippet_chars = snippet_chars
        # 파싱 실패로 벡터 순서에 기댄 횟수. 폴백이 조용히 일어나면 '리랭킹이 효과 없음'으로 오판하게 된다
        self.calls = 0
        self.fallbacks = 0
        self.last_raw: str | None = None

    def rerank(self, query: str, hits: list[SearchHit], k: int) -> list[SearchHit]:
        if not hits:
            return []
        lines = [f"[{i}] {h.title} ({h.metadata.get('status', '')}, {h.metadata.get('decided_at', '')}) — {_body(h, self.snippet_chars)}"
                 for i, h in enumerate(hits, 1)]
        with usage_log.timer() as elapsed:
            msg = self.client.messages.create(
                model=self.model, max_tokens=512, system=PROMPT,
                tools=[SCORE_TOOL], tool_choice={"type": "tool", "name": "submit_scores"},
                messages=[{"role": "user", "content": f"질문: {query}\n\n후보:\n" + "\n".join(lines)}],
            )
        usage_log.from_anthropic("rerank", self.model, msg, elapsed())
        self.calls += 1
        tool_input = next((b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        if tool_input is not None:
            scores = self._from_pairs(tool_input.get("scores"), len(hits))
            text = text or json.dumps(tool_input, ensure_ascii=False)
        else:
            scores = self._parse(text, len(hits))
        if scores is None or getattr(msg, "stop_reason", None) == "max_tokens" and not scores:
            # 파싱 실패 시 검색을 막지 않고 벡터 순서로 대체하되, 원인 분석용으로 원문을 남긴다
            self.fallbacks += 1
            self.last_raw = text
            log.warning("리랭커 응답 파싱 실패 → 벡터 순서 사용: %r", text[:200])
            return hits[:k]
        ranked = sorted(
            ((scores.get(i, 0), h) for i, h in enumerate(hits, 1)),
            key=lambda x: (x[0], x[1].score), reverse=True,  # 동점이면 벡터 점수 순
        )
        out = []
        for s, h in ranked:
            if s < self.min_relevance:
                break
            h.metadata = {**h.metadata, "rerank": s}
            out.append(h)
        return out[:k]

    @staticmethod
    def _from_pairs(pairs, n: int) -> dict[int, int] | None:
        try:
            return {int(i): int(s) for i, s in pairs if 1 <= int(i) <= n}
        except (ValueError, TypeError):
            return None

    @classmethod
    def _parse(cls, text: str, n: int) -> dict[int, int] | None:
        m = re.search(r"\[\s*\[.*\]\s*\]|\[\s*\]", text, re.DOTALL)
        if not m:
            return None
        try:
            return cls._from_pairs(json.loads(m.group(0)), n)
        except ValueError:
            return None
````

**체크포인트:** `.env`에서 `RERANK_ENABLED=true`로 바꾸고 uvicorn을 재시작(`--reload`는 `.env` 변경을 감지하지 못한다)한 뒤, `api.http`의 검색을 다시 보낸다. 결과 개수가 줄고(평균 1.5개) 문서에 없는 질문(예: "다크모드 지원해?")은 빈 결과가 나오면 성공이다. `tail -3 data/usage.jsonl`에 `"feature": "rerank"`가 보인다.

```bash
git add -A && git commit -m "5단계: 리랭커"
```

---

## 6단계 — 테스트와 골든셋 평가

**목표:** 코드가 맞게 도는지(단위 테스트)와 답이 좋은지(골든셋)를 따로 검증한다.

**배우는 것:** 가짜 객체로 외부 API 없이 테스트하기, `dependency_overrides`, Hit@k·Top1·MRR·거절 정확도, 하한선 스윕, 전후 비교.

#### `pytest.ini` (4줄)

pytest가 프로젝트 루트를 import 경로로 쓰게 한다.

````ini
[pytest]
# 프로젝트 루트를 import 경로에 추가 (app, policy_search, config 를 찾기 위해)
pythonpath = .
testpaths = tests
````

#### `tests/conftest.py` (7줄)

테스트가 `.env`의 실제 설정(API 키, 리랭킹)에 영향받지 않게 막는다.

````python
import os

# 테스트가 실제 data/usage.jsonl에 기록을 남기지 않도록 (필요한 테스트만 fixture로 켠다)
os.environ.setdefault("USAGE_LOG_DISABLED", "1")
# .env 값이 테스트에 섞이지 않도록 (load_dotenv는 이미 있는 환경변수를 덮어쓰지 않는다)
os.environ["API_KEY"] = ""
os.environ["RERANK_ENABLED"] = "false"
````

#### `tests/test_rag_api.py` (162줄)

**검색·API 테스트.** 글자 2-gram 해시로 만든 가짜 임베더와 메모리 Chroma를 쓴다. 키 없이, 0원으로, 매번 같은 결과.

````python
"""외부 API 키 없이 돌아가는 테스트. 가짜 임베더(글자 n-gram 해시) + 메모리 Chroma 사용."""
import hashlib
import math
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.main import app
from app.config import Settings
from app.rag.chunker import chunk_document
from app.rag.ingest import ingest_documents
from app.rag.models import PolicyDocument
from app.rag.retriever import Retriever
from app.rag.store import VectorStore

DIM = 256


class FakeEmbedder:
    """문자 2-gram을 해시해서 벡터로. 겹치는 단어가 많을수록 유사도가 높아진다."""

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * DIM
        t = text.replace(" ", "")
        for i in range(len(t) - 1):
            v[int(hashlib.md5(t[i:i + 2].encode()).hexdigest(), 16) % DIM] += 1
        n = math.sqrt(sum(x * x for x in v)) or 1
        return [x / n for x in v]

    def embed_documents(self, texts): return [self._vec(t) for t in texts]
    def embed_query(self, text): return self._vec(text)


class FakeGenerator:
    def answer(self, question, hits, user=None):
        from app.rag.generator import GeneratedAnswer
        from app.usage import Usage
        if not hits:
            return GeneratedAnswer("없음")
        u = Usage("answer", "fake", input_tokens=1000, output_tokens=200, latency_ms=5).compute_cost()
        return GeneratedAnswer(f"최신 결론: {hits[0].title} [1]", u)


DOCS = [
    PolicyDocument("p1", "리뷰 작성 가능 조건", "https://notion.so/p1",
                   "행사 참여 인증(발도장)을 한 사용자만 리뷰를 작성할 수 있다.\n사진은 최대 5장.",
                   {"area": "리뷰", "status": "최종결정", "decider": "PM", "decided_at": "2026-09-20", "last_edited": "t1"}),
    PolicyDocument("p2", "리뷰 신고 처리", "https://notion.so/p2",
                   "신고 3회 누적 시 리뷰를 블라인드 처리한다.",
                   {"area": "리뷰", "status": "논의중", "last_edited": "t1"}),
    PolicyDocument("p3", "북마크 정렬 기준", "https://notion.so/p3",
                   "북마크 목록은 행사 시작일 가까운 순으로 정렬한다.",
                   {"area": "북마크", "status": "제안", "last_edited": "t1"}),
]


@pytest.fixture
def setup():
    settings = Settings(admin_token="t")
    store = VectorStore(None, f"test_{uuid.uuid4().hex[:8]}")
    emb = FakeEmbedder()
    ingest_documents(DOCS, emb, store, settings)
    app.dependency_overrides[deps.get_store] = lambda: store
    app.dependency_overrides[deps.get_embedder] = lambda: emb
    app.dependency_overrides[deps.get_generator] = lambda: FakeGenerator()
    app.dependency_overrides[deps.get_settings] = lambda: Settings(_env_file=None, admin_token="t", rag_min_score=0.0)
    yield store, emb, settings
    app.dependency_overrides.clear()


def test_chunk_has_context_header():
    long_doc = PolicyDocument("x", "긴 정책", "", "\n".join(["가나다라마바사" * 20] * 10), {"area": "알림"})
    chunks = chunk_document(long_doc, size=300, overlap=50)
    assert len(chunks) > 1
    assert all(c.text.startswith("[정책] 긴 정책 | 영역: 알림") for c in chunks)
    assert chunks[0].chunk_id == "x:0"


def test_incremental_ingest_skips_unchanged(setup):
    store, emb, settings = setup
    r = ingest_documents(DOCS, emb, store, settings)
    assert (r.updated_pages, r.skipped_pages) == (0, 3)
    r = ingest_documents(DOCS[:2], emb, store, settings)  # p3 삭제됨
    assert r.removed_chunks == 1


def test_retriever_ranks_and_filters(setup):
    store, emb, _ = setup
    ret = Retriever(emb, store)
    assert ret.search("리뷰 작성 조건")[0].page_id == "p1"
    only_final = ret.search("리뷰", area="리뷰", status="최종결정")
    assert [h.page_id for h in only_final] == ["p1"]


def test_search_endpoint(setup):
    c = TestClient(app)
    r = c.get("/policies/search", params={"q": "북마크 정렬"})
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["title"] == "북마크 정렬 기준"
    assert body["items"][0]["status"] == "제안"


def test_search_validation(setup):
    assert TestClient(app).get("/policies/search", params={"q": "a"}).status_code == 422


def test_answer_endpoint(setup):
    r = TestClient(app).post("/policies/answers", json={"question": "리뷰 누가 쓸 수 있어?", "area": "리뷰"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"].startswith("최신 결론:")
    assert {s["page_id"] for s in body["sources"]} == {"p1", "p2"}  # 영역 필터로 북마크 제외
    assert body["usage"]["input_tokens"] == 1000
    assert body["usage"]["cost_usd"] == 0.006  # 1000*3/1M + 200*15/1M


def test_admin_requires_token(setup):
    assert TestClient(app).post("/admin/sync-jobs", json={}).status_code == 401


def test_health(setup):
    r = TestClient(app).get("/health")
    assert r.json() == {"status": "ok", "indexed_chunks": 3}


def test_min_score_drops_unrelated_results(setup):
    store, emb, _ = setup
    loose = Retriever(emb, store, min_score=0.0).search("오프라인 온보딩")
    strict = Retriever(emb, store, min_score=0.3).search("오프라인 온보딩")
    assert loose and all(h.score >= 0.3 for h in strict)
    assert len(strict) < len(loose)


def test_search_endpoint_applies_threshold(setup):
    app.dependency_overrides[deps.get_settings] = lambda: Settings(_env_file=None, rag_min_score=0.99)
    r = TestClient(app).get("/policies/search", params={"q": "오프라인 온보딩"})
    assert r.status_code == 200 and r.json()["count"] == 0


def test_answer_returns_502_when_claude_fails(setup):
    import anthropic
    import httpx

    class BrokenGenerator:
        def answer(self, question, hits, user=None):
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))

    app.dependency_overrides[deps.get_generator] = lambda: BrokenGenerator()
    r = TestClient(app).post("/policies/answers", json={"question": "리뷰 누가 쓸 수 있어?"})
    assert r.status_code == 502
    assert r.json()["detail"].startswith("Claude 호출 실패: APIConnectionError")


def test_policies_require_api_key_when_configured(setup):
    app.dependency_overrides[deps.get_settings] = lambda: Settings(_env_file=None, api_key="secret", rag_min_score=0.0)
    c = TestClient(app)
    assert c.get("/policies/search", params={"q": "북마크 정렬"}).status_code == 401
    assert c.get("/policies/search", params={"q": "북마크 정렬"}, headers={"X-API-Key": "secret"}).status_code == 200
    assert c.get("/health").status_code == 200  # 헬스체크는 인증 없이 (Docker healthcheck용)
````

#### `tests/test_loaders_and_bot.py` (97줄)

**로더와 봇 커맨드 등록 테스트.**

````python
"""로더(Notion 페이지 / history.jsonl)와 봇 커맨드 등록 테스트. 네트워크 없이 동작."""
import json

import discord
from discord import app_commands

from app.config import Settings
from app.rag.chunker import chunk_document
from app.rag.notion_loader import JsonlLoader, build_loader, NotionLoader, page_to_document

RECORD = {
    "id": "abc12345", "area": "회원가입·온보딩", "topic": "오프라인 실행 시 진입 플로우", "status": "최종결정",
    "decision": "세션이 있으면 탭바로 진입하고 오류 UI를 띄운다.", "rationale": "온보딩 재노출 이슈 해결",
    "decided_by": "담당자A", "scope": "앱 실행 시점", "external_refs": ["Figma 참고"],
    "open_questions": [{"question": "비로그인 오프라인은?", "ask_to": "담당자A"}],
    "source": {"thread_name": "오프라인 환경 플로우", "thread_url": "https://discord.com/channels/1/2"},
    "approved_by": "yoon", "approved_at": "2026-10-10T18:17:43+09:00",
}


def _rt(text):
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


NOTION_PAGE = {
    "object": "page", "id": "page-1", "url": "https://www.notion.so/page1", "last_edited_time": "2026-10-11T00:00:00Z",
    "properties": {
        "주제": {"type": "title", "title": _rt("리뷰 작성 조건")},
        "기능영역": {"type": "select", "select": {"name": "리뷰"}},
        "상태": {"type": "select", "select": {"name": "논의중"}},
        "결정": {"type": "rich_text", "rich_text": _rt("발도장 인증한 사용자만 작성")},
        "근거": {"type": "rich_text", "rich_text": []},
        "결정자": {"type": "rich_text", "rich_text": _rt("PM")},
        "적용범위": {"type": "rich_text", "rich_text": _rt("행사 상세 > 리뷰 탭")},
        "승인일": {"type": "date", "date": {"start": "2026-10-11T09:00:00+09:00"}},
        "스레드": {"type": "url", "url": "https://discord.com/channels/1/3"},
    },
}


def test_notion_page_to_document_uses_bot_schema():
    d = page_to_document(NOTION_PAGE, Settings())
    assert d.title == "리뷰 작성 조건"
    assert d.metadata["area"] == "리뷰" and d.metadata["status"] == "논의중"
    assert d.metadata["decided_at"] == "2026-10-11"
    assert "결정: 발도장 인증한 사용자만 작성" in d.body
    assert "근거:" not in d.body  # 빈 속성은 생략


def test_jsonl_loader_reads_bot_history(tmp_path):
    (tmp_path / "history.jsonl").write_text(json.dumps(RECORD, ensure_ascii=False) + "\n", encoding="utf-8")
    docs = JsonlLoader(Settings(data_dir=str(tmp_path))).load()
    assert len(docs) == 1
    d = docs[0]
    assert d.page_id == "local-abc12345"
    assert d.url == "https://discord.com/channels/1/2"
    assert "확인필요: • 비로그인 오프라인은? → 담당자A" in d.body
    header = chunk_document(d)[0].text.splitlines()[0]
    assert header.startswith("[정책] 오프라인 실행 시 진입 플로우 | 영역: 회원가입·온보딩 | 상태: 최종결정")


def test_source_auto_selection():
    assert isinstance(build_loader(Settings(notion_token="", notion_data_source_id="")), JsonlLoader)
    assert isinstance(build_loader(Settings(notion_token="t", notion_data_source_id="d")), NotionLoader)
    assert Settings(rag_source="jsonl", notion_token="t", notion_data_source_id="d").resolved_source() == "jsonl"


def test_claude_model_env_is_shared(monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-test-model")
    assert Settings(_env_file=None).llm_model == "claude-test-model"


def test_policy_commands_register_on_client_tree():
    from policy_search import register_policy_commands
    client = discord.Client(intents=discord.Intents.default())
    tree = app_commands.CommandTree(client)
    register_policy_commands(tree)
    names = {c.name for c in tree.get_commands()}
    assert names == {"정책검색", "정책질문"}


def test_reindex_waits_out_running_job(monkeypatch):
    import asyncio
    import policy_search as ps
    calls = []
    responses = iter([(409, {"detail": "running"}), (409, {"detail": "running"}), (202, {"id": "job1"})])

    async def fake_call(method, path, **kw):
        calls.append((method, path, kw["headers"]["X-Admin-Token"]))
        return next(responses)

    monkeypatch.setattr(ps, "_call", fake_call)
    monkeypatch.setattr(ps, "ADMIN_TOKEN", "tok")
    monkeypatch.setattr(ps, "REINDEX_INITIAL_DELAY", 0)
    monkeypatch.setattr(ps, "REINDEX_RETRY_DELAY", 0)
    asyncio.run(ps._reindex())
    assert calls == [("POST", "/admin/sync-jobs", "tok")] * 3
````

#### `app/evaluation.py` (116줄)

**평가 지표 계산(순수 함수).** 검색 지표와 답변 지표를 따로 계산한다.

````python
"""RAG 정확도 지표 계산 (순수 함수 → API 키 없이 테스트 가능).

검색(Retrieval) 지표
- Hit@k     : 정답 결정이 상위 k개 안에 하나라도 있는가 (가장 직관적인 '찾았나?')
- Recall@k  : 정답이 여러 개일 때 그중 몇 %를 찾았나
- MRR       : 정답이 몇 번째에 나왔나의 역수 평균 (1위=1.0, 2위=0.5 ...). 순위 품질
- 거절 정확도: 문서에 없는 질문에 '결과 0건'을 돌려줬나 (엉뚱한 근거로 답하는 걸 막는 능력)

답변(Generation) 지표
- 핵심어 포함률: must_include 단어가 답변에 다 들어 있나 (정답성의 값싼 근사)
- 인용률     : [1] 같은 근거 표시가 있나 (근거 기반 답변 형식 준수)
- 거절 정확도 : 없는 질문에 "확인되지 않음/찾지 못했습니다"라고 답했나
- (선택) LLM 채점: 근거 충실도 · 질문 응답성 1~5점
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

REFUSAL_MARKERS = ("확인되지 않음", "찾지 못했습니다", "찾지 못했어요", "관련 정책 문서를 찾지")
CITATION_RE = re.compile(r"\[\d+\]")


@dataclass
class GoldenItem:
    id: str
    question: str
    expected_titles: list[str] = field(default_factory=list)
    must_include: list[str] = field(default_factory=list)
    area: str | None = None

    @property
    def is_negative(self) -> bool:
        return not self.expected_titles


def load_golden(path: str | Path) -> list[GoldenItem]:
    items = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            d = json.loads(line)
            items.append(GoldenItem(d["id"], d["question"], d.get("expected_titles", []),
                                    d.get("must_include", []), d.get("area")))
    return items


def _is_relevant(title: str, item: GoldenItem) -> bool:
    return any(exp in title for exp in item.expected_titles)


def retrieval_scores(item: GoldenItem, titles: list[str]) -> dict:
    """titles: 검색 결과 제목 (점수 높은 순, 하한선 적용 후)."""
    if item.is_negative:
        return {"negative": True, "rejected": len(titles) == 0, "n_results": len(titles)}
    first_rank = next((i for i, t in enumerate(titles, 1) if _is_relevant(t, item)), None)
    found = {e for e in item.expected_titles if any(e in t for t in titles)}
    return {
        "negative": False,
        "hit": first_rank is not None,
        "top1": first_rank == 1,
        "rr": 1 / first_rank if first_rank else 0.0,
        "recall": len(found) / len(item.expected_titles),
        "n_results": len(titles),
    }


def aggregate_retrieval(rows: list[dict]) -> dict:
    pos = [r for r in rows if not r["negative"]]
    neg = [r for r in rows if r["negative"]]
    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    correct = sum(r["hit"] for r in pos) + sum(r["rejected"] for r in neg)
    return {
        "n": len(rows),
        "hit_at_k": avg([float(r["hit"]) for r in pos]),
        "top1": avg([float(r["top1"]) for r in pos]),
        "mrr": avg([r["rr"] for r in pos]),
        "recall_at_k": avg([r["recall"] for r in pos]),
        "reject_acc": avg([float(r["rejected"]) for r in neg]),
        # 정답 있는 질문은 찾고, 없는 질문은 거절해야 '맞음'
        "overall_acc": round(correct / len(rows), 3) if rows else None,
        "avg_results": avg([r["n_results"] for r in rows]),
    }


def answer_scores(item: GoldenItem, answer: str) -> dict:
    refused = any(m in answer for m in REFUSAL_MARKERS)
    if item.is_negative:
        return {"negative": True, "refused": refused}
    missing = [w for w in item.must_include if w not in answer]
    return {
        "negative": False,
        "keywords_ok": not missing,
        "missing": missing,
        "cited": bool(CITATION_RE.search(answer)),
        "wrongly_refused": refused and not CITATION_RE.search(answer),
    }


def aggregate_answers(rows: list[dict]) -> dict:
    pos = [r for r in rows if not r["negative"]]
    neg = [r for r in rows if r["negative"]]
    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
    out = {
        "keyword_acc": avg([float(r["keywords_ok"]) for r in pos]),
        "citation_rate": avg([float(r["cited"]) for r in pos]),
        "wrong_refusal_rate": avg([float(r["wrongly_refused"]) for r in pos]),
        "reject_acc": avg([float(r["refused"]) for r in neg]),
    }
    judged = [r for r in pos if "faithfulness" in r]
    if judged:
        out["judge_faithfulness"] = avg([r["faithfulness"] for r in judged])
        out["judge_relevance"] = avg([r["relevance"] for r in judged])
    return out
````

#### `tests/test_usage_and_eval.py` (143줄)

**토큰 기록·지표·리랭커 테스트.** 리랭커는 가짜 LLM 클라이언트로 '설명만 쓰는 모델', '도구로 답하는 모델'을 흉내 낸다.

````python
"""토큰 기록/집계, 평가 지표 계산 테스트 (API 키 불필요)."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import usage as usage_log
from app.api import deps
from app.api.main import app
from app.config import Settings
from app.evaluation import (GoldenItem, aggregate_answers, aggregate_retrieval, answer_scores,
                            retrieval_scores)


@pytest.fixture
def usage_file(tmp_path, monkeypatch):
    p = tmp_path / "usage.jsonl"
    monkeypatch.setenv("USAGE_LOG_PATH", str(p))
    monkeypatch.delenv("USAGE_LOG_DISABLED", raising=False)
    return p


def test_cost_uses_price_table(monkeypatch):
    monkeypatch.setenv("PRICE_LLM_INPUT_PER_MTOK", "1")
    monkeypatch.setenv("PRICE_LLM_OUTPUT_PER_MTOK", "10")
    u = usage_log.Usage("answer", "m", input_tokens=2_000_000, output_tokens=100_000).compute_cost()
    assert u.cost_usd == 3.0


def test_from_anthropic_records_cache_tokens(usage_file):
    resp = SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=50,
                                                 cache_read_input_tokens=1000, cache_creation_input_tokens=0))
    u = usage_log.from_anthropic("summarize", "m", resp, latency_ms=12)
    assert (u.input_tokens, u.output_tokens, u.cache_read_tokens) == (100, 50, 1000)
    assert usage_file.read_text().count("\n") == 1


def test_summarize_groups_by_feature(usage_file):
    for feat, i, o in [("answer", 1000, 100), ("answer", 3000, 300), ("embed_query", 20, 0)]:
        usage_log.record(usage_log.Usage(feat, "m", kind="embedding" if feat.startswith("embed") else "llm",
                                         input_tokens=i, output_tokens=o, latency_ms=10))
    s = usage_log.summarize(days=1)
    assert s["total_calls"] == 3
    assert s["features"]["answer"]["avg_input_tokens"] == 2000
    assert s["features"]["embed_query"]["calls"] == 1


def test_usage_endpoint_requires_admin(usage_file):
    app.dependency_overrides[deps.get_settings] = lambda: Settings(_env_file=None, admin_token="t")
    try:
        c = TestClient(app)
        assert c.get("/admin/usage").status_code == 401
        r = c.get("/admin/usage", headers={"X-Admin-Token": "t"})
        assert r.status_code == 200 and r.json()["total_calls"] == 0
    finally:
        app.dependency_overrides.clear()


POS = GoldenItem("q", "질문", expected_titles=["주소 검색"], must_include=["도로명"])
NEG = GoldenItem("n", "없는 질문")


def test_retrieval_metrics():
    rows = [
        retrieval_scores(POS, ["좌표 조회", "행사 등록 주소 검색 방식"]),  # 2위에서 찾음
        retrieval_scores(POS, ["무관"]),                                 # 못 찾음
        retrieval_scores(NEG, []),                                      # 올바르게 거절
        retrieval_scores(NEG, ["무관"]),                                 # 엉뚱한 결과 반환
    ]
    a = aggregate_retrieval(rows)
    assert a["hit_at_k"] == 0.5 and a["mrr"] == 0.25 and a["top1"] == 0.0
    assert a["reject_acc"] == 0.5 and a["overall_acc"] == 0.5


def test_answer_metrics():
    good = answer_scores(POS, "최신 결론: 도로명주소 API 사용 [1]")
    bad = answer_scores(POS, "구글 API를 쓴다")
    refused = answer_scores(NEG, "문서에서 확인되지 않음")
    a = aggregate_answers([good, bad, refused])
    assert good["keywords_ok"] and good["cited"]
    assert bad["missing"] == ["도로명"]
    assert a["keyword_acc"] == 0.5 and a["citation_rate"] == 0.5 and a["reject_acc"] == 1.0


# --- 리랭커 ---
from app.rag.models import SearchHit
from app.rag.reranker import LLMReranker


class _FakeMsgs:
    def __init__(self, text): self.text = text
    def create(self, **kw):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)],
                               usage=SimpleNamespace(input_tokens=500, output_tokens=20))


def _hits():
    return [SearchHit(f"p{i}", f"결정{i}", "", f"[정책] 결정{i}\n본문{i}", 0.5 - i * 0.01, {}) for i in range(1, 5)]


def test_reranker_reorders_and_drops_irrelevant():
    r = LLMReranker("k", "m", client=SimpleNamespace(messages=_FakeMsgs("[[1, 1], [2, 3], [3, 2], [4, 0]]")))
    out = r.rerank("q", _hits(), k=5)
    assert [h.page_id for h in out] == ["p2", "p3"]       # 3점 → 2점, 1점 이하는 버림
    assert out[0].metadata["rerank"] == 3


def test_reranker_rejects_when_nothing_relevant():
    r = LLMReranker("k", "m", client=SimpleNamespace(messages=_FakeMsgs("[[1, 1], [2, 0]]")))
    assert r.rerank("q", _hits(), k=5) == []


def test_reranker_falls_back_to_vector_order_on_bad_output():
    r = LLMReranker("k", "m", client=SimpleNamespace(messages=_FakeMsgs("모르겠어요")))
    assert [h.page_id for h in r.rerank("q", _hits(), k=2)] == ["p1", "p2"]


def test_small_model_uses_its_own_price():
    big = usage_log.Usage("answer", "claude-sonnet-5-5", input_tokens=1_000_000).compute_cost()
    small = usage_log.Usage("rerank", "claude-haiku-5-5", input_tokens=1_000_000).compute_cost()
    assert big.cost_usd == 3.0 and small.cost_usd == 1.0


def test_reranker_counts_fallbacks():
    r = LLMReranker("k", "m", client=SimpleNamespace(messages=_FakeMsgs("1번은 관련 있고...")))
    r.rerank("q", _hits(), k=2)
    assert (r.calls, r.fallbacks) == (1, 1) and r.last_raw.startswith("1번")


class _FakeToolMsgs:
    def __init__(self, pairs): self.pairs = pairs; self.kw = None
    def create(self, **kw):
        self.kw = kw
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"scores": self.pairs})],
                               usage=SimpleNamespace(input_tokens=500, output_tokens=40), stop_reason="tool_use")


def test_reranker_uses_forced_tool_output():
    msgs = _FakeToolMsgs([[1, 0], [2, 2], [3, 3], [4, 1]])
    r = LLMReranker("k", "m", client=SimpleNamespace(messages=msgs))
    assert [h.page_id for h in r.rerank("q", _hits(), k=5)] == ["p3", "p2"]
    assert msgs.kw["tool_choice"] == {"type": "tool", "name": "submit_scores"}
    assert r.fallbacks == 0
````

#### `tests/test_limits.py` (71줄)

**사용량 제한 테스트.** 하루 제한 429, 예산 차단, 알림이 한 번만 가는지.

````python
"""사용자별 일일 제한, 월 예산 차단, 예산 알림 1회."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import limits
from app import usage as usage_log
from app.api import deps
from app.api.main import app
from app.config import Settings
from tests.test_rag_api import FakeGenerator, setup  # noqa: F401  (fixture 재사용)


@pytest.fixture
def usage_file(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_path / "usage.jsonl"))
    monkeypatch.delenv("USAGE_LOG_DISABLED", raising=False)
    return tmp_path


def _answer(user, cost=0.01, ts=None):
    u = usage_log.Usage("answer", "m", user=user)
    u.compute_cost()
    if ts:
        u.ts = ts
    u.cost_usd = cost
    # compute_cost를 다시 부르지 않도록 직접 기록
    import json
    with usage_log.usage_path().open("a") as f:
        f.write(json.dumps(u.__dict__) + "\n")


def test_answers_today_counts_only_that_user_today(usage_file):
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1, hours=1)).isoformat()
    _answer("a"); _answer("a"); _answer("b"); _answer("a", ts=yesterday)
    assert limits.answers_today("a") == 2 and limits.answers_today("b") == 1


def test_daily_limit_and_budget(usage_file):
    _answer("a"); _answer("a")
    s = Settings(_env_file=None, daily_answer_limit=2)
    with pytest.raises(limits.LimitExceeded) as e:
        limits.check_answer_allowed("a", s)
    assert e.value.reason == "daily"
    assert limits.check_answer_allowed("b", s)["used_today"] == 0
    s2 = Settings(_env_file=None, daily_answer_limit=0, monthly_budget_usd=0.02)
    with pytest.raises(limits.LimitExceeded) as e:
        limits.check_answer_allowed("b", s2)
    assert e.value.reason == "budget"


def test_budget_alert_sent_once_per_level(usage_file):
    s = Settings(_env_file=None, monthly_budget_usd=0.025, data_dir=str(usage_file))
    _answer("a")                       # $0.01 → 40%
    assert limits.maybe_alert(s) is None
    _answer("a")                       # $0.02 → 80%
    assert limits.maybe_alert(s) == "alert"
    assert limits.maybe_alert(s) is None   # 같은 달 같은 단계는 한 번만
    _answer("a")                       # $0.03 → 120%
    assert limits.maybe_alert(s) == "100"


def test_answer_endpoint_returns_429_over_daily_limit(setup, usage_file):  # noqa: F811
    _answer("u1")
    app.dependency_overrides[deps.get_settings] = lambda: Settings(_env_file=None, rag_min_score=0.0, daily_answer_limit=1)
    c = TestClient(app)
    r = c.post("/policies/answers", json={"question": "리뷰 누가 쓸 수 있어?"}, headers={"X-User-Id": "u1"})
    assert r.status_code == 429
    r = c.post("/policies/answers", json={"question": "리뷰 누가 쓸 수 있어?"}, headers={"X-User-Id": "u2"})
    assert r.status_code == 200 and r.json()["quota"]["used_today"] == 1
````

#### `scripts/eval_rag.py` (181줄)

**골든셋 평가 실행.** `--sweep`(하한선별 비교), `--answers`(답변 평가 + 1회당 비용), `--rerank`(리랭킹 켠 비교).

````python
"""RAG 정확도 + 토큰 비용 평가.

    python -m scripts.eval_rag                    # 검색만
    python -m scripts.eval_rag --sweep            # 하한선별 비교 → RAG_MIN_SCORE 고르기
    python -m scripts.eval_rag --answers          # 답변 생성까지
    python -m scripts.eval_rag --answers --judge  # + LLM 채점
    python -m scripts.eval_rag --k 3 --min-score 0.35   # 설정 바꿔서 실험

먼저 인덱스가 있어야 한다 (python -m scripts.ingest). 결과는 eval/results/에 저장.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic

from app import usage as usage_log
from app.config import get_settings
from app.evaluation import (aggregate_answers, aggregate_retrieval, answer_scores, load_golden,
                            retrieval_scores)
from app.rag.embedder import build_embedder
from app.rag.generator import AnswerGenerator, build_context
from app.rag.retriever import Retriever
from app.rag.store import VectorStore

JUDGE_PROMPT = """너는 RAG 답변 채점자다. <context>의 문서와 <answer>를 비교해 JSON만 출력한다.
- faithfulness (1~5): 답변의 모든 주장이 context에 근거하는가. 근거 없는 내용이 하나라도 있으면 3 이하.
- relevance (1~5): 질문에 직접 답했는가.
- reason: 한 문장.
출력 예: {"faithfulness": 5, "relevance": 4, "reason": "..."}"""

SWEEP = [0.0, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5]


def judge(client: Anthropic, model: str, question: str, hits, answer: str) -> dict:
    with usage_log.timer() as elapsed:
        msg = client.messages.create(
            model=model, max_tokens=300, system=JUDGE_PROMPT,
            messages=[{"role": "user", "content": f"{build_context(hits)}\n\n<question>{question}</question>\n<answer>{answer}</answer>"}],
        )
    usage_log.from_anthropic("judge", model, msg, elapsed())
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        d = json.loads(text[text.find("{"): text.rfind("}") + 1])
        return {"faithfulness": int(d["faithfulness"]), "relevance": int(d["relevance"]), "reason": d.get("reason", "")}
    except (ValueError, KeyError):
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default="eval/golden.jsonl")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--min-score", type=float, default=None, help="기본값: .env의 RAG_MIN_SCORE")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--answers", action="store_true")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--rerank", action="store_true", help="LLM 리랭커를 켜고 평가 (RERANK_* 설정 사용)")
    args = ap.parse_args()

    s = get_settings()
    min_score = s.rag_min_score if args.min_score is None else args.min_score
    items = load_golden(args.golden)
    store = VectorStore(s.chroma_path, s.chroma_collection)
    if store.count() == 0:
        raise SystemExit("인덱스가 비어 있어요. 먼저 python -m scripts.ingest 를 실행하세요.")
    if args.rerank:
        if not s.anthropic_api_key:
            raise SystemExit("리랭킹에는 ANTHROPIC_API_KEY가 필요해요.")
        from app.rag.reranker import LLMReranker
        # 리랭커가 순위와 거절을 정하므로 벡터 하한선은 후보를 넓게 받는 용도(rerank_min_score)로만 쓰고,
        # 아래 공통 하한선 적용은 끈다
        retriever = Retriever(build_embedder(s), store, min_score=s.rerank_min_score,
                              reranker=LLMReranker(s.anthropic_api_key, s.rerank_model, s.rerank_min_relevance),
                              rerank_candidates=s.rerank_candidates)
        min_score = 0.0
        args.sweep = False
    else:
        # 하한선은 아래에서 직접 적용 (sweep 때 임베딩을 한 번만 하려고)
        retriever = Retriever(build_embedder(s), store, min_score=0.0)
    started = datetime.now(timezone.utc)

    raw = {it.id: retriever.search(it.question, k=args.k, area=it.area) for it in items}

    def run_retrieval(th: float):
        rows = []
        for it in items:
            hits = [h for h in raw[it.id] if h.score >= th]
            r = retrieval_scores(it, [h.title for h in hits])
            r.update(id=it.id, question=it.question, results=[(h.title, h.score) for h in hits])
            rows.append(r)
        return rows

    rows = run_retrieval(min_score)
    rr = getattr(retriever, "reranker", None)
    if rr:
        print(f"리랭커 호출 {rr.calls}회 중 파싱 실패(벡터 순서로 대체) {rr.fallbacks}회")
        if rr.last_raw:
            print(f"  마지막 실패 응답: {rr.last_raw[:300]!r}")
    report = {"run_at": started.isoformat(), "k": args.k, "min_score": min_score, "rerank": args.rerank,
              "rerank_fallbacks": rr.fallbacks if rr else None,
              "rerank_last_failed_raw": rr.last_raw if rr else None,
              "rerank_model": s.rerank_model if args.rerank else None,
              "embedding": s.embedding_backend, "llm_model": s.llm_model,
              "retrieval": aggregate_retrieval(rows), "items": rows}

    mode = f"리랭킹 {s.rerank_model}, 후보 {s.rerank_candidates}" if args.rerank else f"min_score={min_score}"
    print(f"\n=== 검색 평가 (k={args.k}, {mode}, {len(items)}문항) ===")
    for r in rows:
        mark = ("✅" if r["rejected"] else "❌") if r["negative"] else ("✅" if r["hit"] else "❌")
        top = ", ".join(f"{t[:18]}({sc:.2f})" for t, sc in r["results"][:3]) or "(결과 없음)"
        print(f"{mark} {r['id']} {r['question'][:28]:<28} → {top}")
    print(json.dumps(report["retrieval"], ensure_ascii=False, indent=2))

    if args.sweep:
        print("\n=== 하한선 스윕 ===\n min_score  hit@k   top1   MRR  거절정확도  전체정확도  평균결과수")
        sweep = {}
        for th in SWEEP:
            a = aggregate_retrieval(run_retrieval(th))
            sweep[th] = a
            print(f"   {th:<6}   {a['hit_at_k']!s:<6} {a['top1']!s:<6} {a['mrr']!s:<6} "
                  f"{a['reject_acc']!s:<10} {a['overall_acc']!s:<10} {a['avg_results']}")
        report["sweep"] = sweep
        # 점수가 정답/오답을 얼마나 잘 가르는지 보려면 이 두 분포를 비교
        pos_scores = [h.score for it in items if not it.is_negative for h in raw[it.id]
                      if any(e in h.title for e in it.expected_titles)]
        neg_top = [raw[it.id][0].score for it in items if it.is_negative and raw[it.id]]
        if pos_scores and neg_top:
            print(f"\n정답 문서 점수 최저 {min(pos_scores):.3f} / 없는 질문의 1위 점수 최고 {max(neg_top):.3f}"
                  f"  → 이 사이에 하한선을 두면 둘 다 맞출 수 있음" if min(pos_scores) > max(neg_top)
                  else f"\n정답 최저 {min(pos_scores):.3f} ≤ 오답 최고 {max(neg_top):.3f} → 하한선만으로는 분리 불가 (리랭킹/하이브리드 검색 필요)")

    if args.answers:
        if not s.anthropic_api_key:
            raise SystemExit("ANTHROPIC_API_KEY가 필요해요.")
        gen = AnswerGenerator(s.anthropic_api_key, s.llm_model, s.llm_max_tokens)
        client = Anthropic(api_key=s.anthropic_api_key) if args.judge else None
        ans_rows = []
        print("\n=== 답변 평가 ===")
        for it in items:
            hits = [h for h in raw[it.id] if h.score >= min_score]
            res = gen.answer(it.question, hits)
            a = answer_scores(it, res.text)
            if args.judge and hits and not it.is_negative:
                a.update(judge(client, s.llm_model, it.question, hits, res.text))
            a.update(id=it.id, answer=res.text,
                     tokens=(res.usage.input_tokens, res.usage.output_tokens) if res.usage else (0, 0))
            ans_rows.append(a)
            ok = a["refused"] if a["negative"] else a["keywords_ok"] and a["cited"]
            extra = f" 누락={a['missing']}" if a.get("missing") else ""
            extra += f" 채점={a['faithfulness']}/{a['relevance']}" if "faithfulness" in a else ""
            print(f"{'✅' if ok else '❌'} {it.id} tokens in/out={a['tokens'][0]}/{a['tokens'][1]}{extra}")
        report["answers"] = aggregate_answers(ans_rows)
        report["answer_items"] = ans_rows
        print(json.dumps(report["answers"], ensure_ascii=False, indent=2))

    # 이번 실행에서 쓴 토큰/비용 (usage.jsonl에서 실행 시작 이후 기록만)
    spent = [r for r in usage_log.load() if r["ts"] >= started.isoformat()]
    cost = {}
    for r in spent:
        c = cost.setdefault(r["feature"], {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        c["calls"] += 1
        c["input_tokens"] += r["input_tokens"]
        c["output_tokens"] += r["output_tokens"]
        c["cost_usd"] = round(c["cost_usd"] + r["cost_usd"], 6)
    report["cost"] = cost
    report["cost_total_usd"] = round(sum(c["cost_usd"] for c in cost.values()), 6)
    if args.answers:
        answered = cost.get("answer", {}).get("calls", 0)
        report["cost_per_answer_usd"] = round(cost.get("answer", {}).get("cost_usd", 0) / answered, 6) if answered else 0
    print(f"\n이번 실행 비용: ${report['cost_total_usd']:.4f}  {json.dumps(cost, ensure_ascii=False)}")

    out = Path("eval/results") / f"{started.astimezone().strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
````

#### `eval/golden.example.jsonl` (3줄)

**골든셋 형식 예시.** 실제 38문항은 원본에서 복사한다(팀 정책 내용이라 git에 올리지 않음).

````json
{"id": "q01", "question": "인터넷 없이 앱 켜면 어디로 가?", "expected_titles": ["오프라인 상태에서 앱 실행 시 진입 플로우"], "must_include": ["탭바"]}
{"id": "q02", "question": "북마크 폴더 이름 최대 몇 글자야?", "expected_titles": ["폴더명 글자수 제한"], "must_include": ["18"]}
{"id": "n01", "question": "리뷰 사진은 몇 장까지 올릴 수 있어?", "expected_titles": [], "must_include": []}
````

**체크포인트:**

```bash
python -m pytest -q                              # 34 passed
cp ~/policy-bot/eval/golden.jsonl eval/
python -m scripts.eval_rag --sweep               # 검색 지표 + 하한선 스윕 (거의 0원)
python -m scripts.eval_rag --rerank              # 리랭킹 켠 상태 비교 (약 $0.15)
```

어제 숫자(Top1 83% → 97~100%, 거절 12.5% → 87.5%)와 비슷하게 나오면 성공이다. VS Code 왼쪽 **Testing**(비커 아이콘)에서 pytest를 연결하면 테스트 하나씩 실행·디버깅할 수 있다.

```bash
git add -A && git commit -m "6단계: 테스트 + 골든셋 평가"
```

---

## 7단계 (선택) — 지난 스레드 일괄 정리

#### `scripts/batch_summarize.py` (284줄)

**일괄 요약.** 링크 목록 → 읽기(REST만, 흔적 없음) → 요약 → 검토 파일 → `--commit`으로 저장.

- 볼 포인트: `client.login`만 하고 `connect`는 안 한다 — 게이트웨이에 안 붙어 온라인 표시도 안 된다.
- 볼 포인트: `snowflake_time` — Discord ID에 들어 있는 생성 시각으로 승인일을 복원한다.

````python
"""여러 스레드를 한 번에 /정책정리 하는 스크립트 (초기 데이터 채우기용).

    # 1) 읽기 + 요약 → 검토 파일 생성 (아무것도 저장하지 않음)
    python -m scripts.batch_summarize links.txt
    # 2) 검토 파일에서 뺄 것만 "include": false 로 바꾼 뒤 저장
    python -m scripts.batch_summarize --commit data/batch/review-<시각>.json

links.txt: 한 줄에 링크 하나. '#'으로 시작하는 줄은 무시.
  - 스레드 링크  https://discord.com/channels/<서버>/<스레드>
  - 메시지 링크  https://discord.com/channels/<서버>/<스레드>/<메시지>  (스레드로 취급)
  - 포럼/채널 링크 → 그 채널의 스레드 전부 (진행중 + 보관된 것)

Discord에 남는 흔적 없음:
  - 게이트웨이에 접속하지 않고 REST로 '읽기'만 한다 (메시지 전송·반응·입력중 표시·온라인 표시 없음)
  - 스레드에 참여(join)하지 않는다. 봇이 멤버가 아닌 비공개 스레드는 읽지 못하고 '건너뜀'으로 보고만 한다
요약은 /정책정리와 같은 프롬프트(summarizer.py)를 쓰므로 결과 품질이 같다.
"""
import argparse
import asyncio
import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import discord

from config import ANTHROPIC_API_KEY, DATA_DIR, DISCORD_TOKEN, KST, MAX_MESSAGES
from summarizer import SummaryError, ThreadMessage, summarize_thread

LINK_RE = re.compile(r"discord(?:app)?\.com/channels/(\d+)/(\d+)(?:/(\d+))?")
CONCURRENCY = 3  # 동시에 요약할 스레드 수 (Claude 레이트리밋 고려)


def _format_content(m: discord.Message) -> str:  # bot.py와 동일 규칙
    parts = [m.content.strip()] if m.content.strip() else []
    parts += [f"[첨부: {a.filename}]" for a in m.attachments]
    parts += [f"[링크: {e.title or e.url}]" for e in m.embeds if e.title or e.url]
    return " ".join(parts).replace("\n", " / ")


def parse_links(path: str) -> list[tuple[int, int]]:
    out, seen = [], set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = LINK_RE.search(line)
        if not m:
            print(f"⚠️  링크 형식 아님, 건너뜀: {line}")
            continue
        key = (int(m.group(1)), int(m.group(2)))
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def already_saved_thread_ids() -> set[str]:
    p = Path(DATA_DIR) / "history.jsonl"
    if not p.exists():
        return set()
    ids = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            ids.add(str(json.loads(line)["source"]["thread_id"]))
        except (ValueError, KeyError, TypeError):
            pass
    return ids


async def expand_channel(client: discord.Client, guild_id: int, ch) -> list[discord.Thread]:
    """포럼/텍스트 채널 → 그 안의 스레드 (진행중 + 보관)."""
    threads: dict[int, discord.Thread] = {}
    guild = await client.fetch_guild(guild_id)
    for t in await guild.active_threads():
        if t.parent_id == ch.id:
            threads[t.id] = t
    try:
        async for t in ch.archived_threads(limit=None):
            threads[t.id] = t
    except discord.Forbidden:
        print(f"⚠️  #{ch.name}: 보관된 스레드 목록 권한 없음 (Read Message History 확인)")
    return list(threads.values())


async def collect(client: discord.Client, thread: discord.Thread) -> list[ThreadMessage]:
    raw: list[discord.Message] = []
    # 텍스트 채널 스레드는 시작 메시지가 부모 채널에 있다 (포럼은 history에 포함)
    try:
        parent = await client.fetch_channel(thread.parent_id)
        if isinstance(parent, discord.TextChannel):
            raw.append(await parent.fetch_message(thread.id))
    except discord.HTTPException:
        pass
    starter_ids = {m.id for m in raw}
    async for m in thread.history(limit=MAX_MESSAGES, oldest_first=True):
        if m.id not in starter_ids:
            raw.append(m)

    result = []
    for m in raw:
        if m.author.bot:
            continue
        content = _format_content(m)
        if content:
            result.append(ThreadMessage(len(result) + 1, m.author.display_name, m.created_at, content, m.jump_url))
    return result


async def read_and_summarize(links_path: str, force: bool, limit: int | None) -> None:
    targets = parse_links(links_path)
    saved = set() if force else already_saved_thread_ids()
    client = discord.Client(intents=discord.Intents.none())
    await client.login(DISCORD_TOKEN)  # REST 인증만. connect()를 안 하므로 온라인 표시도 안 됨
    try:
        threads: list[discord.Thread] = []
        report: list[dict] = []
        for guild_id, ch_id in targets:
            try:
                ch = await client.fetch_channel(ch_id)
            except discord.Forbidden:
                report.append({"id": ch_id, "skip": "권한 없음 (비공개 스레드면 봇이 멤버가 아님)"})
                continue
            except discord.NotFound:
                report.append({"id": ch_id, "skip": "찾을 수 없음 (링크 확인)"})
                continue
            if isinstance(ch, discord.Thread):
                threads.append(ch)
            elif isinstance(ch, (discord.ForumChannel, discord.TextChannel)):
                found = await expand_channel(client, guild_id, ch)
                print(f"📂 #{ch.name}: 스레드 {len(found)}개")
                threads.extend(found)
            else:
                report.append({"id": ch_id, "skip": f"스레드/채널이 아님 ({type(ch).__name__})"})

        uniq = {t.id: t for t in threads}
        todo = [t for t in uniq.values() if str(t.id) not in saved]
        for t in uniq.values():
            if str(t.id) in saved:
                report.append({"id": t.id, "name": t.name, "skip": "이미 저장된 스레드 (--force로 다시)"})
        if limit:
            todo = todo[:limit]
        print(f"🧵 처리할 스레드 {len(todo)}개 (건너뜀 {len(report)}개)")

        sem = asyncio.Semaphore(CONCURRENCY)
        results: list[dict] = []

        async def one(t: discord.Thread):
            async with sem:
                try:
                    msgs = await collect(client, t)
                except discord.Forbidden:
                    report.append({"id": t.id, "name": t.name, "skip": "메시지 읽기 권한 없음 (비공개 스레드?)"})
                    return
                if not msgs:
                    report.append({"id": t.id, "name": t.name, "skip": "사람이 쓴 메시지 없음"})
                    return
                try:
                    decisions = await summarize_thread(t.name, msgs)
                except SummaryError as e:
                    report.append({"id": t.id, "name": t.name, "skip": f"요약 실패: {e}"})
                    if e.raw:  # 왜 깨졌는지 볼 수 있게 원문 저장
                        fail_dir = Path(DATA_DIR) / "batch" / "failed"
                        fail_dir.mkdir(parents=True, exist_ok=True)
                        (fail_dir / f"{t.id}.txt").write_text(e.raw, encoding="utf-8")
                    return
                for d in decisions:
                    d["include"] = True
                results.append({
                    "thread_name": t.name,
                    "message_count": len(msgs),
                    # 저장 시 승인일로 쓴다. 오늘 날짜로 넣으면 과거 결정이 전부 '최신'이 되어 시간순이 깨짐
                    "last_message_at": msgs[-1].created_at.isoformat(),
                    "source": {
                        "thread_id": str(t.id),
                        "thread_name": t.name,
                        "thread_url": t.jump_url,
                        "message_urls_by_index": {m.index: m.jump_url for m in msgs},
                    },
                    "decisions": decisions,
                })
                print(f"  ✓ {t.name[:40]} — 메시지 {len(msgs)}개 → 결정 {len(decisions)}건")

        await asyncio.gather(*(one(t) for t in todo))
    finally:
        await client.close()

    results.sort(key=lambda r: r["thread_name"])
    out_dir = Path(DATA_DIR) / "batch"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
    review = out_dir / f"review-{stamp}.json"
    review.write_text(json.dumps({"threads": results, "skipped": report}, ensure_ascii=False, indent=2), encoding="utf-8")

    # 사람이 훑어보기 쉬운 요약본
    lines = [f"# 일괄 요약 검토 ({stamp})", ""]
    n = 0
    for r in results:
        lines.append(f"## {r['thread_name']}  ({r['message_count']}개 메시지) {r['source']['thread_url']}")
        if not r["decisions"]:
            lines.append("- (결정 없음)")
        for d in r["decisions"]:
            n += 1
            lines.append(f"- **[{n}] [{d['area']}] {d['status']} · {d['topic']}** — {d['decision']}")
        lines.append(f"  - 승인일로 저장될 날짜: {r['last_message_at'][:10]}")
        lines.append("")
    if report:
        lines += ["## 건너뜀", *[f"- {s.get('name', s['id'])}: {s['skip']}" for s in report]]
    (out_dir / f"review-{stamp}.md").write_text("\n".join(lines), encoding="utf-8")

    total = sum(len(r["decisions"]) for r in results)
    print(f"\n결정 {total}건 → 검토 파일: {review}\n               요약본: {review.with_suffix('.md')}")
    print("뺄 결정은 JSON에서 \"include\": false 로 바꾼 뒤:")
    print(f"  python -m scripts.batch_summarize --commit {review}")


async def commit(review_path: str, approved_by: str) -> None:
    from storage import save_decisions  # 여기서 import: 1단계에선 Notion 설정이 없어도 돌게

    data = json.loads(Path(review_path).read_text(encoding="utf-8"))
    saved = already_saved_thread_ids()
    total = notion_fail = 0
    for r in data["threads"]:
        if r["source"]["thread_id"] in saved:
            print(f"  - {r['thread_name']}: 이미 저장됨, 건너뜀")
            continue
        decisions = [{k: v for k, v in d.items() if k != "include"} for d in r["decisions"] if d.get("include", True)]
        if not decisions:
            continue
        source = dict(r["source"])
        # JSON을 거치면 dict 키가 문자열이 되므로 메시지 번호(int)로 되돌린다
        source["message_urls_by_index"] = {int(k): v for k, v in source["message_urls_by_index"].items()}
        decided = (datetime.fromisoformat(r["last_message_at"]) if r.get("last_message_at")
                   else snowflake_time(source["message_urls_by_index"]))
        results = await save_decisions(decisions, source=source, approved_by=approved_by, approved_at=decided)
        total += len(results)
        notion_fail += sum(1 for x in results if x.notion_error)
        print(f"  ✓ {r['thread_name'][:40]} — {len(results)}건 저장")
    print(f"\n저장 {total}건 (Notion 실패 {notion_fail}건)")
    trigger_reindex()


def snowflake_time(urls_by_index: dict) -> datetime | None:
    """Discord ID(snowflake)에는 생성 시각이 들어 있다: (id >> 22) + 2015-01-01 기준 ms.
    날짜 정보 없이 만든 검토 파일도 마지막 메시지 링크의 ID로 결정 시점을 복원한다."""
    ids = [int(u.rstrip("/").rsplit("/", 1)[-1]) for u in urls_by_index.values() if u]
    return discord.utils.snowflake_time(max(ids)) if ids else None


def trigger_reindex() -> None:
    import os
    url = os.environ.get("POLICY_API_URL", "http://localhost:8000").rstrip("/") + "/admin/sync-jobs"
    req = urllib.request.Request(url, data=b"{}", method="POST", headers={
        "Content-Type": "application/json", "X-Admin-Token": os.environ.get("ADMIN_TOKEN", "")})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            print(f"🔄 재인덱싱 시작 ({res.status})")
    except (urllib.error.URLError, OSError) as e:
        print(f"⚠️  API 서버에 재인덱싱 요청 실패({e}). API가 꺼져 있으면: python -m scripts.ingest")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("links", nargs="?", help="스레드/채널 링크 파일")
    ap.add_argument("--commit", metavar="REVIEW_JSON", help="검토 파일을 저장")
    ap.add_argument("--approved-by", default="일괄정리", help="저장 시 승인자 이름")
    ap.add_argument("--force", action="store_true", help="이미 저장된 스레드도 다시 요약")
    ap.add_argument("--limit", type=int, help="앞에서부터 N개만 (시험용)")
    args = ap.parse_args()

    if args.commit:
        asyncio.run(commit(args.commit, args.approved_by))
    elif args.links:
        if not DISCORD_TOKEN or not ANTHROPIC_API_KEY:
            raise SystemExit(".env에 DISCORD_TOKEN, ANTHROPIC_API_KEY가 필요해요.")
        asyncio.run(read_and_summarize(args.links, args.force, args.limit))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
````

**체크포인트:** 스레드 링크 하나를 `data/links.txt`에 넣고 `python -m scripts.batch_summarize data/links.txt --limit 1` → `data/batch/review-*.md`가 생기면 성공. **`--commit`은 하지 않는다**(이미 원본에 저장된 결정이다).

---

## 8단계 — Docker

#### `.dockerignore` (13줄)

이미지에 넣지 않을 것. 비밀값과 데이터는 이미지가 아니라 실행 시 주입·볼륨으로.

````text
# 비밀값은 이미지에 굽지 않는다 → 실행 시 env_file로 주입
.env
# 데이터는 이미지가 아니라 볼륨에 둔다 (컨테이너를 지워도 남도록)
data/
eval/results/
.venv/
.git/
__pycache__/
*.pyc
.pytest_cache/
.idea/
.vscode/
.DS_Store
````

#### `Dockerfile` (30줄)

**이미지 레시피.**

- 볼 포인트: `requirements.txt`를 먼저 복사해 설치 → 코드만 바뀌면 설치 단계는 캐시 재사용.
- 볼 포인트: `--host 0.0.0.0` — 컨테이너 밖(다른 컨테이너)에서 접속 가능하게.

````dockerfile
# ───────────────────────────────────────────────────────────────
# 정책 봇 이미지. API 서버와 디스코드 봇이 '같은 이미지'를 쓰고
# 실행 명령(command)만 다르게 띄운다 → docker-compose.yml 참고
# ───────────────────────────────────────────────────────────────
FROM python:3.12-slim

# 파이썬 로그가 버퍼링 없이 바로 `docker logs`에 찍히도록 / .pyc 안 만들기
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Seoul

WORKDIR /app

# 1) 의존성 먼저 설치 → 코드만 바뀌면 이 레이어는 캐시 재사용 (빌드가 빨라짐)
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2) 코드 복사 (.dockerignore에 있는 .env, data/, .venv 는 들어가지 않음)
COPY . .

# 3) root가 아닌 사용자로 실행 (컨테이너가 뚫려도 권한 최소화)
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app:app /app
USER app

# 컨테이너 '안'에서 열리는 포트. 문서 역할일 뿐, 바깥 공개는 compose의 ports가 결정
EXPOSE 8000

# 기본 실행 = API 서버. 0.0.0.0 으로 열어야 컨테이너 밖(다른 컨테이너/호스트)에서 접속 가능
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
````

#### `docker-compose.yml` (57줄)

**컨테이너 구성.** api·bot은 같은 이미지, 실행 명령만 다르다.

- 볼 포인트: `127.0.0.1:8000:8000` — Mac 자신만 접속.
- 볼 포인트: `POLICY_API_URL: http://api:8000` — 컨테이너끼리는 서비스 이름으로 찾는다.
- 볼 포인트: `depends_on: condition: service_healthy` — api가 건강해진 뒤 bot.

````yaml
# 실행:  docker compose up -d --build
# 로그:  docker compose logs -f bot   (또는 api)
# 중지:  docker compose down
services:
  api:
    build: .
    image: policy-bot:latest
    env_file: .env
    environment:
      CHROMA_PATH: /app/data/chroma
      DATA_DIR: /app/data
    volumes:
      - ./data:/app/data            # 호스트 폴더 ↔ 컨테이너 폴더. 인덱스·히스토리·usage 로그 보존
    ports:
      # "호스트IP:호스트포트:컨테이너포트"
      # 127.0.0.1 로 묶으면 서버 자기 자신만 접근 가능 (인터넷에서 8000번으로 못 들어옴).
      # 외부에 열 때는 이 줄을 바꾸는 대신 Nginx(80/443)를 앞에 두는 게 정석
      - "127.0.0.1:8000:8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
    restart: unless-stopped

  bot:
    image: policy-bot:latest        # api가 빌드한 같은 이미지 재사용
    command: ["python", "bot.py"]
    env_file: .env
    environment:
      # 컨테이너끼리는 compose 네트워크 안에서 '서비스 이름'이 곧 호스트명(내부 DNS)
      # localhost 는 '봇 컨테이너 자기 자신'이라 여기선 쓰면 안 됨
      POLICY_API_URL: http://api:8000
      DATA_DIR: /app/data
    volumes:
      - ./data:/app/data
    # ports 없음: 봇은 디스코드 서버로 '나가는' 연결(WebSocket)만 쓰므로 들어오는 포트가 필요 없다
    depends_on:
      api:
        condition: service_healthy
    restart: unless-stopped

  # 외부 공개용 터널 (기본 꺼짐). 켜기:  docker compose --profile tunnel up -d
  # Mac → Cloudflare 로 '나가는' 연결을 열어 두고, 외부 요청이 그 연결을 타고 api:8000 으로 들어온다.
  # 공유기 포트포워딩 불필요, 집 공개 IP 비노출, HTTPS 자동.
  # 계정 없는 임시 주소(https://<랜덤>.trycloudflare.com)라 재시작하면 주소가 바뀐다.
  # 주소 확인:  docker compose logs tunnel | grep trycloudflare
  # ⚠️ 켜기 전에 .env 에 API_KEY 를 꼭 설정할 것 (없으면 누구나 /policies/answers 로 Claude 비용 사용)
  tunnel:
    image: cloudflare/cloudflared:latest
    command: ["tunnel", "--no-autoupdate", "--url", "http://api:8000"]
    depends_on:
      api:
        condition: service_healthy
    profiles: ["tunnel"]
    restart: unless-stopped
````

**체크포인트:**

```bash
# 클론 터미널의 uvicorn, bot은 Ctrl+C로 끈다. 원본 compose도 꺼져 있어야 한다 (8000번, 봇 토큰 중복)
docker compose up -d --build
docker compose ps        # api (healthy), bot Up
docker compose logs bot --tail 10
```

Discord에서 `/정책질문`이 되면 끝이다. 확인 후에는 클론을 내리고 원본을 다시 켠다:

```bash
docker compose down
cd ~/policy-bot && docker compose up -d
```

---

## 마지막 — 원본과 비교

```bash
diff -rq --exclude=.venv --exclude=data --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache ~/policy-bot ~/policy-bot-clone
```

다른 파일만 나온다. 하나씩 VS Code의 **Compare Selected**로 열어 차이를 확인한다. 오타가 아닌 차이(내가 바꾼 것)가 있다면, 왜 바꿨는지 설명할 수 있으면 된다.
