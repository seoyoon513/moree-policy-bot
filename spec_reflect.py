"""승인된 결정 → 원천 정책서(기능명세서) 반영 제안. (EXPERIMENTS 3-3 매칭 · 3-4 반영)

원칙: 정책서 원문은 고치지 않는다. 제안만 넣고 반영은 PM이 한다.
- modify  (기존 정책 ID와 다름)  → 그 표 행에 댓글 (+ PM 멘션)
- new     (화면은 있는데 ID 없음) → 그 화면의 기능 표 바로 뒤에 노란 콜아웃 (+ PM 멘션 댓글)
- unknown (위치 모름)            → 페이지 댓글
- same    (이미 반영됨)          → 아무것도 하지 않음
- skip    (정책서 대상 아님: 디자인 리소스·시안 오류·구현 방식) → 아무것도 하지 않음
- inbox   (맞는 정책서가 없음)   → 미분류 제안 페이지에 체크박스 (+ PM 멘션 댓글)
          기능영역에 정책서가 없는 경우, 기본 정책서("*")로 넘어갔는데 위치를 못 찾은 경우.
          영역의 정책서 안에서 위치만 못 찾은 unknown은 그 정책서의 페이지 댓글로 둔다 (문서는 맞고 자리만 모름).

매칭은 정책서별로 LLM 1회 (정책서 목록 + 결정 목록). 비용은 app.usage에 "spec_match"로 기록하고 월 예산을 확인한다.
반영 결과는 data/spec/reflections.jsonl에 남겨 3-3 정확도·3-5 비용 측정에 쓴다.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from anthropic import AsyncAnthropic

import notion_store
from app import usage as usage_log
from app.rag.spec_loader import SpecDocument, SpecEntry, load_spec
from config import (ANTHROPIC_API_KEY, DATA_DIR, KST, NOTION_PM_USER_IDS, NOTION_SPEC_INBOX_PAGE, NOTION_SPEC_PAGES,
                    SPEC_MATCH_MAX_TOKENS, SPEC_MATCH_MODEL, SPEC_REFLECT_STATUSES)
from prompts import SPEC_MATCH_SYSTEM, build_spec_match_prompt
from summarizer import SummaryError, parse_json

log = logging.getLogger("policy-bot")

ACTIONS = ("skip", "same", "modify", "new", "unknown")
ENTRY_BODY_CHARS = 600   # 정책서 항목 하나를 프롬프트에 넣을 때 최대 글자
CALLOUT_COLOR = "yellow_background"


@dataclass
class Match:
    decision: int            # 1부터
    action: str
    policy_id: str | None = None
    screen_id: str | None = None
    current: str | None = None
    proposal: str | None = None
    reason: str | None = None


@dataclass
class Outcome:
    record_id: str
    topic: str
    action: str              # skip | same | modify | new | unknown | inbox | skipped(상태·정책서 미설정) | error
    policy_id: str | None = None
    screen_id: str | None = None
    url: str | None = None   # 제안이 들어간 곳 (블록 링크)
    note: str | None = None


# --- 매칭 (3-3) ---
def spec_listing(doc: SpecDocument) -> str:
    lines = [f"정책서: {doc.title}", "", "### 화면"]
    lines += [f"- {sid}: {s['title']}" + (f" — {s['purpose']}" if s["purpose"] else "") for sid, s in doc.screens.items()]
    lines += ["", "### 정책 항목"]
    for e in doc.entries:
        if not e.policy_id:
            continue
        body = e.body if len(e.body) <= ENTRY_BODY_CHARS else e.body[:ENTRY_BODY_CHARS] + "…"
        lines.append(f"[{e.policy_id}] {e.name} (화면 {e.screen_id or '-'})\n{body}")
    return "\n".join(lines)


def decisions_text(records: list[dict]) -> str:
    out = []
    for i, r in enumerate(records, start=1):
        out.append(f"[{i}] 주제: {r['topic']}\n결정: {r['decision']}"
                   + (f"\n적용범위: {r['scope']}" if r.get("scope") else ""))
    return "\n\n".join(out)


def _s(v) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def normalize_matches(data: dict, doc: SpecDocument, n: int) -> list[Match]:
    """모델이 목록에 없는 ID를 쓰거나 필수값을 빼먹으면 한 단계 보수적인 판정으로 내린다.
    결정마다 정확히 하나: 빠진 결정은 unknown."""
    ids = doc.by_id()
    got: dict[int, Match] = {}
    for m in data.get("matches") or []:
        if not isinstance(m, dict):
            continue
        try:
            k = int(m.get("decision"))
        except (TypeError, ValueError):
            continue
        if not 1 <= k <= n or k in got:
            continue
        action = m.get("action") if m.get("action") in ACTIONS else "unknown"
        pid, sid = _s(m.get("policy_id")), _s(m.get("screen_id"))
        if pid not in ids:
            pid = None
        if pid and not sid:
            sid = ids[pid].screen_id
        if sid not in doc.screens:
            sid = None
        # same은 쓸 게 없다는 판정이라 ID가 틀려도 그대로 둔다 (new로 내리면 없던 제안이 생긴다)
        if action == "modify" and not pid:
            action = "new" if sid else "unknown"
        if action == "new" and not (sid and doc.last_table_of(sid)):
            action = "unknown"
        got[k] = Match(k, action, pid, sid, _s(m.get("current")), _s(m.get("proposal")), _s(m.get("reason")))
    return [got.get(k) or Match(k, "unknown", reason="모델 응답에 없음") for k in range(1, n + 1)]


_client: AsyncAnthropic | None = None


async def match_records(doc: SpecDocument, records: list[dict], user: str | None = None) -> list[Match]:
    from app import limits
    from app.config import get_settings
    limits.check_budget(get_settings())

    global _client
    _client = _client or AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    with usage_log.timer() as elapsed:
        resp = await _client.messages.create(
            model=SPEC_MATCH_MODEL, max_tokens=SPEC_MATCH_MAX_TOKENS, system=SPEC_MATCH_SYSTEM,
            messages=[{"role": "user", "content": build_spec_match_prompt(spec_listing(doc), decisions_text(records))}],
        )
    usage_log.from_anthropic("spec_match", SPEC_MATCH_MODEL, resp, elapsed(), user=user)
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    try:
        return normalize_matches(parse_json(text), doc, len(records))
    except SummaryError:
        log.warning("정책서 매칭 JSON 파싱 실패: %s", text[:500])
        return [Match(k, "unknown", reason="매칭 응답 파싱 실패") for k in range(1, len(records) + 1)]


# --- 반영 (3-4) ---
def block_url(doc: SpecDocument, block_id: str) -> str:
    return f"{doc.url}#{block_id.replace('-', '')}"


def _t(text: str, url: str | None = None, bold: bool = False) -> dict:
    d = {"type": "text", "text": {"content": text[:2000], "link": {"url": url} if url else None}}
    if bold:
        d["annotations"] = {"bold": True}
    return d


def _mentions() -> list[dict]:
    out = []
    for uid in NOTION_PM_USER_IDS:
        out += [{"type": "mention", "mention": {"type": "user", "user": {"id": uid}}}, _t(" ")]
    return out


def marker(record: dict) -> str:
    return f"기록 {record['id']}"


def proposal_rich_text(record: dict, match: Match, notion_url: str | None, *, title: str,
                       with_mentions: bool = True) -> list[dict]:
    src = record.get("source") or {}
    parts = (_mentions() if with_mentions else []) + [
        _t(f"💡 {title}", bold=True),
        _t(f" · {record['status']} · 결정일 {(record.get('decided_at') or '')[:10]}\n"),
    ]
    if match.current:
        parts.append(_t(f"현재: {match.current}\n"))
    parts.append(_t(f"제안: {match.proposal or record['decision']}\n"))
    parts.append(_t("근거: "))
    if src.get("thread_url"):
        parts.append(_t(src.get("thread_name") or "디스코드 스레드", src["thread_url"]))
    if notion_url:
        parts += [_t(" · "), _t("결정 기록", notion_url)]
    parts.append(_t(f"\n{marker(record)}"))
    return parts


async def _already_proposed(client, block_id: str, record: dict) -> bool:
    """같은 기록으로 같은 블록에 이미 댓글을 달았으면 다시 달지 않는다 (재시도·중복 클릭 대비)."""
    try:
        res = await client.comments.list(block_id=block_id)
    except Exception:
        return False
    return any(marker(record) in "".join(t.get("plain_text", "") for t in c.get("rich_text", []))
               for c in res.get("results", []))


async def apply_match(client, doc: SpecDocument, record: dict, match: Match, notion_url: str | None) -> Outcome:
    out = Outcome(record["id"], record["topic"], match.action, match.policy_id, match.screen_id, note=match.reason)
    if match.action == "skip":
        return out
    if match.action == "same":
        if match.policy_id:
            out.url = block_url(doc, doc.by_id()[match.policy_id].block_id)
        return out

    if match.action == "modify":
        entry: SpecEntry = doc.by_id()[match.policy_id]
        out.url = block_url(doc, entry.block_id)
        if await _already_proposed(client, entry.block_id, record):
            out.note = "이미 같은 기록으로 제안함"
            return out
        await client.comments.create(parent={"block_id": entry.block_id},
                                     rich_text=proposal_rich_text(record, match, notion_url, title="정책봇 반영 제안"))
        return out

    if match.action == "new":
        anchor = doc.last_table_of(match.screen_id)
        res = await client.blocks.children.append(
            block_id=anchor.parent_id,
            position={"type": "after_block", "after_block": {"id": anchor.table_id}},
            children=[{"type": "callout", "callout": {
                "rich_text": proposal_rich_text(record, match, notion_url, title=f"정책봇 제안 · 신규 ({match.screen_id})",
                                                with_mentions=False),
                "icon": {"type": "emoji", "emoji": "💡"}, "color": CALLOUT_COLOR}}],
        )
        callout_id = res["results"][0]["id"]
        out.url = block_url(doc, callout_id)
        if NOTION_PM_USER_IDS:  # 콜아웃만으로는 알림이 안 가서 멘션 댓글을 붙인다
            await client.comments.create(parent={"block_id": callout_id},
                                         rich_text=_mentions() + [_t("새 정책 항목 제안이에요. 확인 부탁드려요.")])
        return out

    # unknown → 페이지 댓글
    await client.comments.create(parent={"page_id": doc.page_id},
                                 rich_text=proposal_rich_text(record, match, notion_url, title="정책봇 반영 제안 (위치 확인 필요)"))
    out.url = doc.url
    return out


def inbox_rich_text(record: dict, proposal: str | None, notion_url: str | None) -> list[dict]:
    src = record.get("source") or {}
    parts = [
        _t(f"[{record['area']}] {record['topic']}", bold=True),
        _t(f" · {record['status']} · 결정일 {(record.get('decided_at') or '')[:10]}\n"),
        _t(f"제안: {proposal or record['decision']}\n"),
        _t("근거: "),
    ]
    if src.get("thread_url"):
        parts.append(_t(src.get("thread_name") or "디스코드 스레드", src["thread_url"]))
    if notion_url:
        parts += [_t(" · "), _t("결정 기록", notion_url)]
    parts.append(_t(f" · {marker(record)}"))
    return parts


async def _inbox_has(client, page_id: str, record: dict) -> bool:
    """같은 기록이 이미 미분류 페이지에 있으면 다시 넣지 않는다 (재실행 대비)."""
    cursor = None
    while True:
        kw = {"block_id": page_id, "page_size": 100}
        if cursor:
            kw["start_cursor"] = cursor
        res = await client.blocks.children.list(**kw)
        for b in res.get("results", []):
            text = "".join(t.get("plain_text", "") for t in b.get(b["type"], {}).get("rich_text", []))
            if marker(record) in text:
                return True
        if not res.get("has_more"):
            return False
        cursor = res["next_cursor"]


async def add_to_inbox(client, record: dict, proposal: str | None, notion_url: str | None, note: str | None) -> Outcome:
    page_id = notion_store.extract_id(NOTION_SPEC_INBOX_PAGE)
    out = Outcome(record["id"], record["topic"], "inbox", note=note)
    base = f"https://www.notion.so/{page_id}"
    if await _inbox_has(client, page_id, record):
        out.url, out.note = base, "이미 미분류 페이지에 있음"
        return out
    res = await client.blocks.children.append(block_id=page_id, children=[{"type": "to_do", "to_do": {
        "rich_text": inbox_rich_text(record, proposal, notion_url), "checked": False}}])
    block_id = res["results"][0]["id"]
    out.url = f"{base}#{block_id.replace('-', '')}"
    if NOTION_PM_USER_IDS:
        await client.comments.create(parent={"block_id": block_id},
                                     rich_text=_mentions() + [_t("맞는 정책서를 못 찾은 제안이에요. 반영할 곳을 정해 주세요.")])
    return out


def _log_outcomes(outcomes: list[Outcome], records: dict[str, dict], doc: SpecDocument | None) -> None:
    path = Path(DATA_DIR) / "spec" / "reflections.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(KST).isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as f:
            for o in outcomes:
                r = records.get(o.record_id, {})
                f.write(json.dumps({"ts": now, "thread_id": (r.get("source") or {}).get("thread_id"),
                                    "spec_page": doc.page_id if doc else None, **asdict(o)}, ensure_ascii=False) + "\n")
    except OSError:
        pass


async def reflect_records(saved: list[tuple[dict, str | None]], user: str | None = None) -> list[Outcome]:
    """saved: [(record, 노션 결정 기록 URL)]. 대상 상태의 결정을 정책서에 반영 제안하고, 맞는 정책서가 없으면 미분류 페이지로."""
    if not (notion_store.enabled() and (NOTION_SPEC_PAGES or NOTION_SPEC_INBOX_PAGE)):
        return []
    client = notion_store.get_client()
    outcomes: list[Outcome] = []
    direct: list[Outcome] = []  # 정책서 없이 바로 미분류로 간 것 (정책서별 로그와 따로 기록)
    by_page: dict[str, list[tuple[dict, str | None]]] = {}
    for record, url in saved:
        # 영역별 정책서가 없으면 기본 정책서("*"). 영역 분류가 정책서 구분과 어긋날 때(탐험 화면의 북마크 기능 등) 대비
        page = NOTION_SPEC_PAGES.get(record["area"]) or NOTION_SPEC_PAGES.get("*")
        if record["status"] not in SPEC_REFLECT_STATUSES:
            outcomes.append(Outcome(record["id"], record["topic"], "skipped", note=f"{record['status']}은 반영 제안 안 함"))
        elif page:
            by_page.setdefault(notion_store.extract_id(page), []).append((record, url))
        elif NOTION_SPEC_INBOX_PAGE:  # 정책서 자체가 없음 → 매칭 없이 미분류로
            direct.append(await _safe_inbox(client, record, None, url, f"'{record['area']}' 정책서 없음"))
            outcomes.append(direct[-1])
        else:
            outcomes.append(Outcome(record["id"], record["topic"], "skipped", note=f"'{record['area']}' 정책서 미설정"))

    for page_id, items in by_page.items():
        records = [r for r, _ in items]
        doc = None
        try:
            doc = await load_spec(client, page_id, Path(DATA_DIR) / "spec")
            matches = await match_records(doc, records, user=user)
            area_out = []
            for (record, url), m in zip(items, matches):
                # 기본 정책서로 넘어온 결정인데 위치를 못 찾음 = 이 문서가 아님 → 미분류 페이지로
                if m.action == "unknown" and record["area"] not in NOTION_SPEC_PAGES and NOTION_SPEC_INBOX_PAGE:
                    area_out.append(await _safe_inbox(client, record, m.proposal, url, m.reason))
                    continue
                try:
                    area_out.append(await apply_match(client, doc, record, m, url))
                except Exception as e:  # 한 건 실패가 나머지를 막지 않게
                    log.exception("정책서 반영 실패 (id=%s)", record["id"])
                    area_out.append(Outcome(record["id"], record["topic"], "error", m.policy_id, m.screen_id,
                                            note=str(e)[:200]))
        except Exception as e:
            log.exception("정책서 반영 단계 실패 (정책서=%s)", page_id)
            area_out = [Outcome(r["id"], r["topic"], "error", note=str(e)[:200]) for r in records]
        _log_outcomes(area_out, {r["id"]: r for r in records}, doc)
        outcomes += area_out
    _log_outcomes(direct, {r["id"]: r for r, _ in saved}, None)
    return outcomes


async def _safe_inbox(client, record: dict, proposal: str | None, url: str | None, note: str | None) -> Outcome:
    try:
        return await add_to_inbox(client, record, proposal, url, note)
    except Exception as e:
        log.exception("미분류 페이지 추가 실패 (id=%s)", record["id"])
        return Outcome(record["id"], record["topic"], "error", note=str(e)[:200])


LABELS = {"modify": "📝 행 댓글", "new": "➕ 새 항목 제안", "unknown": "❔ 페이지 댓글", "same": "✔️ 이미 반영됨",
          "skip": "➖ 정책서 대상 아님", "inbox": "📥 미분류 제안", "skipped": "⏭️ 건너뜀", "error": "⚠️ 실패"}


def format_outcomes(outcomes: list[Outcome]) -> list[str]:
    """디스코드 승인 메시지에 붙일 줄."""
    if not outcomes:
        return []
    lines = ["📚 기준 정책서 반영 제안"]
    for o in outcomes:
        where = o.policy_id or o.screen_id or ""
        label = f"{LABELS[o.action]} {where}".strip()
        text = f"[{label}]({o.url})" if o.url else label
        tail = f" — {o.note}" if o.action in ("skipped", "error") and o.note else ""
        lines.append(f"• {text} · {o.topic}{tail}")
    return lines
