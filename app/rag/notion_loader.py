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
            # '결정일'이 생기기 전 페이지는 비어 있으니 옛 '승인일'로 대신한다
            "decided_at": (p(s.notion_prop_decided_at) or p("승인일"))[:10],
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
    decided_at = r.get("decided_at") or approved_at  # decided_at이 생기기 전 기록은 승인일로
    return PolicyDocument(
        page_id=f"local-{r['id']}",
        title=r.get("topic") or "(제목 없음)",
        url=src.get("thread_url", ""),  # 로컬 기록은 Notion URL이 없어서 디스코드 스레드 링크 사용
        body=body,
        metadata={
            "area": r.get("area", ""),
            "status": r.get("status", ""),
            "decider": r.get("decided_by") or "",
            "decided_at": decided_at[:10],
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
