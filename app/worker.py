"""API 프로세스 안의 백그라운드 처리기 (A-5c).

몇 초마다(FOLLOWUP_INTERVAL_S) 한 번씩:
  1) 후속 작업 장부(outbox)에서 다시 할 때가 된 작업 실행 — 노션 저장 → 정책서 반영 제안
     노션 저장이 하나라도 끝나면 재인덱싱을 요청한다 (새 결정이 검색에 나오게)
  2) 주기 동기화: 마지막 동기화 이후 PERIODIC_SYNC_MINUTES가 지났으면 재인덱싱.
     승인 외의 변경(노션에서 사람이 고침, 정책서 수정, 놓친 재인덱싱)도 최대 그 시간 안에 반영된다.
     내용 해시 비교라 바뀐 게 없으면 임베딩 비용 0.

처리기는 1개여야 한다: uvicorn worker 1개 전제 (늘리면 같은 작업을 두 번 집을 수 있다 → 그때 파일 잠금이나 DB).
쓰기 작업이 다시 실행돼도 안전한 이유는 A-5b의 멱등 쓰기.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.config import Settings
from app.logging_setup import new_request_id
from app.notion.decisions import get_decision_repository
from app.outbox import Outbox
from app.services import followups

log = logging.getLogger("policy-worker")


class Worker:
    def __init__(self, settings: Settings, request_sync):
        """request_sync: (settings) -> None, 블로킹 재인덱싱 요청 (app/api/routers/sync.request_sync를 감싼 것)."""
        self.s = settings
        self.request_sync = request_sync
        self.last_sync = time.monotonic()  # 시작 직후엔 동기화하지 않는다 (재시작마다 전체 스캔 방지)
        self._sync_task: asyncio.Task | None = None

    def _sync_in_background(self, reason: str) -> None:
        if self._sync_task and not self._sync_task.done():
            return  # 이미 요청 중 (sync 쪽도 돌고 있으면 끝난 뒤 한 번 더 돌게 처리한다)
        log.info("재인덱싱 요청 (%s)", reason)
        self.last_sync = time.monotonic()
        self._sync_task = asyncio.create_task(asyncio.to_thread(self.request_sync, self.s))

    async def tick(self, now: float | None = None) -> dict:
        new_request_id("w-")  # 처리기 한 바퀴 = 요청 하나 (로그를 묶어 본다)
        now = time.monotonic() if now is None else now
        out = {"notion_done": 0, "notion_failed": 0, "synced": False}
        outbox = Outbox(self.s.data_dir)
        if outbox.pending():
            r = await followups.run_pending(outbox, get_decision_repository(self.s), only_due=True, user="worker")
            out |= {k: r[k] for k in ("notion_done", "notion_failed")}
            if r["notion_done"]:
                self._sync_in_background(f"후속 작업으로 노션 저장 {r['notion_done']}건")
                out["synced"] = True
        minutes = self.s.periodic_sync_minutes
        if not out["synced"] and minutes and now - self.last_sync >= minutes * 60:
            self._sync_in_background(f"주기 동기화 ({minutes}분)")
            out["synced"] = True
        return out

    async def run_forever(self) -> None:
        log.info("백그라운드 처리기 시작 (간격 %ss, 주기 동기화 %s분)", self.s.followup_interval_s,
                 self.s.periodic_sync_minutes or "끔")
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — 한 바퀴의 실패가 처리기를 멈추면 안 된다
                log.exception("백그라운드 처리기 한 바퀴 실패")
            await asyncio.sleep(self.s.followup_interval_s)
