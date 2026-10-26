"""정책서(기능명세서) → 검색 문서 (EXPERIMENTS 4-1).

결정 DB만 검색하면 "정리 안 된 건 없는 것"처럼 답한다. 노션 정책서가 기준 문서이므로 같이 인덱싱한다.
- 정책 하나(표의 한 행) = 문서 하나. 로더(spec_loader)가 이미 정책 ID 단위로 쪼개 준다.
- page_id를 정책서 페이지가 아니라 **행(block_id)** 으로 둔다. 검색의 '페이지당 1개' 중복 제거가
  정책서 한 페이지를 결과 1개로 합쳐버리지 않게 하기 위해서다.
- 링크는 그 행으로 바로 가는 노션 앵커(#block_id). 출처 표시와 반영 제안 위치가 같은 단위가 된다.
LLM 호출이 없어서 사용량 기록 대상이 아니다 (임베딩은 ingest가 기록).
"""
import asyncio
import logging

from app.config import Settings
from app.rag.models import PolicyDocument
from app.rag.spec_loader import SpecDocument, SpecEntry, load_spec

log = logging.getLogger("policy-api")

SPEC_PREFIX = "spec-"


def _anchor(doc: SpecDocument, block_id: str) -> str:
    base = doc.url or f"https://www.notion.so/{doc.page_id.replace('-', '')}"
    return f"{base.split('#')[0]}#{block_id.replace('-', '')}"


def entry_title(e: SpecEntry) -> str:
    if e.policy_id:
        return f"{e.policy_id} {e.name}".strip()
    heading = e.section_path.rsplit(" #", 1)[0].split(" > ")[-1]  # ID 없는 표(공통 규칙): 소속 제목 + 행 이름
    return f"{heading} · {e.name}".strip(" ·")


def entry_to_document(doc: SpecDocument, e: SpecEntry, area: str = "") -> PolicyDocument:
    return PolicyDocument(
        page_id=f"{SPEC_PREFIX}{e.block_id}",
        title=entry_title(e),
        url=_anchor(doc, e.block_id),
        body=e.body,
        metadata={
            "doc_type": "정책서",
            "area": area,
            "policy_id": e.policy_id or "",
            "screen": e.screen_title,
            "version_tags": ",".join(e.tags),
            "spec_title": doc.title,
            "spec_edited": e.last_edited[:10],
            "last_edited": e.last_edited,
        },
    )


def spec_documents(doc: SpecDocument, areas: list[str]) -> list[PolicyDocument]:
    # 정책서 하나를 여러 영역이 같이 쓰면 영역을 특정할 수 없다 → 비워서 영역 필터에 걸리지 않게
    area = areas[0] if len(areas) == 1 else ""
    return [entry_to_document(doc, e, area) for e in doc.entries if e.body.strip() or e.name.strip()]


async def _load_all(settings: Settings) -> list[PolicyDocument]:
    from notion_client import AsyncClient
    client = AsyncClient(auth=settings.notion_token)
    docs: list[PolicyDocument] = []
    for page_id, areas in settings.spec_pages().items():
        spec = await load_spec(client, page_id, f"{settings.data_dir}/spec")
        docs += spec_documents(spec, areas)
    return docs


def load_spec_documents(settings: Settings) -> list[PolicyDocument]:
    """설정된 정책서를 모두 읽는다. 블록은 페이지 수정 시각 기준으로 data/spec에 캐시된다 (반영 제안과 공용)."""
    if not (settings.spec_index_enabled and settings.notion_token and settings.notion_spec_pages):
        return []
    return asyncio.run(_load_all(settings))
