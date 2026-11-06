"""결정 기록 저장소 (Repository, cosmicpython 2장) — 노션 '정책 결정 히스토리' DB.

호출하는 쪽(storage.save_decisions)은 DecisionRepository 인터페이스만 안다.
- NotionDecisionRepository: 실제 노션. 클라이언트를 주입받아(테스트에서는 가짜) 노션 API 세부를 감춘다.
- 노션이 설정되지 않았으면 get_decision_repository()가 None → 로컬 원장에만 저장.

결정 1건 = 페이지 1개. 재정리에서 '변경'이면 같은 페이지를 덮어쓰고 이전 버전은 본문 '변경 이력'에 남긴다.
페이지 본문은 원장(history.jsonl)에서 매번 다시 만드는 화면이라, 몇 번 갱신해도 어긋나지 않는다.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

from app.config import Settings, get_settings
from app.notion import client as notion
from app.notion.schema import APPROVED_AT, DB_TITLE, PROPERTIES, RECORD_ID, build_properties, prop_to_str, rich_text

log = logging.getLogger("policy-notion")


class DecisionRepository(Protocol):
    """결정 기록 원본 저장소가 지켜야 할 약속. 돌려주는 값은 사람이 열어 볼 URL."""

    async def add(self, record: dict) -> str: ...

    async def update(self, record: dict, previous: list[dict]) -> str: ...

    async def find_url(self, record_id: str) -> str | None: ...


# --- 페이지 본문 (순수 함수: API 없이 테스트) ---
def _heading(text: str) -> dict:
    return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": rich_text(text)}}


def _paragraph(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(text)}}


def _bullet(text: str, url: str | None = None) -> dict:
    rich = [{"type": "text", "text": {"content": text[:2000], "link": {"url": url} if url else None}}]
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich}}


def _callout(text: str, url: str | None = None) -> dict:
    rich = [{"type": "text", "text": {"content": text[:2000], "link": {"url": url} if url else None}}]
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": rich, "icon": {"type": "emoji", "emoji": "↩️"}, "color": "yellow_background"}}


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


def history_blocks(previous: list[dict]) -> list[dict]:
    """'변경 이력' 섹션: 덮어쓴 이전 버전마다 접히는 블록 하나 (최신 버전이 위)."""
    if not previous:
        return []
    blocks = [{"object": "block", "type": "heading_2", "heading_2": {"rich_text": rich_text("변경 이력")}}]
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
                       "toggle": {"rich_text": rich_text(title), "children": inner[:100]}})
    return blocks


# --- 노션 구현 ---
class NotionDecisionRepository:
    def __init__(self, client=None, data_source_id: str | None = None):
        """client: notion_client.AsyncClient (주지 않으면 공용 클라이언트). 테스트는 가짜를 넣는다."""
        self._client = client
        self.data_source_id = data_source_id or get_settings().notion_data_source_id

    @property
    def client(self):
        return self._client or notion.get_async_client()

    # --- 멱등 쓰기 (A-5b): 같은 작업을 몇 번 다시 실행해도 결과가 같다 ---
    # 완료 표시 = 페이지의 '정리일' 속성이 이 버전의 승인 시각과 같음. 버전마다 승인 시각(초)이 달라서 버전을 구분한다.
    # 속성은 항상 **마지막에** 쓰므로, 정리일이 같으면 그 버전의 본문까지 다 쓴 것이다 (DB 구조 변경 없이).

    @staticmethod
    def is_written(page: dict, record: dict) -> bool:
        """이 페이지에 이 버전이 이미 다 써졌나 (노션은 '…:06.000+09:00'처럼 돌려줘서 시각으로 비교한다)."""
        got = prop_to_str((page.get("properties") or {}).get(APPROVED_AT))
        try:
            return bool(got) and datetime.fromisoformat(got) == datetime.fromisoformat(record["approved_at"])
        except ValueError:
            return False

    async def add(self, record: dict) -> str:
        """결정 1건을 페이지로 추가하고 URL을 돌려준다.
        만들기 전에 기록ID로 먼저 찾는다: 지난번에 '생성은 됐는데 응답만 타임아웃'이었다면 중복으로 만들지 않는다."""
        page = await self.find_page(record["id"])
        if page:
            if self.is_written(page, record):
                log.info("노션 기록이 이미 있음 — 다시 만들지 않음 (id=%s)", record["id"], extra={"record_id": record["id"]})
                return page.get("url", "")
            return await self._rewrite(page, record, [])
        reopens_url = await self.find_url(record["reopens"]) if record.get("reopens") else None
        page = await self.client.pages.create(  # 속성+본문을 요청 하나로 → 반쯤 만든 페이지가 남지 않는다
            parent={"type": "data_source_id", "data_source_id": self.data_source_id},
            properties=build_properties(record),
            children=build_children(record, reopens_url),
        )
        log.info("노션 기록 추가 (id=%s)", record["id"], extra={"record_id": record["id"]})
        return page.get("url", "")

    async def update(self, record: dict, previous: list[dict]) -> str:
        """이전 기록을 덮어쓴다 (재정리에서 '변경'). previous: 이 기록의 이전 버전들 (오래된 순).
        페이지를 못 찾으면 새로 만들고, 이미 이 버전이 써져 있으면 아무것도 하지 않는다."""
        page = await self.find_page(record["id"])
        if not page:
            log.warning("덮어쓸 노션 페이지가 없어 새로 만듦 (id=%s)", record["id"], extra={"record_id": record["id"]})
            return await self.add(record)
        if self.is_written(page, record):
            log.info("노션 기록이 이미 이 버전 — 건너뜀 (id=%s, v%s)", record["id"], record.get("version"),
                     extra={"record_id": record["id"]})
            return page.get("url", "")
        return await self._rewrite(page, record, previous)

    async def _rewrite(self, page: dict, record: dict, previous: list[dict]) -> str:
        """본문 다시 쓰기 → 속성(완료 표시) 순서.
        본문: 지금 블록 ID를 먼저 적어 두고 → 새 블록 추가 → 적어 둔 것만 삭제. 본문이 비는 순간이 없고,
        중간에 실패해 다시 하면 그때의 블록(이전 시도의 새 블록 포함)을 적어 두고 같은 일을 하므로 결과가 같다.
        이 페이지는 봇이 만든 결정 기록이라 통째로 다시 쓴다 (정책서 원문이 아님)."""
        client = self.client
        old, cursor = [], None
        while True:
            kw = {"block_id": page["id"], "page_size": 100}
            if cursor:
                kw["start_cursor"] = cursor
            res = await client.blocks.children.list(**kw)
            old += [b["id"] for b in res["results"]]
            if not res.get("has_more"):
                break
            cursor = res["next_cursor"]
        blocks = build_children(record) + history_blocks(previous)
        for i in range(0, len(blocks), 100):  # 요청당 최대 100블록
            await client.blocks.children.append(block_id=page["id"], children=blocks[i:i + 100])
        for block_id in old:
            await client.blocks.delete(block_id=block_id)  # 휴지통으로 이동
        await client.pages.update(page_id=page["id"], properties=build_properties(record))  # 마지막 = 완료 표시
        log.info("노션 기록 갱신 (id=%s, v%s)", record["id"], record.get("version"), extra={"record_id": record["id"]})
        return page.get("url", "")

    async def find_page(self, record_id: str) -> dict | None:
        """기록ID로 결정 기록 페이지를 찾는다."""
        res = await self.client.data_sources.query(
            data_source_id=self.data_source_id, page_size=1,
            filter={"property": RECORD_ID, "rich_text": {"equals": record_id}},
        )
        return res["results"][0] if res.get("results") else None

    async def find_url(self, record_id: str) -> str | None:
        page = await self.find_page(record_id)
        return page.get("url") if page else None

    async def ensure_schema(self) -> list[str]:
        """이미 만든 DB에 PROPERTIES 중 없는 속성을 추가하고, 추가한 이름을 돌려준다. 있는 속성은 건드리지 않는다."""
        ds = await self.client.data_sources.retrieve(data_source_id=self.data_source_id)
        missing = {name: spec for name, spec in PROPERTIES.items() if name not in ds.get("properties", {})}
        if missing:
            await self.client.data_sources.update(data_source_id=self.data_source_id, properties=missing)
        return list(missing)


async def create_database(parent_page_id: str, client=None) -> dict:
    """부모 페이지 아래에 히스토리 DB를 만든다. setup_notion.py에서 1회 실행."""
    client = client or notion.get_async_client()
    db = await client.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=rich_text(DB_TITLE),
        initial_data_source={"properties": PROPERTIES},
    )
    sources = db.get("data_sources") or []
    if not sources:  # 응답에 없으면 다시 조회
        db = await client.databases.retrieve(database_id=db["id"])
        sources = db.get("data_sources") or []
    if not sources:
        raise RuntimeError("DB는 만들었지만 data source ID를 찾지 못했어요.")
    return {"database_id": db["id"], "data_source_id": sources[0]["id"], "url": db.get("url", "")}


def get_decision_repository(s: Settings | None = None) -> NotionDecisionRepository | None:
    """노션이 설정돼 있으면 노션 저장소, 아니면 None (로컬 원장에만 저장)."""
    s = s or get_settings()
    return NotionDecisionRepository(data_source_id=s.notion_data_source_id) if notion.enabled(s) else None
