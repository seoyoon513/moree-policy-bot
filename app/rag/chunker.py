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
