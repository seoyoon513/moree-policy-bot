"""노션 쓰기 멱등화(A-5b): 중간에 실패하거나 같은 작업을 다시 실행해도 최종 상태가 같다.
상태를 기억하는 가짜 노션(페이지·블록)으로 "N번째 호출에서 실패"를 주입한다. 네트워크 없음."""
import asyncio
import itertools

import httpx
import pytest
from notion_client import APIResponseError

from app.notion.decisions import NotionDecisionRepository, build_children, history_blocks
from app.notion.schema import APPROVED_AT, RECORD_ID
from app.services.followups import classify_failure


def _record(**kw):
    r = {"id": "rec00001", "version": 1, "area": "리뷰", "topic": "리뷰 사진", "status": "최종결정",
         "decision": "최대 5장", "rationale": None, "decided_by": None, "scope": None, "external_refs": [],
         "open_questions": [], "source": {"thread_url": "u", "thread_name": "t", "messages": []},
         "decided_at": "2026-06-01T10:00:00+09:00", "approved_by": "승인자", "approved_at": "2026-06-02T11:00:00+09:00"}
    return r | kw


class FakeNotion:
    """pages.create/update, data_sources.query(기록ID), blocks.children.list/append, blocks.delete만 흉내 낸다.
    fail_on={"delete": 2} → 두 번째 delete 호출에서 예외. timeout_after_create → 만들고 나서 응답 대신 예외."""

    def __init__(self, fail_on=None, timeout_after_create=False):
        self.pages: dict[str, dict] = {}      # page_id → {"properties", "blocks": [block_id]}
        self.counts: dict[str, int] = {}
        self.fail_on = dict(fail_on or {})
        self.timeout_after_create = timeout_after_create
        self._ids = (f"b{i}" for i in itertools.count())
        outer = self

        def tick(name):
            outer.counts[name] = outer.counts.get(name, 0) + 1
            if outer.fail_on.get(name) == outer.counts[name]:
                raise RuntimeError(f"주입한 실패: {name} #{outer.counts[name]}")

        def stored(props):  # 요청 모양 → 노션이 돌려주는 모양 (rich_text·date에 type)
            out = {}
            for k, v in props.items():
                t = next(iter(v))
                val = v[t]
                if t in ("title", "rich_text"):
                    val = [{"plain_text": x["text"]["content"]} for x in val]
                if t == "date" and val:
                    val = {"start": val["start"].replace("+09:00", ".000+09:00")}  # 노션은 밀리초를 붙인다
                out[k] = {"type": t, t: val}
            return out

        class Pages:
            async def create(self, parent, properties, children):
                tick("create")
                pid = f"page-{len(outer.pages) + 1}"
                outer.pages[pid] = {"properties": stored(properties), "blocks": [next(outer._ids) for _ in children]}
                if outer.timeout_after_create:
                    outer.timeout_after_create = False
                    raise TimeoutError("응답을 못 받음 (생성은 됨)")
                return {"id": pid, "url": f"https://notion.so/{pid}"}

            async def update(self, page_id, properties):
                tick("update")
                outer.pages[page_id]["properties"] |= stored(properties)

        class DataSources:
            async def query(self, data_source_id, page_size, filter):
                rid = filter["rich_text"]["equals"]
                hits = [{"id": pid, "url": f"https://notion.so/{pid}", "properties": p["properties"]}
                        for pid, p in outer.pages.items()
                        if [x["plain_text"] for x in p["properties"][RECORD_ID]["rich_text"]] == [rid]]
                return {"results": hits[:page_size]}

        class Children:
            async def list(self, block_id, page_size, start_cursor=None):
                return {"results": [{"id": b} for b in outer.pages[block_id]["blocks"]], "has_more": False}

            async def append(self, block_id, children):
                tick("append")
                outer.pages[block_id]["blocks"] += [next(outer._ids) for _ in children]

        class Blocks:
            children = Children()

            async def delete(self, block_id):
                tick("delete")
                for p in outer.pages.values():
                    if block_id in p["blocks"]:
                        p["blocks"].remove(block_id)

        self.pages_api, self.blocks = Pages(), Blocks()
        self.data_sources = DataSources()

    @property
    def pages_(self):
        return self.pages

    def __getattr__(self, name):
        raise AttributeError(name)


def _repo(fake):
    class Client:  # repo가 쓰는 이름(pages, blocks, data_sources)으로 연결
        pages = fake.pages_api
        blocks = fake.blocks
        data_sources = fake.data_sources
    return NotionDecisionRepository(client=Client(), data_source_id="ds")


def run(c):
    return asyncio.run(c)


def test_add_twice_creates_one_page():
    fake = FakeNotion()
    repo = _repo(fake)
    url1 = run(repo.add(_record()))
    url2 = run(repo.add(_record()))  # 같은 작업을 다시 실행 (재시도)
    assert url1 == url2 and len(fake.pages) == 1 and fake.counts["create"] == 1


def test_create_succeeded_but_response_lost_does_not_duplicate():
    fake = FakeNotion(timeout_after_create=True)
    repo = _repo(fake)
    with pytest.raises(TimeoutError):
        run(repo.add(_record()))
    run(repo.add(_record()))  # 재시도: 기록ID로 찾아 '이미 있음'
    assert len(fake.pages) == 1 and fake.counts["create"] == 1


def test_update_already_written_does_nothing():
    fake = FakeNotion()
    repo = _repo(fake)
    run(repo.add(_record()))
    v2 = _record(version=2, decision="최대 10장", approved_at="2026-06-03T09:00:00+09:00")
    run(repo.update(v2, [_record()]))
    before = dict(fake.counts)
    run(repo.update(v2, [_record()]))  # 다시 실행
    assert fake.counts == before  # 쓰기 호출 없음


def test_update_interrupted_mid_delete_then_retry_ends_clean():
    fake = FakeNotion(fail_on={"delete": 2})
    repo = _repo(fake)
    run(repo.add(_record()))
    [page] = fake.pages.values()
    old_blocks = list(page["blocks"])
    v2 = _record(version=2, decision="최대 10장", approved_at="2026-06-03T09:00:00+09:00")
    expected = len(build_children(v2) + history_blocks([_record()]))
    with pytest.raises(RuntimeError):
        run(repo.update(v2, [_record()]))
    # 중간 상태: 새 블록이 이미 추가돼 본문이 비지 않았고(옛 블록 일부 + 새 블록), 완료 표시(정리일)는 아직 옛 값
    assert len(page["blocks"]) >= expected
    assert not NotionDecisionRepository.is_written({"properties": page["properties"]}, v2)
    half_done = set(page["blocks"])
    run(repo.update(v2, [_record()]))  # 재시도
    assert NotionDecisionRepository.is_written({"properties": page["properties"]}, v2)
    # 최종 본문 = 마지막 시도에서 추가한 블록만: 옛 본문도, 첫 시도에서 추가한 블록도 남지 않는다
    assert not half_done & set(page["blocks"]) and not set(old_blocks) & set(page["blocks"])
    assert len(page["blocks"]) == expected


def test_marker_compares_times_not_strings():
    page = {"properties": {APPROVED_AT: {"type": "date", "date": {"start": "2026-06-02T11:00:00.000+09:00"}}}}
    assert NotionDecisionRepository.is_written(page, _record())
    assert not NotionDecisionRepository.is_written(page, _record(approved_at="2026-06-02T11:00:01+09:00"))


def _api_error(status, code):
    resp = httpx.Response(status, request=httpx.Request("POST", "https://api.notion.com/v1/pages"))
    return APIResponseError(code=code, status=status, message=code, headers=resp.headers, raw_body_text="{}")


@pytest.mark.parametrize("status, code, retryable", [
    (429, "rate_limited", True), (503, "service_unavailable", True), (409, "conflict_error", True),
    (400, "validation_error", False), (401, "unauthorized", False), (404, "object_not_found", False),
])
def test_notion_errors_classified(status, code, retryable):
    msg, again = classify_failure(_api_error(status, code))
    assert again is retryable and str(status) in msg


def test_unknown_errors_are_retried():
    assert classify_failure(ConnectionError("reset"))[1] is True
