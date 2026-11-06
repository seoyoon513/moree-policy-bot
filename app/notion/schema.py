"""'정책 결정 히스토리' DB 스키마 — 속성 이름·타입과 값 변환을 한 곳에.

쓰기(app/notion/decisions.py: 기록 → 속성)와 읽기(app/rag/notion_loader.py: 속성 → 검색 문서)가
같은 이름표를 쓴다. 전에는 봇의 PROPERTIES와 API 설정의 notion_prop_*에 따로 있어서
속성 하나를 추가하면 두 곳을 고쳐야 했다.
"""
from __future__ import annotations

from app.domain import AREAS, STATUSES

DB_TITLE = "정책 결정 히스토리"

# --- 속성 이름 ---
TITLE = "주제"
AREA = "기능영역"
STATUS = "상태"
DECISION = "결정"
RATIONALE = "근거"
DECIDER = "결정자"
SCOPE = "적용범위"
EXTERNAL_REFS = "외부참조"
OPEN_QUESTIONS = "확인필요"
THREAD_URL = "스레드"
THREAD_NAME = "스레드명"
APPROVER = "승인자"
DECIDED_AT = "결정일"       # 근거 메시지 중 가장 늦은 시각. 시간순 정렬 기준
APPROVED_AT = "정리일"      # 승인 버튼을 누른 시각
RECORD_ID = "기록ID"
REOPENS = "재논의대상"      # 재논의 기록이 가리키는 이전 최종결정의 기록ID
LEGACY_APPROVED_AT = "승인일"  # '결정일'이 생기기 전 페이지에만 있는 옛 속성 (읽기 전용)

# 검색 문서 본문으로 쓸 속성들 (순서대로 "라벨: 값")
BODY_PROPS = [DECISION, RATIONALE, SCOPE, EXTERNAL_REFS, OPEN_QUESTIONS, THREAD_NAME]

STATUS_COLORS = {"제안": "gray", "논의중": "yellow", "최종결정": "green"}
_AREA_COLORS = ["blue", "purple", "pink", "orange", "brown", "red", "green", "yellow", "gray"]

PROPERTIES = {
    TITLE: {"title": {}},
    AREA: {"select": {"options": [{"name": a, "color": _AREA_COLORS[i % len(_AREA_COLORS)]} for i, a in enumerate(AREAS)]}},
    STATUS: {"select": {"options": [{"name": s, "color": STATUS_COLORS[s]} for s in STATUSES]}},
    DECISION: {"rich_text": {}},
    RATIONALE: {"rich_text": {}},
    DECIDER: {"rich_text": {}},
    SCOPE: {"rich_text": {}},
    EXTERNAL_REFS: {"rich_text": {}},
    OPEN_QUESTIONS: {"rich_text": {}},
    THREAD_URL: {"url": {}},
    THREAD_NAME: {"rich_text": {}},
    APPROVER: {"rich_text": {}},
    DECIDED_AT: {"date": {}},
    APPROVED_AT: {"date": {}},
    RECORD_ID: {"rich_text": {}},
    REOPENS: {"rich_text": {}},
}


# --- 쓰기: 기록(dict) → 속성 ---
def rich_text(text: str | None) -> list[dict]:
    """rich_text 배열. Notion 제한: 조각당 2000자, 최대 100조각."""
    if not text:
        return []
    return [{"type": "text", "text": {"content": text[i : i + 2000]}} for i in range(0, min(len(text), 2000 * 100), 2000)]


def questions_text(questions: list[dict]) -> str:
    return "\n".join(f"• {q['question']}" + (f" → {q['ask_to']}" if q.get("ask_to") else "") for q in questions)


def build_properties(record: dict) -> dict:
    src = record["source"]
    # 재논의대상은 값이 있을 때만 보낸다: 이 속성이 생기기 전 DB(ensure_schema 전)에서도 일반 저장은 되게
    extra = {REOPENS: {"rich_text": rich_text(record["reopens"])}} if record.get("reopens") else {}
    return extra | {
        TITLE: {"title": rich_text(record["topic"])},
        AREA: {"select": {"name": record["area"]}},
        STATUS: {"select": {"name": record["status"]}},
        DECISION: {"rich_text": rich_text(record["decision"])},
        RATIONALE: {"rich_text": rich_text(record.get("rationale"))},
        DECIDER: {"rich_text": rich_text(record.get("decided_by"))},
        SCOPE: {"rich_text": rich_text(record.get("scope"))},
        EXTERNAL_REFS: {"rich_text": rich_text("\n".join(record.get("external_refs") or []))},
        OPEN_QUESTIONS: {"rich_text": rich_text(questions_text(record.get("open_questions") or []))},
        THREAD_URL: {"url": src.get("thread_url") or None},
        THREAD_NAME: {"rich_text": rich_text(src.get("thread_name"))},
        APPROVER: {"rich_text": rich_text(record.get("approved_by"))},
        DECIDED_AT: {"date": {"start": record.get("decided_at") or record["approved_at"]}},
        APPROVED_AT: {"date": {"start": record["approved_at"]}},
        RECORD_ID: {"rich_text": rich_text(record["id"])},
    }


# --- 읽기: 속성 → 문자열 ---
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
