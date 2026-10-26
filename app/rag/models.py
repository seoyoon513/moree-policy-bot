"""RAG 파이프라인에서 주고받는 데이터 구조."""
from dataclasses import dataclass, field


@dataclass
class PolicyDocument:
    """Notion 페이지 하나 = 정책 문서 하나."""
    page_id: str
    title: str
    url: str
    body: str
    metadata: dict[str, str] = field(default_factory=dict)  # area, status, decider, decided_at, scope, last_edited, record_id, reopens


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
