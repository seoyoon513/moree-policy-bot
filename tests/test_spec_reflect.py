"""원천 정책서 로더(3-2) · 매칭 정규화(3-3) · 반영(3-4) 테스트.
노션·Claude 호출 없음 (가짜 블록·가짜 클라이언트). 데이터는 전부 가짜."""
import asyncio

import spec_reflect
from app.rag.spec_loader import parse_tree
from spec_reflect import Match, apply_match, format_outcomes, normalize_matches, reflect_records

PAGE = {"id": "page-1", "url": "https://notion.so/spec-1",
        "properties": {"title": {"type": "title", "title": [{"plain_text": "가짜 기능명세서"}]}}}
_n = 0


def _id(prefix: str) -> str:
    global _n
    _n += 1
    return f"{prefix}-{_n}"


def rt(text: str) -> list[dict]:
    return [{"plain_text": text}]


def heading(level: int, text: str, children=None, color="default") -> dict:
    t = f"heading_{level}"
    b = {"id": _id("h"), "type": t, t: {"rich_text": rt(text), "color": color}}
    if children is not None:
        b["children"] = children
    return b


def row(*cells: str) -> dict:
    return {"id": _id("row"), "type": "table_row", "table_row": {"cells": [rt(c) for c in cells]},
            "last_edited_time": "2026-10-01T00:00:00.000Z"}


def table(*rows: dict, header=True) -> dict:
    return {"id": _id("tbl"), "type": "table", "table": {"has_column_header": header}, "children": list(rows)}


def req_table() -> dict:
    return table(row("화면 ID", "기능명", "상세 설명"),
                 row("XX-05-01", "목록 편집", "체크박스 노출"),
                 row("XX-05-02", "목록 이동", "현재 폴더는 체크 표시"))


def sample_doc():
    blocks = [
        heading(2, "2. 화면 목록"),
        table(row("화면 ID", "화면명", "비고"), row("XX-05", "목록 편집", "폴더 이동")),
        heading(2, "4. 화면별 상세 명세"),
        heading(3, "XX-05. 목록 편집 (1.3.0)", color="green", children=[
            table(row("항목", "내용"), row("화면 목적", "폴더 안 목록 수정")),
            heading(4, "기능 요구사항"),
            req_table(),
        ]),
        # 복사하면서 같은 내용이 페이지 바깥에 또 들어간 경우
        heading(4, "기능 요구사항"),
        req_table(),
        heading(3, "XX-06. 에러", children=[
            heading(4, "예외 처리"),
            table(row("화면 ID", "상황", "처리"),
                  row("XX-06-01", "이름 미입력", "에러 문구"),
                  row("", "글자수 초과", "입력 차단"),   # ID 칸이 빈 행 → 위에 이어 붙음
                  row("XX-01", "다른 화면 ID", "오타")),   # 화면과 다른 ID → 경고
        ]),
        heading(2, "공통 규칙", children=[table(row("순서", "규칙"), row("1", "재시도는 해당 영역만"))]),
    ]
    return parse_tree(PAGE, blocks)


# --- 3-2 로더 ---
def test_entries_are_table_rows_with_screen_and_location():
    doc = sample_doc()
    e = doc.by_id()["XX-05-02"]
    assert (e.screen_id, e.name, e.body) == ("XX-05", "목록 이동", "상세 설명: 현재 폴더는 체크 표시")
    assert e.block_id.startswith("row-") and e.table_id.startswith("tbl-")
    assert e.parent_id.startswith("h-")  # 접히는 제목 안의 표
    assert e.section_path == "4. 화면별 상세 명세 > XX-05. 목록 편집 (1.3.0) > 기능 요구사항"
    assert e.tags == ["1.3.0"] and e.heading_colors == ["green"]


def test_screen_index_and_info_tables_are_not_policies():
    doc = sample_doc()
    assert "XX-05" not in doc.by_id()
    assert doc.screens["XX-05"]["title"] == "XX-05. 목록 편집 (1.3.0)"
    assert doc.screens["XX-05"]["purpose"] == "폴더 안 목록 수정"


def test_copied_duplicates_keep_the_one_inside_heading():
    doc = sample_doc()
    xs = [e for e in doc.entries if e.policy_id == "XX-05-01"]
    assert len(xs) == 1 and xs[0].parent_id != PAGE["id"] and len(xs[0].duplicate_block_ids) == 1
    assert not any("충돌" in w for w in doc.warnings)


def test_conflicting_duplicates_are_kept_and_warned():
    a, b = req_table(), table(row("화면 ID", "기능명", "상세 설명"), row("XX-05-01", "목록 편집", "다른 내용"))
    doc = parse_tree(PAGE, [heading(3, "XX-05. 목록", children=[a]), b])
    assert len([e for e in doc.entries if e.policy_id == "XX-05-01"]) == 2
    assert any("XX-05-01" in w and "충돌" in w for w in doc.warnings)


def test_empty_id_row_continues_previous_and_mismatch_warns():
    doc = sample_doc()
    e = doc.by_id()["XX-06-01"]
    assert "글자수 초과" in e.body and len(e.extra_block_ids) == 1
    assert any("XX-01" in w and "XX-06" in w for w in doc.warnings)


def test_tables_without_id_become_section_entries():
    doc = sample_doc()
    sec = [e for e in doc.entries if e.policy_id is None]
    assert len(sec) == 1 and sec[0].name == "1" and "재시도" in sec[0].body and sec[0].screen_id is None


# --- 3-3 매칭 정규화 ---
def test_normalize_matches_downgrades_invalid_ids():
    doc = sample_doc()
    data = {"matches": [
        {"decision": 1, "action": "modify", "policy_id": "XX-05-02", "proposal": "p"},
        {"decision": 2, "action": "modify", "policy_id": "XX-99-99", "screen_id": "XX-05"},  # 없는 ID → new
        {"decision": 3, "action": "new", "screen_id": "XX-99"},                              # 없는 화면 → unknown
        {"decision": 4, "action": "same", "policy_id": None, "screen_id": "XX-05"},          # ID 없는 same → 그대로 same
        {"decision": 9, "action": "same"},                                                    # 범위 밖 무시
    ]}
    ms = normalize_matches(data, doc, 5)
    assert [m.action for m in ms] == ["modify", "new", "unknown", "same", "unknown"]
    assert ms[0].screen_id == "XX-05"   # ID에서 화면을 채움
    assert ms[4].reason == "모델 응답에 없음"


def test_same_without_id_and_skip_write_nothing():
    doc, fake = sample_doc(), FakeNotion()
    out = asyncio.run(apply_match(fake, doc, _record(), Match(1, "same", None, "XX-05"), None))
    assert fake.calls == [] and out.action == "same" and out.url is None
    out = asyncio.run(apply_match(fake, doc, _record(), Match(1, "skip", reason="아이콘 시안 오류"), None))
    assert fake.calls == [] and out.action == "skip"
    assert "정책서 대상 아님" in format_outcomes([out])[1]


def test_prompt_prefers_fewer_proposals():
    from prompts import SPEC_MATCH_SYSTEM
    assert '"skip"' in SPEC_MATCH_SYSTEM and "어느 항목에든" in SPEC_MATCH_SYSTEM
    assert "제안을 덜 만드는 쪽" in SPEC_MATCH_SYSTEM
    assert "문구(버튼·토스트·팝업" in SPEC_MATCH_SYSTEM  # 문구 변경은 skip이 아님 (2026-10-26 오판정)
    assert "skip이 아니라 unknown" in SPEC_MATCH_SYSTEM  # 다른 정책서 내용을 skip으로 버리던 오판정


# --- 3-4 반영 ---
class FakeNotion:
    def __init__(self, existing_comments=None):
        self.calls = []
        existing = existing_comments or []
        outer = self

        class Comments:
            async def create(self, **kw):
                outer.calls.append(("comment", kw))
                return {"id": "c"}

            async def list(self, **kw):
                return {"results": [{"rich_text": [{"plain_text": t}]} for t in existing]}

        class Children:
            async def append(self, **kw):
                outer.calls.append(("append", kw))
                return {"results": [{"id": "callout-1"}]}

            async def list(self, **kw):
                return {"results": []}

        class Blocks:
            children = Children()

        self.comments, self.blocks = Comments(), Blocks()


def _record(**kw) -> dict:
    r = {"id": "rec1", "topic": "목록 이동 체크", "area": "북마크", "status": "최종결정", "decision": "현재 폴더 체크",
         "scope": None, "decided_at": "2026-10-20T10:00:00+09:00",
         "source": {"thread_url": "https://discord.com/channels/1/2", "thread_name": "가짜 스레드", "thread_id": "2"}}
    r.update(kw)
    return r


def _text(rich: list[dict]) -> str:
    return "".join(p["text"]["content"] if p["type"] == "text" else "@" for p in rich)


def test_modify_comments_on_the_row_with_links_and_mention(monkeypatch):
    monkeypatch.setattr(spec_reflect, "NOTION_PM_USER_IDS", ["user-pm"])
    doc, fake = sample_doc(), FakeNotion()
    m = Match(1, "modify", "XX-05-02", "XX-05", current="체크 표시", proposal="체크 아이콘 노출")
    out = asyncio.run(apply_match(fake, doc, _record(), m, "https://notion.so/rec1"))
    kind, kw = fake.calls[0]
    assert kind == "comment" and kw["parent"] == {"block_id": doc.by_id()["XX-05-02"].block_id}
    assert kw["rich_text"][0]["mention"]["user"]["id"] == "user-pm"
    text = _text(kw["rich_text"])
    assert "제안: 체크 아이콘 노출" in text and "현재: 체크 표시" in text and "기록 rec1" in text
    links = [p["text"]["link"]["url"] for p in kw["rich_text"] if p["type"] == "text" and p["text"]["link"]]
    assert links == ["https://discord.com/channels/1/2", "https://notion.so/rec1"]
    assert out.url.endswith("#" + doc.by_id()["XX-05-02"].block_id.replace("-", ""))  # 노션 블록 링크는 하이픈 없음


def test_modify_skips_when_same_record_already_commented():
    doc, fake = sample_doc(), FakeNotion(existing_comments=["💡 정책봇 반영 제안 … 기록 rec1"])
    out = asyncio.run(apply_match(fake, doc, _record(), Match(1, "modify", "XX-05-02", "XX-05"), None))
    assert fake.calls == [] and out.note == "이미 같은 기록으로 제안함"


def test_new_inserts_callout_after_screen_table(monkeypatch):
    monkeypatch.setattr(spec_reflect, "NOTION_PM_USER_IDS", [])
    doc, fake = sample_doc(), FakeNotion()
    asyncio.run(apply_match(fake, doc, _record(), Match(1, "new", screen_id="XX-05", proposal="새 정책"), None))
    kind, kw = fake.calls[0]
    anchor = doc.by_id()["XX-05-02"]
    assert kind == "append" and kw["block_id"] == anchor.parent_id
    assert kw["position"] == {"type": "after_block", "after_block": {"id": anchor.table_id}}
    callout = kw["children"][0]["callout"]
    assert callout["color"] == "yellow_background" and "새 정책" in _text(callout["rich_text"])
    assert len(fake.calls) == 1  # PM 미설정이면 멘션 댓글 없음


def test_unknown_comments_on_page_and_same_does_nothing():
    doc, fake = sample_doc(), FakeNotion()
    asyncio.run(apply_match(fake, doc, _record(), Match(1, "unknown", proposal="어딘가"), None))
    assert fake.calls[0][1]["parent"] == {"page_id": PAGE["id"]}
    fake.calls.clear()
    out = asyncio.run(apply_match(fake, doc, _record(), Match(1, "same", "XX-05-02", "XX-05"), None))
    assert fake.calls == [] and out.action == "same"


def test_reflect_skips_non_final_and_unconfigured_areas(monkeypatch):
    monkeypatch.setattr(spec_reflect, "NOTION_SPEC_PAGES", {"북마크": "0" * 32})
    monkeypatch.setattr(spec_reflect, "NOTION_SPEC_INBOX_PAGE", "")  # 미분류 페이지가 없으면 건너뜀으로 보고
    monkeypatch.setattr(spec_reflect.notion_store, "enabled", lambda: True)
    monkeypatch.setattr(spec_reflect.notion_store, "get_client", lambda: FakeNotion())

    async def fake_load(client, page_id, cache_dir):
        return sample_doc()

    async def fake_match(doc, records, user=None):
        return [Match(i, "same", "XX-05-02", "XX-05") for i in range(1, len(records) + 1)]

    monkeypatch.setattr(spec_reflect, "load_spec", fake_load)
    monkeypatch.setattr(spec_reflect, "match_records", fake_match)
    monkeypatch.setattr(spec_reflect, "_log_outcomes", lambda *a: None)
    saved = [(_record(id="a"), None), (_record(id="b", status="논의중"), None), (_record(id="c", area="리뷰"), None)]
    outs = asyncio.run(reflect_records(saved))
    assert {o.record_id: o.action for o in outs} == {"a": "same", "b": "skipped", "c": "skipped"}
    lines = format_outcomes(outs)
    assert lines[0] == "📚 기준 정책서 반영 제안" and any("논의중" in l for l in lines)


def test_reflect_falls_back_to_default_spec(monkeypatch):
    monkeypatch.setattr(spec_reflect, "NOTION_SPEC_PAGES", {"*": "0" * 32})
    monkeypatch.setattr(spec_reflect, "NOTION_SPEC_INBOX_PAGE", "")
    monkeypatch.setattr(spec_reflect.notion_store, "enabled", lambda: True)
    monkeypatch.setattr(spec_reflect.notion_store, "get_client", lambda: FakeNotion())
    seen = []

    async def fake_load(client, page_id, cache_dir):
        seen.append(page_id)
        return sample_doc()

    async def fake_match(doc, records, user=None):
        return [Match(i, "same", "XX-05-02", "XX-05") for i in range(1, len(records) + 1)]

    monkeypatch.setattr(spec_reflect, "load_spec", fake_load)
    monkeypatch.setattr(spec_reflect, "match_records", fake_match)
    monkeypatch.setattr(spec_reflect, "_log_outcomes", lambda *a: None)
    outs = asyncio.run(reflect_records([(_record(id="a", area="리뷰"), None), (_record(id="b"), None)]))
    assert [o.action for o in outs] == ["same", "same"] and seen == ["0" * 32]  # 같은 정책서는 매칭 1회


INBOX = "1" * 32


def _patch_reflect(monkeypatch, pages: dict, action: str, fake: FakeNotion):
    monkeypatch.setattr(spec_reflect, "NOTION_SPEC_PAGES", pages)
    monkeypatch.setattr(spec_reflect, "NOTION_SPEC_INBOX_PAGE", INBOX)
    monkeypatch.setattr(spec_reflect, "NOTION_PM_USER_IDS", [])
    monkeypatch.setattr(spec_reflect.notion_store, "enabled", lambda: True)
    monkeypatch.setattr(spec_reflect.notion_store, "get_client", lambda: fake)

    async def fake_load(client, page_id, cache_dir):
        return sample_doc()

    async def fake_match(doc, records, user=None):
        return [Match(i, action, proposal="제안 문장", reason="화면 없음") for i in range(1, len(records) + 1)]

    monkeypatch.setattr(spec_reflect, "load_spec", fake_load)
    monkeypatch.setattr(spec_reflect, "match_records", fake_match)
    monkeypatch.setattr(spec_reflect, "_log_outcomes", lambda *a: None)


def test_unknown_from_default_spec_goes_to_inbox(monkeypatch):
    fake = FakeNotion()
    _patch_reflect(monkeypatch, {"*": "0" * 32}, "unknown", fake)
    outs = asyncio.run(reflect_records([(_record(area="계정·프로필"), "https://notion.so/rec1")]))
    assert outs[0].action == "inbox" and outs[0].url.startswith(f"https://www.notion.so/{INBOX}#")
    kind, kw = fake.calls[0]
    assert kind == "append" and kw["block_id"] == INBOX
    todo = kw["children"][0]["to_do"]
    text = _text(todo["rich_text"])
    assert todo["checked"] is False and text.startswith("[계정·프로필] 목록 이동 체크")
    assert "제안: 제안 문장" in text and "기록 rec1" in text
    assert "미분류 제안" in format_outcomes(outs)[1]


def test_unknown_in_own_area_spec_stays_page_comment(monkeypatch):
    fake = FakeNotion()
    _patch_reflect(monkeypatch, {"북마크": "0" * 32, "*": "0" * 32}, "unknown", fake)
    outs = asyncio.run(reflect_records([(_record(), None)]))
    assert outs[0].action == "unknown" and fake.calls[0][1]["parent"] == {"page_id": PAGE["id"]}


def test_area_without_any_spec_goes_to_inbox_without_matching(monkeypatch):
    fake = FakeNotion()
    _patch_reflect(monkeypatch, {"북마크": "0" * 32}, "same", fake)

    async def no_match(*a, **kw):
        raise AssertionError("정책서가 없으면 매칭(LLM)을 부르지 않는다")

    monkeypatch.setattr(spec_reflect, "match_records", no_match)
    outs = asyncio.run(reflect_records([(_record(area="리뷰"), None)]))
    assert outs[0].action == "inbox" and outs[0].note == "'리뷰' 정책서 없음"
    assert "제안: 현재 폴더 체크" in _text(fake.calls[0][1]["children"][0]["to_do"]["rich_text"])


def test_inbox_skips_record_already_there(monkeypatch):
    class WithExisting(FakeNotion):
        def __init__(self):
            super().__init__()
            outer = self

            class Children:
                async def list(self, **kw):
                    return {"results": [{"type": "to_do", "to_do": {"rich_text": [{"plain_text": "… · 기록 rec1"}]}}]}

                async def append(self, **kw):
                    outer.calls.append(("append", kw))
                    return {"results": [{"id": "x"}]}

            self.blocks.children = Children()

    fake = WithExisting()
    _patch_reflect(monkeypatch, {}, "unknown", fake)
    outs = asyncio.run(reflect_records([(_record(area="리뷰"), None)]))
    assert fake.calls == [] and outs[0].note == "이미 미분류 페이지에 있음"


def test_reflect_is_noop_without_spec_pages(monkeypatch):
    monkeypatch.setattr(spec_reflect, "NOTION_SPEC_INBOX_PAGE", "")
    monkeypatch.setattr(spec_reflect, "NOTION_SPEC_PAGES", {})
    assert asyncio.run(reflect_records([(_record(), None)])) == []
