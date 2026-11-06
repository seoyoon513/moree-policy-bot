"""운영 상태와 알림 (ARCHITECTURE_REVIEW A-2).

- notify(): 운영 채널(디스코드 웹훅)로 한 줄 알림. 웹훅이 없으면 로그만. 실패해도 예외를 내지 않는다.
- 동기화 상태: 마지막 재인덱싱 결과를 data/sync_state.json에 남긴다.
  작업 목록(_jobs)은 메모리라 API를 재시작하면 사라지지만, "마지막으로 언제 성공했나"는 /health에서 계속 보여야 한다.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings

log = logging.getLogger("policy-ops")

SYNC_FAILURE_ALERT_AT = 3  # 연속 실패가 이 횟수가 되는 순간 한 번 알린다


def notify(s: Settings, message: str) -> bool:
    """운영 알림. 보냈으면 True. 블로킹 호출이라 asyncio 코드에서는 asyncio.to_thread로 부른다."""
    log.warning("운영 알림: %s", message)
    url = s.resolved_ops_webhook_url()
    if not url:
        return False
    try:
        req = urllib.request.Request(url, method="POST", data=json.dumps({"content": message[:1900]}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "policy-bot"})
        urllib.request.urlopen(req, timeout=5).close()
        return True
    except OSError as e:  # 알림 실패가 본 기능을 막으면 안 된다
        log.warning("운영 알림 전송 실패: %s", e)
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sync_state_path(s: Settings) -> Path:
    return Path(s.data_dir) / "sync_state.json"


def read_sync_state(s: Settings) -> dict:
    try:
        return json.loads(sync_state_path(s).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_atomic(path: Path, data: dict) -> None:
    """임시 파일에 쓰고 교체 → 쓰다가 죽어도 반쯤 쓴 파일이 남지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def record_sync_result(s: Settings, *, ok: bool, result: dict | None = None, error: str | None = None) -> dict:
    """동기화 한 번의 결과를 남기고, 연속 실패가 SYNC_FAILURE_ALERT_AT회가 되면 알린다."""
    state = read_sync_state(s)
    if ok:
        state.update(last_ok_at=_now(), last_result=result, consecutive_failures=0)
    else:
        state.update(last_error_at=_now(), last_error=(error or "")[:500],
                     consecutive_failures=state.get("consecutive_failures", 0) + 1)
    try:
        _write_atomic(sync_state_path(s), state)
    except OSError as e:
        log.warning("동기화 상태 기록 실패: %s", e)
    if not ok and state["consecutive_failures"] == SYNC_FAILURE_ALERT_AT:
        notify(s, f"⚠️ 검색 인덱스 동기화가 {SYNC_FAILURE_ALERT_AT}번 연속 실패했어요. "
                  f"새 결정이 검색에 안 나올 수 있어요. 마지막 오류: {state['last_error'][:300]}")
    return state


def health_report(s: Settings, indexed_chunks: int) -> dict:
    """/health 응답. 설정 문제·동기화 실패가 있으면 status=degraded.
    HTTP 코드는 200으로 둔다: 컨테이너를 재시작해도 설정·노션 문제는 고쳐지지 않으니
    Docker healthcheck(=재시작 판단)는 '프로세스가 응답하나'만 보고, 내용은 사람이 본다."""
    state = read_sync_state(s)
    problems = s.problems("api")
    if state.get("consecutive_failures"):
        problems.append(f"동기화 연속 실패 {state['consecutive_failures']}회: {state.get('last_error', '')[:200]}")
    if not state.get("last_ok_at"):
        problems.append("동기화 성공 기록이 없어요 (POST /admin/sync-jobs).")
    return {
        "status": "degraded" if problems else "ok",
        "indexed_chunks": indexed_chunks,
        "last_sync_ok_at": state.get("last_ok_at"),
        "last_sync_error_at": state.get("last_error_at"),
        "problems": problems,
    }
