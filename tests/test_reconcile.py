"""원장 ↔ 노션 대조(A-5d): 손으로 만든 어긋남을 찾아낸다. 네트워크 없음."""
import asyncio

from app.ledger import Ledger
from app.notion import schema as ns
from app.outbox import NOTION_UPSERT, Outbox, op_key
from app.services import reconcile


def _record(rid, **kw):
    r = {"id": rid, "version": 1, "area": "리뷰", "topic": f"주제 {rid}", "status": "최종결정", "decision": "A",
         "rationale": None, "decided_by": None, "scope": None, "external_refs": [], "open_questions": [],
         "source": {"thread_id": "t", "thread_url": "u", "thread_name": "n"},
         "decided_at": "2026-06-01T10:00:00+09:00", "approved_by": "a", "approved_at": "2026-06-02T11:00:00+09:00"}
    return r | kw


def _page(record, **override):
    """build_properties → 노션이 돌려주는 모양. override로 사람이 고친 값을 흉내 낸다."""
    props = {}
    for name, v in ns.build_properties(record).items():
        t = next(iter(v))
        val = v[t]
        if t in ("title", "rich_text"):
            val = [{"plain_text": x["text"]["content"]} for x in val]
        props[name] = {"type": t, t: val}
    for name, text in override.items():
        t = props[name]["type"]
        props[name][t] = {"name": text} if t == "select" else [{"plain_text": text}]
    return {"object": "page", "id": f"page-{record['id']}", "url": f"https://notion.so/{record['id']}",
            "properties": props}


def test_compare_finds_each_kind_of_drift(tmp_path):
    led, outbox = Ledger(tmp_path), Outbox(tmp_path)
    ok, edited, stale_v1, missing, deleted, pending = (_record(x) for x in ("ok", "edt", "stl", "mis", "del", "pnd"))
    for r in (ok, edited, stale_v1, missing, deleted):
        led.append(r)
    stale_v2 = stale_v1 | {"version": 2, "decision": "B", "approved_at": "2026-06-05T09:00:00+09:00"}
    led.append(stale_v2)
    led.append(pending | {"ops": [NOTION_UPSERT]})                           # 처리기가 할 일
    outbox.done(op_key(deleted, NOTION_UPSERT), notion_url="u")              # 저장됐었는데 지금 노션에 없음
    manual = {"object": "page", "id": "page-x", "url": "https://notion.so/x",
              "properties": {ns.TITLE: {"type": "title", "title": [{"plain_text": "손으로 만든 페이지"}]}}}
    pages = [_page(ok), _page(edited, **{ns.STATUS: "논의중", ns.DECISION: "PM이 고친 결정"}), _page(stale_v1), manual]

    out = reconcile.compare(led, outbox, pages)
    ids = {k: [i.get("id") for i in out[k]] for k in ("pending", "missing", "deleted", "stale", "edited")}
    assert ids == {"pending": ["pnd"], "missing": ["mis"], "deleted": ["del"], "stale": ["stl"], "edited": ["edt"]}
    assert out["edited"][0]["fields"] == ["status", "decision"]
    assert [p["title"] for p in out["unknown_pages"]] == ["손으로 만든 페이지"]
    assert not out["ok"]


def test_clean_state_is_ok(tmp_path):
    led = Ledger(tmp_path)
    r = _record("a")
    led.append(r)
    out = reconcile.compare(led, Outbox(tmp_path), [_page(r)])
    assert out["ok"] and all(v == 0 for v in out["counts"].values())


def test_fix_missing_creates_only_missing(tmp_path):
    led = Ledger(tmp_path)
    led.append(_record("mis"))
    created = []

    class Repo:
        async def add(self, record):
            created.append(record["id"])
            return "https://notion.so/new"

        async def update(self, record, previous):
            raise AssertionError("missing은 새로 만든다")

    report = reconcile.compare(led, Outbox(tmp_path), [])
    urls = asyncio.run(reconcile.fix_missing(str(tmp_path), Repo(), report))
    assert created == ["mis"] and urls == ["https://notion.so/new"]
