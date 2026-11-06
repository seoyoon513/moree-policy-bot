"""원장 ↔ 노션 대조 (A-5d, ARCHITECTURE_REVIEW 3-4 ③) — 앞 단계가 놓친 어긋남을 결국 찾아내는 마지막 방어선.

기본은 **보고만** 한다. 자동으로 지우거나 되살리지 않는다:
- 사람이 노션에서 지운 페이지를 "없으니 다시 만들기"로 되살리면 PM의 의도를 거스른다.
- 사람이 고친 페이지는 노션이 원본이다 (DIRECTION 2-1 "노션 직접 수정 반영"). 언제 원장으로 가져올지는 열린 질문(3-6).
고치는 건 명시적인 명령으로만: scripts/reconcile.py --fix-missing (아직 노션에 없는 기록만 만든다, 멱등).

분류
- pending        : 후속 작업이 장부에 남아 있음 → 처리기가 하는 중 (문제 아님, 개수만)
- missing        : 원장의 현재 기록인데 노션에 없고, 처리할 작업도 없음 (A-5 이전에 노션 저장이 실패한 기록 등)
- deleted        : 노션 저장이 끝났던 기록인데 지금 노션에 없음 → 사람이 지웠거나 공유가 끊김. 되살리지 않는다
- stale          : 노션 페이지가 원장 현재 버전보다 옛 버전 (처리할 작업 없음)
- edited         : 버전은 같은데 주제·영역·상태·결정이 다름 → 노션에서 사람이 고침
- unknown_pages  : 기록ID가 없거나 원장에 없는 페이지 → 손으로 만든 페이지
"""
from __future__ import annotations

from app.ledger import Ledger
from app.notion import schema as ns
from app.notion.decisions import NotionDecisionRepository, same_minute
from app.outbox import NOTION_UPSERT, Outbox, op_key

_COMPARED = [(ns.TITLE, "topic"), (ns.AREA, "area"), (ns.STATUS, "status"), (ns.DECISION, "decision")]


async def fetch_pages(client, data_source_id: str) -> list[dict]:
    """결정 기록 DB의 모든 페이지 (휴지통 제외)."""
    pages, cursor = [], None
    while True:
        kw = {"data_source_id": data_source_id, "page_size": 100}
        if cursor:
            kw["start_cursor"] = cursor
        res = await client.data_sources.query(**kw)
        pages += [p for p in res["results"]
                  if p.get("object", "page") == "page" and not p.get("in_trash") and not p.get("archived")]
        if not res.get("has_more"):
            break
        cursor = res["next_cursor"]
    return pages


def _p(page: dict, name: str) -> str:
    return ns.prop_to_str((page.get("properties") or {}).get(name)).strip()


def compare(ledger: Ledger, outbox: Outbox, pages: list[dict]) -> dict:
    """순수 함수 — 노션 API 없이 테스트한다."""
    by_id: dict[str, dict] = {}
    unknown = []
    versions = ledger.versions()
    for page in pages:
        rid = _p(page, ns.RECORD_ID)
        if rid in versions:
            by_id[rid] = page
        else:
            unknown.append(page)
    pending_keys = {p.key for p in outbox.pending(include_dead=True)}
    ever_done = {line["op"].split(":")[0] for line in outbox.lines()
                 if line.get("result") == "done" and line["op"].endswith(":" + NOTION_UPSERT)}
    out = {k: [] for k in ("pending", "missing", "deleted", "stale", "edited")}
    for rid, vs in versions.items():
        record = vs[-1]
        item = {"id": rid, "version": record.get("version", 1), "topic": record.get("topic", "")}
        if op_key(record, NOTION_UPSERT) in pending_keys:
            out["pending"].append(item)
            continue
        page = by_id.get(rid)
        if page is None:
            out["deleted" if rid in ever_done else "missing"].append(item)
            continue
        item["url"] = page.get("url")
        if not same_minute(_p(page, ns.APPROVED_AT), record.get("approved_at", "")):  # 노션 날짜는 분 단위
            out["stale"].append(item | {"notion_approved_at": _p(page, ns.APPROVED_AT)})
            continue
        diff = [field for prop, field in _COMPARED if _p(page, prop) != (record.get(field) or "").strip()]
        if diff:
            out["edited"].append(item | {"fields": diff})
    out["unknown_pages"] = [{"url": p.get("url"), "title": _p(p, ns.TITLE), "record_id": _p(p, ns.RECORD_ID)}
                            for p in unknown]
    out["counts"] = {k: len(v) for k, v in out.items()}
    out["ok"] = not any(out["counts"][k] for k in ("missing", "deleted", "stale", "unknown_pages"))
    return out


async def reconcile(data_dir: str, repo: NotionDecisionRepository) -> dict:
    pages = await fetch_pages(repo.client, repo.data_source_id)
    return compare(Ledger(data_dir), Outbox(data_dir), pages)


async def fix_missing(data_dir: str, repo: NotionDecisionRepository, report: dict) -> list[str]:
    """보고서의 missing만 노션에 만든다 (명시적 명령으로만). add는 기록ID로 먼저 찾으므로 여러 번 해도 1개."""
    versions = Ledger(data_dir).versions()
    urls = []
    for item in report["missing"]:
        vs = versions[item["id"]]
        url = await (repo.update(vs[-1], vs[:-1]) if len(vs) > 1 else repo.add(vs[-1]))
        urls.append(url)
    return urls
