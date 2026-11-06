"""후속 작업 장부(outbox) 보기·다시 실행 (A-5). 서버에서는 API 컨테이너 안에서 실행한다.

    python -m scripts.outbox list              # 대기·실패·포기(dead) 목록
    python -m scripts.outbox retry             # 대기 중인 것 전부 지금 다시 (next_at 무시)
    python -m scripts.outbox retry --key <작업키> [--include-dead]

작업은 멱등이라 여러 번 실행해도 결과가 같다 (노션은 기록ID로 찾아 갱신, 정책서 반영은 마커로 중복 방지).
"""
import argparse
import asyncio

from shared.config import get_settings
from shared.logging_setup import new_request_id, setup_logging
from app.notion.decisions import get_decision_repository
from app.outbox import Outbox
from app.services import followups


def show(outbox: Outbox) -> None:
    state = outbox.latest()
    ops = outbox.pending(include_dead=True)
    if not ops:
        print("대기 중인 후속 작업이 없어요.")
        return
    for p in ops:
        st = state.get(p.key) or {}
        result = st.get("result", "pending")
        when = f" · 다음 {p.next_at}" if result == "failed" else ""
        print(f"[{result}] {p.key} · {p.record.get('topic', '')[:40]} · 시도 {p.attempt}회{when}")
        if p.last_error:
            print(f"    {p.last_error[:200]}")
    print(outbox.summary())


async def retry(outbox: Outbox, key: str | None, include_dead: bool) -> None:
    keys = {key} if key else None
    if include_dead:  # dead를 다시 살리려면 failed 줄을 하나 남겨 대기 목록에 다시 들어오게 한다
        for p in outbox.pending(include_dead=True):
            if (keys is None or p.key in keys) and (outbox.latest().get(p.key) or {}).get("result") == "dead":
                outbox.record(p.key, "failed", attempt=0, error="수동 재시도")
    out = await followups.run_pending(outbox, get_decision_repository(), keys=keys, only_due=False, user="outbox-retry")
    print({k: v for k, v in out.items() if k != "reflect_outcomes"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "retry"])
    ap.add_argument("--key")
    ap.add_argument("--include-dead", action="store_true")
    args = ap.parse_args()
    setup_logging("script")
    new_request_id("o-")
    outbox = Outbox(get_settings().data_dir)
    if args.cmd == "list":
        show(outbox)
    else:
        asyncio.run(retry(outbox, args.key, args.include_dead))


if __name__ == "__main__":
    main()
