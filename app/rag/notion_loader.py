"""[인덱싱 1] 정책 결정을 읽어 PolicyDocument로 변환.

소스 두 가지:
- NotionLoader: '정책 결정 히스토리' DB (notion-client 3.x, data source API)
- JsonlLoader : 봇이 로컬에 항상 남기는 data/history.jsonl (Notion 없이 테스트할 때)

봇은 결정 1건 = 페이지 1개로 저장하고, 결정/근거 등 핵심 내용을 '속성'에 넣는다.
그래서 본문 블록을 따로 읽지 않고 속성만으로 문서를 만든다 → 페이지당 API 호출 0회 추가.
"""
from shared.config import Settings
from app.ledger import Ledger
from app.notion import schema as ns
from app.notion.client import new_sync_client
from app.notion.schema import prop_to_str  # noqa: F401  (예전 import 경로 유지)
from app.rag.models import PolicyDocument


def _body_from_fields(fields: list[tuple[str, str]]) -> str:
    return "\n".join(f"{label}: {value}" for label, value in fields if value and value.strip())


def page_to_document(page: dict) -> PolicyDocument:
    """속성 이름은 app/notion/schema.py — 봇이 저장할 때 쓰는 이름과 같은 이름표."""
    props = page["properties"]
    p = lambda name: prop_to_str(props.get(name))
    return PolicyDocument(
        page_id=page["id"],
        title=p(ns.TITLE) or "(제목 없음)",
        url=page.get("url", ""),
        body=_body_from_fields([(name, p(name)) for name in ns.BODY_PROPS]),
        metadata={
            "area": p(ns.AREA),
            "status": p(ns.STATUS),
            "decider": p(ns.DECIDER),
            # '결정일'이 생기기 전 페이지는 비어 있으니 옛 '승인일'로 대신한다
            "decided_at": (p(ns.DECIDED_AT) or p(ns.LEGACY_APPROVED_AT))[:10],
            "scope": p(ns.SCOPE),
            "thread_url": p(ns.THREAD_URL),
            "last_edited": page.get("last_edited_time", ""),
            # 재논의 짝을 찾는 키: 재논의 기록은 reopens = 이전 최종결정의 record_id
            "record_id": p(ns.RECORD_ID),
            "reopens": p(ns.REOPENS),
        },
    )


class NotionLoader:
    def __init__(self, settings: Settings):
        self.s = settings
        self.client = new_sync_client(settings.notion_token)  # 재시도·로그 설정은 app/notion/client.py

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
        return [page_to_document(page) for page in self._iter_pages()]


def record_to_document(r: dict) -> PolicyDocument:
    """storage.py가 만든 record(dict) → PolicyDocument."""
    questions = ns.questions_text(r.get("open_questions") or [])
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
            "record_id": r.get("id", ""),
            "reopens": r.get("reopens", ""),
        },
    )


class JsonlLoader:
    def __init__(self, settings: Settings):
        self.ledger = Ledger(settings.data_dir)

    def load(self) -> list[PolicyDocument]:
        """각 기록의 현재 버전만. 깨진 줄은 원장이 건너뛴다 (전에는 예외로 인덱싱 전체가 실패)."""
        return [record_to_document(r) for r in self.ledger.current()]


def build_loader(settings: Settings):
    return NotionLoader(settings) if settings.resolved_source() == "notion" else JsonlLoader(settings)
