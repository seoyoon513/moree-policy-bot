"""정리(요약) → 승인 대기 → 승인 저장 흐름. API 엔드포인트(app/api/routers/summaries.py)가 부른다.

왜 '승인 대기'를 API 쪽 파일로 두나
- 봇은 승인 화면(UI)만 들고, 저장에 필요한 원본(근거 메시지·이전 기록)은 API가 가진다.
  승인할 때 봇은 summary_id + 사람이 고친 결정만 보낸다 → 봇이 데이터를 다시 만들어 보낼 일이 없다.
- 파일(data/pending/<id>.json)이라 API를 재시작해도 승인 화면 유효시간(15분) 안이면 이어서 승인된다.

멱등성 (같은 승인이 두 번 와도 한 번만 저장)
- 봇의 네트워크 재시도·버튼 연타로 같은 승인이 두 번 올 수 있다. 승인 결과를 대기 파일에 남기고,
  다시 오면 저장하지 않고 그 결과를 돌려준다(replayed=True).
- 같은 프로세스 안에서 동시에 두 요청이 오면 summary_id별 asyncio.Lock으로 줄 세운다
  (uvicorn worker 1개 전제 — 늘리면 파일 잠금이나 DB가 필요하다).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shared.domain import ThreadMessage
from app.services import followups
from app.services.storage import resummary_context, save_decisions, thread_source
from app.services.summarizer import summarize_thread

log = logging.getLogger("policy-api")

PENDING_TTL = timedelta(days=1)  # 이보다 오래된 대기 파일은 정리 (승인 화면은 15분이면 닫힌다)


class SummaryNotFound(Exception):
    pass


# --- 승인 대기 보관 ---
class PendingStore:
    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir) / "pending"

    def _path(self, summary_id: str) -> Path:
        if not summary_id.isalnum():  # 경로 조작 방지 (외부 입력)
            raise SummaryNotFound(summary_id)
        return self.dir / f"{summary_id}.json"

    def save(self, item: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(item["id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)  # 반쯤 쓴 파일이 남지 않게

    def load(self, summary_id: str) -> dict:
        path = self._path(summary_id)
        if not path.exists():
            raise SummaryNotFound(summary_id)
        item = json.loads(path.read_text(encoding="utf-8"))
        # JSON은 dict 키를 문자열로 바꾼다 → 메시지 번호(int)로 되돌린다 (결정일·발췌 계산이 int 키로 찾는다)
        src = item.get("source") or {}
        src["messages_by_index"] = {int(k): v for k, v in (src.get("messages_by_index") or {}).items()}
        return item

    def cleanup(self, now: datetime | None = None) -> int:
        if not self.dir.exists():
            return 0
        cutoff = (now or datetime.now(timezone.utc)) - PENDING_TTL
        removed = 0
        for p in self.dir.glob("*.json"):
            if datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) < cutoff:
                p.unlink(missing_ok=True)
                removed += 1
        return removed


# --- 정리 ---
async def create_summary(store: PendingStore, *, thread_id: str, thread_name: str, thread_url: str,
                         messages: list[ThreadMessage], user: str | None = None) -> dict:
    """status: nothing_new(새 메시지 없음, LLM 안 부름) | no_decisions | unchanged(달라진 결정 없음) | ready(승인 대기).
    요약 실패는 SummaryError, 예산 초과는 LimitExceeded로 올라간다."""
    store.cleanup()
    ctx = resummary_context(thread_id, messages)
    if ctx.nothing_new:
        return {"status": "nothing_new", "previous_count": len(ctx.previous),
                "message": f"마지막 정리 이후 새 메시지가 없어요. 기존 기록 {len(ctx.previous)}건을 그대로 둘게요."}
    decisions = await summarize_thread(thread_name, messages, ctx.previous or None, ctx.since, user=user)
    changed = [d for d in decisions if d["relation"] != "unchanged"]
    if not changed:
        if decisions:
            return {"status": "unchanged", "unchanged": len(decisions),
                    "message": f"이전 정리와 달라진 결정이 없어요 (변동 없음 {len(decisions)}건). 기존 기록을 그대로 둘게요."}
        return {"status": "no_decisions", "message": "이 스레드에서 정책 결정이나 논의를 찾지 못했어요."}
    item = {
        "id": uuid.uuid4().hex[:16], "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "state": "pending", "thread_id": thread_id,
        "source": thread_source(thread_id, thread_name, thread_url, messages),
        "decisions": changed, "previous": ctx.previous, "unchanged": len(decisions) - len(changed),
    }
    store.save(item)
    log.info("정리 결과 승인 대기 (summary=%s, thread=%s, 결정 %d건, 변동 없음 %d건)", item["id"], thread_id,
             len(changed), item["unchanged"], extra={"summary_id": item["id"], "thread_id": thread_id})
    return {"status": "ready", "summary_id": item["id"], "decisions": changed, "previous": ctx.previous,
            "unchanged": item["unchanged"]}


# --- 승인 ---
_locks: dict[str, asyncio.Lock] = {}


def _saved_view(r) -> dict:
    return {"id": r.record["id"], "topic": r.record["topic"], "version": r.record.get("version", 1),
            "updated": r.updated, "reopens": r.record.get("reopens"),
            "notion_url": r.notion_url, "notion_error": r.notion_error, "notion_pending": r.notion_pending}


async def approve(store: PendingStore, summary_id: str, *, decisions: list[dict], approved_by: str,
                  user: str | None = None) -> dict:
    """사람이 고친 결정(decisions)을 저장하고 정책서 반영 제안까지 한다. 이미 승인된 건이면 그 결과를 그대로."""
    lock = _locks.setdefault(summary_id, asyncio.Lock())
    async with lock:
        item = store.load(summary_id)
        if item.get("state") == "approved":
            log.info("이미 승인된 정리 — 저장하지 않고 이전 결과 반환 (summary=%s)", summary_id)
            return item["result"] | {"replayed": True}
        # 원장 줄(+후속 작업) → 노션 저장을 바로 한 번. 실패는 장부(outbox)에 남아 다시 시도된다 (A-5)
        results = await save_decisions(decisions, source=item["source"], approved_by=approved_by, plan_reflect=True)
        # 정책서 반영 제안도 후속 작업. 원문은 고치지 않고 댓글·콜아웃만
        reflect_lines = await followups.reflect_after_save(results, user=user)
        result = {"saved": [_saved_view(r) for r in results], "reflect_lines": reflect_lines, "replayed": False}
        item |= {"state": "approved", "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "approved_by": approved_by, "result": result}
        store.save(item)
    _locks.pop(summary_id, None)
    log.info("승인 저장 완료 (summary=%s, 저장 %d건, 노션 실패 %d건)", summary_id, len(results),
             sum(1 for r in results if r.notion_error),
             extra={"summary_id": summary_id, "record_ids": [r.record["id"] for r in results]})
    return result
