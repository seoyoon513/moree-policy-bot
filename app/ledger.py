"""결정 원장 data/history.jsonl — 읽고 쓰는 곳은 여기 하나 (ARCHITECTURE_REVIEW 2-1, 3-4 ①).

- append-only. 같은 기록 ID가 여러 줄이면 버전이고, 마지막 줄이 현재 버전이다.
- 쓰기: 한 줄을 write 한 번 + flush + fsync → 승인 직후 전원이 나가도 줄이 남는다.
- 읽기: 깨진 줄(반쯤 쓴 줄 등)은 건너뛰되 개수를 센다(broken). 전에는 읽는 곳마다 달라서
  storage는 조용히 건너뛰고, 검색 로더(JsonlLoader)는 예외로 인덱싱 전체가 실패했다.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("policy-ledger")

FILENAME = "history.jsonl"


class Ledger:
    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / FILENAME
        self.broken = 0  # 마지막 read에서 건너뛴 줄 수

    def records(self) -> list[dict]:
        """모든 줄(모든 버전)을 쓴 순서대로. 기록이 아닌 줄·깨진 줄은 건너뛴다."""
        self.broken = 0
        if not self.path.exists():
            return []
        out = []
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                self.broken += 1
                log.warning("원장 %s %d번째 줄을 읽을 수 없어 건너뜀", self.path, n)
                continue
            if isinstance(r, dict) and r.get("id"):
                out.append(r)
        return out

    def versions(self) -> dict[str, list[dict]]:
        """기록 ID → 버전 목록 (오래된 순)."""
        out: dict[str, list[dict]] = {}
        for r in self.records():
            out.setdefault(r["id"], []).append(r)
        return out

    def current(self, thread_id: str | None = None) -> list[dict]:
        """각 기록의 현재 버전. thread_id를 주면 그 스레드 것만."""
        latest = [vs[-1] for vs in self.versions().values()]
        if thread_id is not None:
            latest = [r for r in latest if str((r.get("source") or {}).get("thread_id")) == str(thread_id)]
        return latest

    def thread_ids(self) -> set[str]:
        return {str(tid) for r in self.records() if (tid := (r.get("source") or {}).get("thread_id"))}

    def append(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
