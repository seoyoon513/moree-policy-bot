"""/정책정리 흐름 테스트: 대화록(리액션) → 정규화(URL) → 저장 레코드(결정일·발췌) → 노션 속성 → 승인 화면(수정·제외).
네트워크 없이 동작. 데이터는 전부 가짜."""
import asyncio
from datetime import datetime, timedelta, timezone

import notion_store
import storage
from prompts import SYSTEM_PROMPT
from summarizer import ThreadMessage, extract_urls, normalize
from thread_reader import agreed_users, to_thread_messages
from views import ApprovalView, EditModal, apply_edit, build_embeds

T0 = datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc)  # KST 12:00


def _msgs(n: int = 3) -> list[ThreadMessage]:
    return [
        ThreadMessage(i, f"작성자{i}", T0 + timedelta(hours=i), f"메시지 {i}", f"https://discord.com/channels/1/2/{i}")
        for i in range(1, n + 1)
    ]


def _decision(**kw) -> dict:
    d = {"area": "리뷰", "topic": "주제", "status": "논의중", "decision": "결정", "rationale": None,
         "decided_by": None, "scope": None, "external_refs": [], "open_questions": [], "source_messages": [1]}
    d.update(kw)
    return d


# --- 프롬프트 ---
def test_prompt_uses_signals_not_roles():
    assert "결정 권한자" not in SYSTEM_PROMPT
    assert "확정 신호" in SYSTEM_PROMPT and "동의 리액션" in SYSTEM_PROMPT


# --- 외부참조: URL만 ---
def test_extract_urls_keeps_only_urls():
    refs = ["피그마: https://figma.com/design/abc?node-id=1", "시안 이미지 (#4)", "https://figma.com/design/abc?node-id=1",
            "문서 (https://example.com/doc)"]
    assert extract_urls(refs) == ["https://figma.com/design/abc?node-id=1", "https://example.com/doc"]


def test_normalize_drops_non_url_refs():
    out = normalize({"decisions": [_decision(external_refs=["피그마 참고"])]}, max_index=3)
    assert out[0]["external_refs"] == []


# --- 리액션 ---
def test_transcript_line_shows_agreement():
    m = ThreadMessage(1, "A", T0, "이걸로 할까요?", "u", agreed_by=["B", "C"])
    assert m.to_line().endswith("이걸로 할까요? [동의 리액션: B, C]")
    assert "동의 리액션" not in ThreadMessage(2, "A", T0, "x", "u").to_line()


class _User:
    def __init__(self, uid, name, bot=False):
        self.id, self.display_name, self.bot = uid, name, bot


class _Reaction:
    def __init__(self, emoji, users):
        self.emoji, self._users = emoji, users

    async def users(self):
        for u in self._users:
            yield u


class _Message:
    def __init__(self, author, content, reactions=()):
        self.author, self.content, self.reactions = author, content, list(reactions)
        self.attachments, self.embeds = [], []
        self.created_at, self.jump_url = T0, "https://discord.com/channels/1/2/3"


def test_agreed_users_skips_author_bots_and_other_emojis():
    author, b, c, bot = _User(1, "작성자"), _User(2, "B"), _User(3, "C"), _User(9, "봇", bot=True)
    m = _Message(author, "제안", [
        _Reaction("👍", [author, b, bot]),
        _Reaction("✅", [b, c]),
        _Reaction("👀", [c]),  # 동의 이모지가 아님
    ])
    assert asyncio.run(agreed_users(m)) == ["B", "C"]


def test_to_thread_messages_numbers_humans_only():
    human, bot = _User(1, "A"), _User(9, "봇", bot=True)
    raw = [_Message(human, "첫 메시지"), _Message(bot, "봇 메시지"), _Message(human, "  "),
           _Message(human, "둘째", [_Reaction("👍", [_User(2, "B")])])]
    out = asyncio.run(to_thread_messages(raw))
    assert [(m.index, m.content, m.agreed_by) for m in out] == [(1, "첫 메시지", []), (2, "둘째", ["B"])]


# --- 결정일 · 정리일 · 발췌 ---
def test_decided_at_is_latest_source_message():
    by_index = storage.messages_by_index(_msgs())
    assert storage.decided_at([1, 2], by_index) == "2026-06-01T14:00:00+09:00"
    assert storage.decided_at([], by_index) == "2026-06-01T15:00:00+09:00"  # 근거 없으면 마지막 메시지
    assert storage.decided_at([], {}) is None


def test_build_record_separates_decided_and_approved():
    msgs = _msgs()
    source = {"thread_id": "2", "thread_name": "t", "thread_url": "u", "messages_by_index": storage.messages_by_index(msgs)}
    now = datetime(2026, 10, 26, 12, 0, tzinfo=storage.KST)
    r = storage._build_record(_decision(source_messages=[2, 1]), source, "승인자", now)
    assert r["decided_at"] == "2026-06-01T14:00:00+09:00"
    assert r["approved_at"] == "2026-10-26T12:00:00+09:00"
    assert r["source"]["message_urls"] == [msgs[1].jump_url, msgs[0].jump_url]
    assert [m["text"] for m in r["source"]["messages"]] == ["메시지 2", "메시지 1"]
    assert "messages_by_index" not in r["source"] and "source_messages" not in r


def test_excerpt_is_cut():
    long = ThreadMessage(1, "A", T0, "가" * 1000, "u")
    assert len(storage.messages_by_index([long])[1]["text"]) == storage.EXCERPT_CHARS


def _record(**kw) -> dict:
    r = {"id": "abc", "topic": "주제", "area": "리뷰", "status": "최종결정", "decision": "결정", "rationale": None,
         "decided_by": None, "scope": None, "external_refs": [], "open_questions": [],
         "source": {"thread_url": "u", "thread_name": "t", "message_urls": ["https://d/1"],
                    "messages": [{"url": "https://d/1", "author": "A", "at": "2026-06-01T12:00:00+09:00", "text": "원문"}]},
         "decided_at": "2026-06-01T12:00:00+09:00", "approved_by": "x", "approved_at": "2026-10-26T12:00:00+09:00"}
    r.update(kw)
    return r


def test_notion_properties_have_both_dates():
    p = notion_store.build_properties(_record())
    assert p["결정일"]["date"]["start"] == "2026-06-01T12:00:00+09:00"
    assert p["정리일"]["date"]["start"] == "2026-10-26T12:00:00+09:00"
    assert "승인일" not in p


def test_notion_properties_send_reopens_only_when_set():
    # 재논의대상 속성이 없는 옛 DB에서도 일반 저장이 되도록 값이 있을 때만 보낸다
    assert "재논의대상" not in notion_store.build_properties(_record())
    p = notion_store.build_properties(_record(reopens="old00001"))
    assert p["재논의대상"]["rich_text"][0]["text"]["content"] == "old00001"


def test_notion_properties_fall_back_for_old_records():
    r = _record()
    del r["decided_at"]
    assert notion_store.build_properties(r)["결정일"]["date"]["start"] == r["approved_at"]


def test_notion_body_has_linked_excerpts():
    blocks = notion_store.build_children(_record())
    bullet = blocks[-1]["bulleted_list_item"]["rich_text"][0]
    assert bullet["text"]["content"] == "A (06-01 12:00): 원문"
    assert bullet["text"]["link"] == {"url": "https://d/1"}


# --- 승인 화면 ---
def test_apply_edit_tracks_changed_fields():
    d = _decision(topic="원래 주제", scope=None)
    changed = apply_edit(d, topic="원래 주제", status="최종결정", decision="결정", rationale="", scope="후기 작성 화면")
    assert changed == ["status", "scope"]
    assert d["status"] == "최종결정" and d["scope"] == "후기 작성 화면" and d["rationale"] is None
    apply_edit(d, topic="새 주제", status="잘못된값", decision="결정", rationale="", scope="후기 작성 화면")
    assert d["status"] == "최종결정"  # 목록에 없는 상태는 무시
    assert d["edited_fields"] == ["scope", "status", "topic"]


def test_excluded_decision_is_marked_in_embed():
    ds = normalize({"decisions": [_decision(topic="A"), _decision(topic="B")]}, max_index=3)
    embeds = build_embeds(ds, _msgs(), excluded={1})
    assert embeds[0].title.endswith("A") and "(제외)" in embeds[1].title
    assert embeds[1].description is None


def _view(n=2) -> ApprovalView:
    ds = normalize({"decisions": [_decision(topic=f"주제{i}") for i in range(n)]}, max_index=3)
    return ApprovalView(owner_id=1, decisions=ds, source={}, messages=_msgs())


def test_view_exclude_toggle_and_render():
    async def run():
        v = _view()
        assert [o.default for o in v.pick.options] == [True, False]
        v.selected = 1
        v.excluded ^= {1}
        out = v.render()
        assert "제외 1건 → 1건 저장" in out["content"]
        assert v.toggle_exclude.label == "↩️ 다시 포함" and v.edit.disabled
        assert v.pick.options[1].description == "🚫 제외됨"
        assert [d["topic"] for d in v.kept_decisions()] == ["주제0"]
        v.excluded ^= {1}
        v.render()
        assert v.toggle_exclude.label == "🚫 제외" and not v.edit.disabled
    asyncio.run(run())


def test_edit_modal_prefills_current_values():
    async def run():
        v = _view(1)
        v.decisions[0]["scope"] = "화면"
        m = EditModal(v, 0)
        assert m.topic.default == "주제0" and m.scope.default == "화면" and m.rationale.default == ""
        assert [o.value for o in m.status.options if o.default] == ["논의중"]
        assert len(m.children) == 5  # 모달 최대 5칸
    asyncio.run(run())
