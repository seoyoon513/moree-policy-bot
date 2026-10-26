"""정책서 인덱싱(4-1) 테스트. 네트워크 없음, 정책서 내용은 가짜."""
import uuid

import pytest

from app.config import Settings
from app.rag import ingest as ingest_mod
from app.rag import spec_index
from app.rag.chunker import chunk_document
from app.rag.generator import build_context
from app.rag.ingest import ingest_documents, ingest_from_source
from app.rag.retriever import Retriever
from app.rag.spec_loader import SpecDocument, SpecEntry
from app.rag.store import VectorStore
from test_rag_api import DOCS, FakeEmbedder

PAGE = "0123456789abcdef0123456789abcdef"


def _entry(pid, name, body, block, section="화면별 상세 명세 > XX-04. 상세 > 기능 요구사항"):
    return SpecEntry(policy_id=pid, screen_id="XX-04" if pid else None, screen_title="XX-04. 상세" if pid else "",
                     name=name, body=body, section_path=section, block_id=block, table_id="t", parent_id="h",
                     page_id=PAGE, last_edited="2026-10-01T00:00:00.000Z", tags=["1.3.0"] if pid else [])


SPEC = SpecDocument(PAGE, f"https://www.notion.so/spec-{PAGE}", "가짜 기능명세서", [
    _entry("XX-04-01", "상세/정렬", "상세 설명: 최신순 / 거리순 / 이름순 3종", "aaaa-1"),
    _entry("XX-04-02", "상세/Empty", "상세 설명: 문구 '아직 저장된 항목이 없어요'", "bbbb-2"),
    _entry(None, "3", "규칙: 재시도 횟수 제한 없음", "cccc-3", section="공통 상태 > 재시도 규칙 #3"),
], {}, [])


def test_entry_becomes_its_own_document_with_row_anchor():
    docs = spec_index.spec_documents(SPEC, ["가짜영역"])
    assert [d.title for d in docs] == ["XX-04-01 상세/정렬", "XX-04-02 상세/Empty", "재시도 규칙 · 3"]
    d = docs[0]
    assert d.page_id == "spec-aaaa-1"   # 행 단위: 같은 정책서의 다른 정책과 page_id가 다르다
    assert d.url == f"https://www.notion.so/spec-{PAGE}#aaaa1"
    assert d.metadata["doc_type"] == "정책서" and d.metadata["area"] == "가짜영역"
    header = chunk_document(d)[0].text.splitlines()[0]
    assert header == "[정책서] XX-04-01 상세/정렬 | 영역: 가짜영역 | 화면: XX-04. 상세 | 버전: 1.3.0 | 수정일: 2026-10-01"


def test_shared_spec_page_has_no_area():
    assert {d.metadata["area"] for d in spec_index.spec_documents(SPEC, ["A", "B"])} == {""}
    assert {d.metadata["area"] for d in spec_index.spec_documents(SPEC, [])} == {""}


def test_spec_pages_setting_parses_areas_and_default():
    s = Settings(_env_file=None, notion_spec_pages=f"북마크=https://www.notion.so/x-{PAGE}, 지도·탐색={PAGE}\n*={'f' * 32}")
    assert s.spec_pages() == {PAGE: ["북마크", "지도·탐색"], "f" * 32: []}


@pytest.fixture
def mixed_store():
    store = VectorStore(None, f"test_{uuid.uuid4().hex[:8]}")
    emb = FakeEmbedder()
    ingest_documents(DOCS + spec_index.spec_documents(SPEC, ["가짜영역"]), emb, store, Settings())
    return store, emb


def test_search_returns_several_policies_of_one_spec_page(mixed_store):
    store, emb = mixed_store
    hits = Retriever(emb, store).search("상세 정렬 Empty 문구", k=5)
    spec_hits = [h for h in hits if h.metadata.get("doc_type") == "정책서"]
    assert len(spec_hits) >= 2   # 페이지당 1개 중복 제거에 합쳐지지 않는다


def test_answer_context_labels_document_kind(mixed_store):
    store, emb = mixed_store
    ctx = build_context(Retriever(emb, store).search("거리순 정렬", k=3))
    assert 'kind="정책서" title="XX-04-01 상세/정렬"' in ctx
    ctx = build_context(Retriever(emb, store).search("리뷰 작성 조건", k=1))
    assert 'kind="결정기록"' in ctx


def test_spec_failure_keeps_old_spec_chunks(mixed_store, monkeypatch):
    store, emb = mixed_store

    class Loader:
        def load(self):
            return DOCS

    def broken(_settings):
        raise RuntimeError("노션 연결 실패")

    monkeypatch.setattr("app.rag.notion_loader.build_loader", lambda s: Loader())
    monkeypatch.setattr(spec_index, "load_spec_documents", broken)
    r = ingest_from_source(Settings(_env_file=None), emb, store)
    assert r.removed_chunks == 0 and r.spec_error == "RuntimeError: 노션 연결 실패"
    assert ingest_mod._indexed_hashes(store).keys() >= {"spec-aaaa-1", "spec-bbbb-2", "p1"}


def test_spec_disabled_without_settings():
    # .env 값이 환경변수로 들어와 있을 수 있어 명시적으로 비운다 (네트워크 호출 방지)
    assert spec_index.load_spec_documents(Settings(_env_file=None, notion_token="t", notion_spec_pages="")) == []
    assert spec_index.load_spec_documents(
        Settings(_env_file=None, notion_token="t", notion_spec_pages=f"*={PAGE}", spec_index_enabled=False)) == []
