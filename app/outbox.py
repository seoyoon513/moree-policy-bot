"""후속 작업 장부 — 아웃박스 패턴 (ARCHITECTURE_REVIEW 3-1, 3-4, A-5).

- 해야 할 일: 원장(history.jsonl)의 버전 줄에 `ops`로 같이 쓴다. 결정과 할 일이 한 줄 → 둘이 어긋날 수 없다.
- 한 결과:   여기(outbox.jsonl)에 append. 작업이 멱등하니 이 파일을 잃어도 "아직 안 함"으로 보고 한 번 더 할 뿐이다.
  그래서 원장과 원자적일 필요가 없다. 원장에 섞지 않는 이유: 원장을 읽는 코드는 "모든 줄 = 결정 기록"을 가정한다.

작업 키: "<기록ID>:<버전>:<작업>". 대기 목록은 각 기록의 **현재 버전**만 본다 —
v2가 생기면 v1의 노션 저장은 의미가 없다 (v2 저장이 그 내용을 덮는다).

결과(result): done | failed(다시 시도 대상, next_at 이후) | dead(다시 해도 같음 → 사람이 봐야 함)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.ledger import Ledger

log = logging.getLogger("policy-outbox")

FILENAME = "outbox.jsonl"
NOTION_UPSERT = "notion_upsert"
SPEC_REFLECT = "spec_reflect"
OPS = (NOTION_UPSERT, SPEC_REFLECT)   # 실행 순서: 정책서 반영은 노션 기록 링크가 필요하다

MAX_ATTEMPTS = 6                       # 이만큼 실패하면 dead
BACKOFF_BASE_S = 60                    # 1·2·4·8·16분 …
BACKOFF_MAX_S = 30 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def op_key(record: dict, op: str) -> str:
    return f"{record['id']}:{record.get('version', 1)}:{op}"


def backoff(attempt: int) -> timedelta:
    return timedelta(seconds=min(BACKOFF_BASE_S * 2 ** max(attempt - 1, 0), BACKOFF_MAX_S))


@dataclass
class PendingOp:
    key: str
    op: str
    record: dict            # 현재 버전
    history: list[dict]     # 이전 버전들 (오래된 순) — 노션 본문 '변경 이력'에 쓴다
    attempt: int = 0        # 지금까지 실패 횟수
    next_at: str | None = None
    last_error: str | None = None

    def due(self, now: datetime | None = None) -> bool:
        return not self.next_at or datetime.fromisoformat(self.next_at) <= (now or _now())


class Outbox:
    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / FILENAME
        self.ledger = Ledger(data_dir)

    # --- 쓰기 ---
    def record(self, key: str, result: str, **fields) -> dict:
        line = {"op": key, "result": result, "at": _now().isoformat(timespec="seconds")} | fields
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return line

    def done(self, key: str, **fields) -> dict:
        return self.record(key, "done", **fields)

    def fail(self, key: str, error: str, *, retryable: bool = True) -> dict:
        """실패 기록. 재시도 가능하고 횟수가 남았으면 failed(+next_at), 아니면 dead."""
        attempt = (self.latest().get(key) or {}).get("attempt", 0) + 1
        if not retryable or attempt >= MAX_ATTEMPTS:
            log.error("후속 작업 포기 (%s, %d회): %s", key, attempt, error[:300], extra={"op": key})
            return self.record(key, "dead", attempt=attempt, error=error[:500])
        next_at = (_now() + backoff(attempt)).isoformat(timespec="seconds")
        log.warning("후속 작업 실패 (%s, %d회, 다음 %s): %s", key, attempt, next_at, error[:300], extra={"op": key})
        return self.record(key, "failed", attempt=attempt, error=error[:500], next_at=next_at)

    # --- 읽기 ---
    def lines(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue  # 반쯤 쓴 줄: 그 작업은 "아직 안 함"으로 보인다 → 멱등이라 한 번 더 할 뿐
            if isinstance(r, dict) and r.get("op"):
                out.append(r)
        return out

    def latest(self) -> dict[str, dict]:
        """작업 키 → 마지막 결과 줄."""
        out: dict[str, dict] = {}
        for r in self.lines():
            out[r["op"]] = r
        return out

    def pending(self, *, include_dead: bool = False) -> list[PendingOp]:
        """현재 버전들의 ops 중 done이 아닌 것 (dead는 기본 제외). 실행 순서대로 (노션 저장 → 정책서 반영)."""
        state = self.latest()
        out: list[PendingOp] = []
        for vs in self.ledger.versions().values():
            record = vs[-1]
            for op in OPS:
                if op not in (record.get("ops") or []):
                    continue
                key = op_key(record, op)
                st = state.get(key) or {}
                if st.get("result") == "done" or (st.get("result") == "dead" and not include_dead):
                    continue
                out.append(PendingOp(key, op, record, vs[:-1], st.get("attempt", 0), st.get("next_at"),
                                     st.get("error")))
        return sorted(out, key=lambda p: OPS.index(p.op))

    def notion_url(self, record: dict) -> str | None:
        """이 버전의 노션 저장이 끝났으면 그 페이지 URL (정책서 반영 제안의 링크로 쓴다)."""
        st = self.latest().get(op_key(record, NOTION_UPSERT)) or {}
        return st.get("notion_url") if st.get("result") == "done" else None

    def summary(self, now: datetime | None = None) -> dict:
        now = now or _now()
        ops = self.pending(include_dead=True)
        state = self.latest()
        dead = [p for p in ops if (state.get(p.key) or {}).get("result") == "dead"]
        waiting = [p for p in ops if p not in dead]
        return {"pending": len(waiting), "due": sum(1 for p in waiting if p.due(now)), "dead": len(dead),
                "oldest_pending_at": min((p.record.get("approved_at") for p in waiting), default=None)}
