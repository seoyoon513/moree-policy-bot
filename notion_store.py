"""Notion '정책 결정 히스토리' DB 연동 (Notion API 2025-09-03, data source 기반).

- DB 속성 스키마는 PROPERTIES 한 곳에서 관리
- setup_notion.py가 이 스키마로 DB를 생성
- 승인 시 save_record()가 결정 1건 = 페이지 1개로 추가
- 같은 스레드 재정리에서 '변경'이면 update_record()가 같은 페이지를 덮어쓰고 이전 버전은 본문 '변경 이력'에 남긴다
"""
import logging

from notion_client import AsyncClient

from app.rag.spec_loader import notion_id
from config import AREAS, NOTION_DATA_SOURCE_ID, NOTION_TOKEN, STATUSES

log = logging.getLogger("policy-bot")

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
    # 재논의 기록이 가리키는 이전 최종결정의 기록ID. 본문 콜아웃만으로는 검색 로더(속성만 읽음)가 짝을 모른다
    "재논의대상": {"rich_text": {}},
}

_client: AsyncClient | None = None


def enabled() -> bool:
    return bool(NOTION_TOKEN and NOTION_DATA_SOURCE_ID)


def get_client() -> AsyncClient:
    global _client
    if _client is None:
        _client = AsyncClient(auth=NOTION_TOKEN)
    return _client


extract_id = notion_id  # 페이지 URL이나 ID 문자열에서 32자리 ID (app.rag.spec_loader와 공용)


def _rt(text: str | None) -> list[dict]:
    """rich_text 배열. Notion 제한: 조각당 2000자, 최대 100조각."""
    if not text:
        return []
    return [{"type": "text", "text": {"content": text[i : i + 2000]}} for i in range(0, min(len(text), 2000 * 100), 2000)]


def _questions_text(questions: list[dict]) -> str:
    return "\n".join(f"• {q['question']}" + (f" → {q['ask_to']}" if q.get("ask_to") else "") for q in questions)


def build_properties(record: dict) -> dict:
    src = record["source"]
    # 재논의대상은 값이 있을 때만 보낸다: 이 속성이 생기기 전 DB(ensure_schema 전)에서도 일반 저장은 되게
    extra = {"재논의대상": {"rich_text": _rt(record["reopens"])}} if record.get("reopens") else {}
    return extra | {
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


def build_children(record: dict, reopens_url: str | None = None) -> list[dict]:
    """페이지 본문: 사람이 읽기 쉬운 형태 + 근거 메시지 원문 발췌(링크).
    발췌는 LLM이 아니라 원문을 잘라 넣은 것이라, 요약이 틀렸을 때 사람이 바로 대조할 수 있다."""
    blocks = []
    if record.get("reopens"):  # 이전 최종결정을 덮지 않은 재논의 기록
        blocks.append(_callout(f"재논의 중 — 이전 최종결정(기록 {record['reopens']})은 그대로 유효해요.", reopens_url))
    blocks += [_heading("결정"), _paragraph(record["decision"] or "-")]
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


def _callout(text: str, url: str | None = None) -> dict:
    rich = [{"type": "text", "text": {"content": text[:2000], "link": {"url": url} if url else None}}]
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": rich, "icon": {"type": "emoji", "emoji": "↩️"}, "color": "yellow_background"}}


def history_blocks(previous: list[dict]) -> list[dict]:
    """'변경 이력' 섹션: 덮어쓴 이전 버전마다 접히는 블록 하나 (최신 버전이 위).
    로컬 history.jsonl의 버전에서 매번 다시 만들기 때문에 몇 번 갱신해도 어긋나지 않는다."""
    if not previous:
        return []
    blocks = [{"object": "block", "type": "heading_2", "heading_2": {"rich_text": _rt("변경 이력")}}]
    for r in reversed(previous):
        title = (f"v{r.get('version', 1)} · {(r.get('approved_at') or '')[:10]} 정리 · {r.get('status', '')}"
                 f" · 승인 {r.get('approved_by') or '-'}")
        inner = [_paragraph(f"결정: {r.get('decision') or '-'}")]
        if r.get("rationale"):
            inner.append(_paragraph(f"근거: {r['rationale']}"))
        if r.get("scope"):
            inner.append(_paragraph(f"적용범위: {r['scope']}"))
        inner += [_bullet(_excerpt_line(m), m.get("url")) for m in (r.get("source") or {}).get("messages") or []]
        blocks.append({"object": "block", "type": "toggle",
                       "toggle": {"rich_text": _rt(title), "children": inner[:100]}})
    return blocks


async def save_record(record: dict) -> str:
    """결정 1건을 Notion 페이지로 추가하고 페이지 URL을 돌려준다."""
    reopens_url = await find_record_url(record["reopens"]) if record.get("reopens") else None
    page = await get_client().pages.create(
        parent={"type": "data_source_id", "data_source_id": NOTION_DATA_SOURCE_ID},
        properties=build_properties(record),
        children=build_children(record, reopens_url),
    )
    return page.get("url", "")


async def update_record(record: dict, previous: list[dict]) -> str:
    """이전 기록을 덮어쓴다 (재정리에서 '변경'). 속성은 새 내용으로, 본문은 새로 쓰고 아래에 변경 이력.
    previous: 이 기록의 이전 버전들 (오래된 순). 페이지를 못 찾으면 새로 만든다."""
    page = await find_record_page(record["id"])
    if not page:
        log.warning("덮어쓸 노션 페이지가 없어 새로 만듦 (id=%s)", record["id"])
        return await save_record(record)
    client = get_client()
    await client.pages.update(page_id=page["id"], properties=build_properties(record))
    # 본문 다시 쓰기: 이 페이지는 봇이 만든 결정 기록이라 통째로 다시 쓴다 (정책서 원문이 아님)
    old, cursor = [], None
    while True:
        kw = {"block_id": page["id"], "page_size": 100}
        if cursor:
            kw["start_cursor"] = cursor
        res = await client.blocks.children.list(**kw)
        old += res["results"]
        if not res.get("has_more"):
            break
        cursor = res["next_cursor"]
    for b in old:
        await client.blocks.delete(block_id=b["id"])  # 휴지통으로 이동
    blocks = build_children(record) + history_blocks(previous)
    for i in range(0, len(blocks), 100):  # 요청당 최대 100블록
        await client.blocks.children.append(block_id=page["id"], children=blocks[i:i + 100])
    return page.get("url", "")


async def find_record_page(record_id: str) -> dict | None:
    """기록ID로 결정 기록 페이지를 찾는다."""
    res = await get_client().data_sources.query(
        data_source_id=NOTION_DATA_SOURCE_ID, page_size=1,
        filter={"property": "기록ID", "rich_text": {"equals": record_id}},
    )
    return res["results"][0] if res.get("results") else None


async def find_record_url(record_id: str) -> str | None:
    """기록ID로 결정 기록 페이지 URL을 찾는다 (저장된 기록을 나중에 정책서에 반영할 때 링크용)."""
    page = await find_record_page(record_id)
    return page.get("url") if page else None


async def ensure_schema() -> list[str]:
    """이미 만든 DB에 PROPERTIES 중 없는 속성을 추가하고, 추가한 이름을 돌려준다. 있는 속성은 건드리지 않는다."""
    client = get_client()
    ds = await client.data_sources.retrieve(data_source_id=NOTION_DATA_SOURCE_ID)
    missing = {name: spec for name, spec in PROPERTIES.items() if name not in ds.get("properties", {})}
    if missing:
        await client.data_sources.update(data_source_id=NOTION_DATA_SOURCE_ID, properties=missing)
    return list(missing)


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
