"""재정리·갱신(Q-7) 테스트: 같은 스레드를 다시 정리하면 바뀐 결정만 이전 기록을 덮어쓰고, 이전 버전은 변경 이력으로 남는다.
네트워크 없음. 데이터는 전부 가짜."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import notion_store
import spec_reflect
import storage
from prompts import build_user_prompt, format_previous
from summarizer import ThreadMessage, build_transcript, normalize
from views import ApprovalView, relation_fields, set_relation_by_status, toggle_link

T0 = datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc)


def _msgs(n: int = 3) -> list[ThreadMessage]:
    return [ThreadMessage(i, f"작성자{i}", T0 + timedelta(hours=i), f"메시지 {i}", f"https://discord.com/channels/1/2/{i}")
            for i in range(1, n + 1)]


def _decision(**kw) -> dict:
    d = {"area": "리뷰", "topic": "주제", "status": "최종결정", "decision": "결정", "rationale": None,
         "decided_by": None, "scope": None, "external_refs": [], "open_questions": [], "source_messages": [1]}
    d.update(kw)
    return d


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(storage.notion_store, "enabled", lambda: False)
    return tmp_path


def _save(decisions, msgs=None):
    msgs = msgs or _msgs()
    source = storage.thread_source("thread-1", "가짜 스레드", "https://discord.com/channels/1/2", msgs)
    return asyncio.run(storage.save_decisions(decisions, source=source, approved_by="승인자"))


# --- 저장소: 버전 ---
def test_update_overwrites_same_id_with_new_version(tmp_store):
    first = _save([_decision(topic="필터", decision="A")])[0].record
    assert first["version"] == 1 and first["source"]["summarized_until"].startswith("2026-06-01T15:00")
    res = _save([_decision(topic="필터", decision="A+B", relation="update", prior_id=first["id"])])[0]
    assert res.record["id"] == first["id"] and res.record["version"] == 2 and res.updated
    assert [r["decision"] for r in res.previous] == ["A"]
    # 로컬은 append-only: 두 줄이 남고 현재 버전은 마지막 줄
    assert len(storage.load_versions()[first["id"]]) == 2
    assert [r["decision"] for r in storage.current_records("thread-1")] == ["A+B"]
    assert "prior_id" not in res.record and res.record["relation"] == "update"


def test_reopen_keeps_previous_and_links(tmp_store):
    first = _save([_decision(decision="확정안")])[0].record
    res = _save([_decision(status="논의중", decision="변경 논의", relation="reopen", prior_id=first["id"])])[0]
    assert res.record["id"] != first["id"] and res.record["reopens"] == first["id"] and not res.updated
    assert sorted(r["decision"] for r in storage.current_records("thread-1")) == ["변경 논의", "확정안"]


def test_resummary_context_skips_when_nothing_new(tmp_store):
    msgs = _msgs(3)
    _save([_decision()], msgs)
    assert storage.resummary_context("thread-1", msgs).nothing_new
    more = msgs + [ThreadMessage(4, "작성자4", T0 + timedelta(hours=9), "새 의견", "u4")]
    ctx = storage.resummary_context("thread-1", more)
    assert not ctx.nothing_new and len(ctx.previous) == 1
    assert not storage.resummary_context("other-thread", msgs).nothing_new  # 처음 정리


def test_old_records_without_summarized_until_are_resummarized(tmp_store):
    rec = _save([_decision()])[0].record
    rec["source"].pop("summarized_until")
    assert storage.summarized_until([rec]) is None
    assert not storage.ResummaryContext([rec], None).nothing_new


# --- 요약: 관계 판정 ---
PRIOR = {"aaa": {"id": "aaa", "status": "최종결정"}, "bbb": {"id": "bbb", "status": "논의중"}}


def _rel(items: list[dict], prior=PRIOR) -> list[tuple]:
    out = normalize({"decisions": [_decision(**i) for i in items]}, max_index=3, prior=prior)
    return [(d["relation"], d["prior_id"]) for d in out]


def test_relation_without_prior_is_always_new():
    assert _rel([{"relation": "update", "prior_id": "aaa"}], prior=None) == [("new", None)]


def test_relation_normalization_is_conservative():
    assert _rel([
        {"relation": "unchanged", "prior_id": "aaa"},                      # 그대로
        {"relation": "update", "prior_id": "zzz"},                         # 목록에 없는 ID → new
        {"relation": "update", "prior_id": "aaa"},                         # 이미 앞에서 가져간 ID → new
        {"relation": "unchanged", "prior_id": "bbb", "status": "최종결정"},  # 상태가 바뀜 → update
    ]) == [("unchanged", "aaa"), ("new", None), ("new", None), ("update", "bbb")]


def test_final_is_not_overwritten_by_discussion():
    assert _rel([{"relation": "update", "prior_id": "aaa", "status": "논의중"}]) == [("reopen", "aaa")]


def test_prompt_marks_new_messages_and_lists_previous():
    msgs = _msgs(3)
    transcript = build_transcript(msgs, new_after=T0 + timedelta(hours=2))
    assert "[#3]" in transcript and transcript.count("[새 메시지]") == 1
    prev = format_previous([{"id": "aaa", "area": "리뷰", "status": "최종결정", "topic": "필터", "decision": "A"}])
    prompt = build_user_prompt("스레드", transcript, prev)
    assert "[기록 aaa]" in prompt and '"unchanged"' in prompt and "현재 전체 정책" in prompt
    assert "previous_records" not in build_user_prompt("스레드", transcript)  # 처음 정리는 프롬프트 그대로


# --- 노션: 덮어쓰기 + 변경 이력 ---
class FakeNotion:
    def __init__(self, old_blocks=2):
        self.calls = []
        outer = self

        class Pages:
            async def update(self, **kw):
                outer.calls.append(("update", kw))

        class Children:
            async def list(self, **kw):
                return {"results": [{"id": f"old-{i}"} for i in range(old_blocks)]}

            async def append(self, **kw):
                outer.calls.append(("append", kw))

        class Blocks:
            children = Children()

            async def delete(self, **kw):
                outer.calls.append(("delete", kw))

        self.pages, self.blocks = Pages(), Blocks()


def _record(**kw) -> dict:
    r = {"id": "aaa", "version": 2, "area": "리뷰", "topic": "필터", "status": "최종결정", "decision": "A+B",
         "rationale": None, "decided_by": None, "scope": None, "external_refs": [], "open_questions": [],
         "source": {"thread_url": "u", "thread_name": "t", "messages": []},
         "decided_at": "2026-06-02T10:00:00+09:00", "approved_by": "승인자2", "approved_at": "2026-06-02T11:00:00+09:00"}
    r.update(kw)
    return r


def test_update_record_rewrites_page_with_history(monkeypatch):
    fake = FakeNotion()
    monkeypatch.setattr(notion_store, "get_client", lambda: fake)

    async def page(record_id):
        return {"id": "page-1", "url": "https://notion.so/page-1"}

    monkeypatch.setattr(notion_store, "find_record_page", page)
    v1 = _record(version=1, decision="A", approved_by="승인자1", approved_at="2026-06-01T11:00:00+09:00")
    url = asyncio.run(notion_store.update_record(_record(), [v1]))
    kinds = [k for k, _ in fake.calls]
    assert url == "https://notion.so/page-1" and kinds == ["update", "delete", "delete", "append"]
    assert fake.calls[0][1]["properties"]["결정"]["rich_text"][0]["text"]["content"] == "A+B"
    blocks = fake.calls[-1][1]["children"]
    texts = [b[b["type"]]["rich_text"][0]["text"]["content"] for b in blocks]
    assert "변경 이력" in texts
    toggle = next(b for b in blocks if b["type"] == "toggle")
    assert toggle["toggle"]["rich_text"][0]["text"]["content"].startswith("v1 · 2026-06-01 정리 · 최종결정 · 승인 승인자1")
    assert toggle["toggle"]["children"][0]["paragraph"]["rich_text"][0]["text"]["content"] == "결정: A"


def test_history_blocks_newest_first():
    blocks = notion_store.history_blocks([_record(version=1, decision="A"), _record(version=2, decision="B")])
    titles = [b["toggle"]["rich_text"][0]["text"]["content"] for b in blocks if b["type"] == "toggle"]
    assert titles[0].startswith("v2") and titles[1].startswith("v1")
    assert notion_store.history_blocks([]) == []


# --- 승인 화면 ---
def test_toggle_link_round_trip_and_status_rule():
    d = _decision(relation="update", prior_id="aaa")
    toggle_link(d)
    assert (d["relation"], d["prior_id"], d["unlinked_from"]) == ("new", None, ["update", "aaa"])
    toggle_link(d)
    assert (d["relation"], d["prior_id"]) == ("update", "aaa") and "unlinked_from" not in d
    d["status"] = "논의중"
    set_relation_by_status(d, PRIOR)
    assert d["relation"] == "reopen"   # 수정 모달에서 확정 → 논의중으로 바꾸면 덮어쓰지 않음


def test_view_shows_previous_and_unchanged_count():
    async def run():
        prev = [{"id": "aaa", "status": "최종결정", "decision": "이전 결정 A"}]
        ds = normalize({"decisions": [_decision(relation="update", prior_id="aaa")]}, max_index=3,
                       prior={"aaa": prev[0]})
        v = ApprovalView(owner_id=1, decisions=ds, source={}, messages=_msgs(), previous=prev, unchanged=2)
        out = v.render()
        assert "변경 1건 · 변동 없음 2건" in out["content"]
        names = [f.name for f in out["embeds"][0].fields]
        assert any("이전 기록 aaa을 덮어씀" in n for n in names)
        assert out["embeds"][0].title.startswith("🔁")
        assert not v.toggle_link.disabled and v.toggle_link in v.children
        first = ApprovalView(owner_id=1, decisions=normalize({"decisions": [_decision()]}, max_index=3),
                             source={}, messages=_msgs())
        assert first.toggle_link not in first.children  # 처음 정리에는 연결 버튼이 없다
    asyncio.run(run())


def test_relation_fields_for_unlinked():
    d = {"relation": "new", "prior_id": None, "unlinked_from": ["update", "aaa"]}
    assert relation_fields(d, {})[0][0] == "🔗 연결 해제됨"


# --- 정책서 반영: 변경은 이전 제안 스레드에 답글 ---
class FakeComments:
    def __init__(self, texts):
        self.existing = [{"discussion_id": f"disc-{i}", "rich_text": [{"plain_text": t}]} for i, t in enumerate(texts)]
        self.created = []

    async def list(self, **kw):
        return {"results": self.existing}

    async def create(self, **kw):
        self.created.append(kw)


class Client:
    def __init__(self, texts):
        self.comments = FakeComments(texts)


def test_v2_proposal_replies_in_previous_discussion():
    c = Client(["💡 정책봇 반영 제안 … 기록 aaa"])
    note = asyncio.run(spec_reflect.post_proposal(c, "row-1", {"id": "aaa", "version": 2}, [{"t": 1}]))
    assert note == "이전 제안 스레드에 답글" and c.comments.created == [{"discussion_id": "disc-0", "rich_text": [{"t": 1}]}]


def test_same_version_is_not_posted_twice_and_first_posts_new():
    c = Client(["… 기록 aaa v2"])
    assert asyncio.run(spec_reflect.post_proposal(c, "row-1", {"id": "aaa", "version": 2}, [])) == "이미 같은 기록으로 제안함"
    c = Client([])
    assert asyncio.run(spec_reflect.post_proposal(c, "page-1", {"id": "aaa", "version": 1}, [], page=True)) is None
    assert c.comments.created[0]["parent"] == {"page_id": "page-1"}
    assert spec_reflect.marker({"id": "aaa", "version": 2}) == "기록 aaa v2"
