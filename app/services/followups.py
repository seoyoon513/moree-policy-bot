"""후속 작업 실행 — 승인 직후 한 번, 실패한 것은 나중에 다시 (A-5).

원장에 버전 줄이 써지면 승인은 끝난 것이다(커밋 지점). 노션 저장·정책서 반영 제안은 그 줄의 `ops`에 적힌
후속 작업이고, 여기서 실행해 결과를 app/outbox.py 장부에 남긴다.
- 승인 직후: 방금 저장한 기록의 작업만 바로 실행 (사용자가 보는 결과는 예전과 같다)
- 그 뒤: 장부에 done이 없는 작업을 다시 실행 (scripts/outbox.py, A-5c부터는 API의 백그라운드 처리기)
작업은 다시 실행해도 결과가 같아야 한다(멱등) — 노션 저장은 기록ID로 페이지를 찾아 갱신, 정책서 반영은 마커로 중복 방지.
"""
from __future__ import annotations

import asyncio
import logging

from app.notion.decisions import DecisionRepository
from app.outbox import NOTION_UPSERT, SPEC_REFLECT, Outbox, PendingOp, op_key
from app.services import spec_reflect

log = logging.getLogger("policy-followups")


# 다시 보내도 같은 결과인 HTTP 상태: 요청 형식(400)·토큰(401)·권한(403)·대상 없음/공유 안 됨(404)
_NO_RETRY_STATUS = {400, 401, 403, 404}


def classify_failure(e: Exception) -> tuple[str, bool]:
    """(오류 문구, 다시 시도하면 나을 수 있나).
    - 다시 해 볼 것: 타임아웃·연결 오류·429·409(충돌)·5xx (notion-client가 이미 몇 번 재시도한 뒤라도 시간이 지나면 나을 수 있다)
    - 바로 포기(dead): 400·401·403·404 — 설정·데이터 문제라 사람이 고쳐야 한다"""
    from notion_client import APIResponseError
    from notion_client.errors import HTTPResponseError, RequestTimeoutError
    status = getattr(e, "status", None)
    if isinstance(e, (APIResponseError, HTTPResponseError)) and status is not None:
        return f"{status} {getattr(e, 'code', '')}: {e}"[:500], status not in _NO_RETRY_STATUS
    if isinstance(e, RequestTimeoutError):
        return f"timeout: {e}"[:500], True
    return f"{type(e).__name__}: {e}"[:500], True


async def run_notion(op: PendingOp, outbox: Outbox, repo: DecisionRepository) -> str | None:
    """노션 결정 기록 저장. 성공하면 URL, 실패하면 None (장부에 failed/dead)."""
    try:
        url = await (repo.update(op.record, op.history) if op.history else repo.add(op.record))
    except Exception as e:  # noqa: BLE001 — 원장은 이미 저장됐다. 실패를 장부에 남기고 다음에 다시
        error, retryable = classify_failure(e)
        log.exception("노션 저장 실패 (%s)", op.key, extra={"op": op.key, "record_id": op.record["id"]})
        if outbox.fail(op.key, error, retryable=retryable)["result"] == "dead":
            await asyncio.to_thread(_alert_dead, op, error)  # 웹훅은 블로킹 HTTP라 이벤트 루프 밖에서
        return None
    outbox.done(op.key, notion_url=url)
    return url


def _alert_dead(op: PendingOp, error: str) -> None:
    """포기한 작업은 사람이 봐야 한다 → 운영 알림 (웹훅이 없으면 로그만)."""
    from app import ops
    from app.config import get_settings
    ops.notify(get_settings(), f"⚠️ 후속 작업을 포기했어요 — {op.op} · {op.record.get('topic', '')[:60]} "
                               f"(기록 {op.record['id']} v{op.record.get('version', 1)}): {error[:200]}\n"
                               f"확인: python -m scripts.outbox list")


async def run_reflect(ops: list[PendingOp], outbox: Outbox, user: str | None = None) -> list:
    """정책서 반영 제안. 노션 저장이 아직 안 끝난 기록은 건너뛴다(기록 링크가 필요해서) → 다음 차례에."""
    ready = [(op, url) for op in ops if (url := outbox.notion_url(op.record))]
    if not ready:
        return []
    outcomes = await spec_reflect.reflect_records([(op.record, url) for op, url in ready], user=user)
    by_id = {o.record_id: o for o in outcomes}
    for op, _ in ready:
        o = by_id.get(op.record["id"])
        if o is None or o.action == "error":
            error = (o.note if o else None) or "정책서 반영 결과 없음"
            if outbox.fail(op.key, error)["result"] == "dead":
                await asyncio.to_thread(_alert_dead, op, error)
        else:
            outbox.done(op.key, action=o.action)
    return outcomes


async def run_pending(outbox: Outbox, repo: DecisionRepository | None, *, keys: set[str] | None = None,
                      only_due: bool = True, user: str | None = None) -> dict:
    """장부에서 done이 아닌 작업을 실행. keys를 주면 그 작업만, only_due=False면 next_at을 무시(수동 재시도)."""
    ops = [p for p in outbox.pending() if (keys is None or p.key in keys) and (not only_due or p.due())]
    notion_ops = [p for p in ops if p.op == NOTION_UPSERT]
    done = failed = 0
    if notion_ops and repo is None:
        log.warning("노션이 설정되지 않아 노션 작업 %d건을 실행하지 않음", len(notion_ops))
        notion_ops = []
    for op in notion_ops:
        if await run_notion(op, outbox, repo):
            done += 1
        else:
            failed += 1
    reflect_ops = [p for p in ops if p.op == SPEC_REFLECT]
    outcomes = await run_reflect(reflect_ops, outbox, user=user) if reflect_ops else []
    return {"notion_done": done, "notion_failed": failed, "reflect_outcomes": outcomes,
            "remaining": len(outbox.pending())}


def keys_for(records: list[dict], op: str) -> set[str]:
    return {op_key(r, op) for r in records if op in (r.get("ops") or [])}


async def reflect_after_save(results: list, *, user: str | None = None) -> list[str]:
    """승인 직후 정책서 반영 제안을 한 번 실행하고 디스코드에 보여 줄 줄을 만든다 (승인 API·일괄 정리 공용).
    반영 대상(ops에 spec_reflect)만 실행해 장부에 남기고, 대상이 아닌 기록은 이유를 '건너뜀'으로 보여 준다."""
    if not (spec_reflect.NOTION_SPEC_PAGES or spec_reflect.NOTION_SPEC_INBOX_PAGE):
        return []
    from app.services import storage  # 순환 import 방지 (storage가 이 모듈을 쓴다)
    outbox = Outbox(storage.DATA_DIR)
    records = [r.record for r in results]
    keys = keys_for(records, SPEC_REFLECT)
    ops = [p for p in outbox.pending() if p.key in keys]
    outcomes = await run_reflect(ops, outbox, user=user)
    seen = {o.record_id for o in outcomes}
    for r in records:
        if r["id"] in seen:
            continue
        if SPEC_REFLECT in (r.get("ops") or []):
            note = "노션 저장이 끝나면 다시 시도해요"
        elif r.get("status") not in spec_reflect.SPEC_REFLECT_STATUSES:
            note = f"{r.get('status')}은 반영 제안 안 함"
        else:
            note = "노션 미설정"
        outcomes.append(spec_reflect.Outcome(r["id"], r["topic"], "skipped", note=note))
    return spec_reflect.format_outcomes(outcomes)

