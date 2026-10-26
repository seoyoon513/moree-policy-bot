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
    "결정일": {"date": {}},  # 근거 메시지 중 가장 늦은 시각. 시간순 정렬 기준
    "정리일": {"date": {}},  # 승인 버튼을 누른 시각
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
        "결정일": {"date": {"start": record.get("decided_at") or record["approved_at"]}},
        "정리일": {"date": {"start": record["approved_at"]}},
        "기록ID": {"rich_text": _rt(record["id"])},
    }


def _heading(text: str) -> dict:
    return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": _rt(text)}}


def _paragraph(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rt(text)}}


def _bullet(text: str, url: str | None = None) -> dict:
    rich = [{"type": "text", "text": {"content": text[:2000], "link": {"url": url} if url else None}}]
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich}}


def _excerpt_line(m: dict) -> str:
    ts = m["at"][5:16].replace("T", " ") if m.get("at") else ""
    return f"{m.get('author', '')} ({ts}): {m.get('text', '')}"


def build_children(record: dict) -> list[dict]:
    """페이지 본문: 사람이 읽기 쉬운 형태 + 근거 메시지 원문 발췌(링크).
    발췌는 LLM이 아니라 원문을 잘라 넣은 것이라, 요약이 틀렸을 때 사람이 바로 대조할 수 있다."""
    blocks = [_heading("결정"), _paragraph(record["decision"] or "-")]
    if record.get("rationale"):
        blocks += [_heading("근거"), _paragraph(record["rationale"])]
    if record.get("open_questions"):
        blocks.append(_heading("확인 필요"))
        blocks += [
            _bullet(q["question"] + (f" → {q['ask_to']}" if q.get("ask_to") else "")) for q in record["open_questions"]
        ]
    messages = record["source"].get("messages") or []
    urls = record["source"].get("message_urls") or []
    if messages:
        blocks.append(_heading("근거 메시지"))
        blocks += [_bullet(_excerpt_line(m), m.get("url")) for m in messages]
    elif urls:  # 발췌를 저장하기 전 기록
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
