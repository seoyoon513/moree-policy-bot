"""원장 ↔ 노션 대조 (A-5d). 기본은 보고만. 서버에서는 API 컨테이너 안에서 실행한다.

    python -m scripts.reconcile                 # 보고서
    python -m scripts.reconcile --fix-missing   # 노션에 없는 기록(missing)만 만든다 — 지운 페이지(deleted)는 되살리지 않음
"""
import argparse
import asyncio

from shared.config import get_settings
from shared.logging_setup import new_request_id, setup_logging
from app.notion.decisions import get_decision_repository
from app.services import reconcile

LABELS = {"pending": "⏳ 처리 중(후속 작업)", "missing": "❓ 노션에 없음", "deleted": "🗑️ 노션에서 지워짐",
          "stale": "🕰️ 노션이 옛 버전", "edited": "✏️ 노션에서 고침", "unknown_pages": "📄 원장에 없는 페이지"}


async def main(fix: bool) -> None:
    s = get_settings()
    repo = get_decision_repository(s)
    if repo is None:
        raise SystemExit("노션이 설정되지 않았어요.")
    report = await reconcile.reconcile(s.data_dir, repo)
    for key, label in LABELS.items():
        items = report[key]
        print(f"{label}: {len(items)}건")
        for it in items[:20]:
            extra = f" · {','.join(it['fields'])}" if it.get("fields") else ""
            print(f"    {it.get('id') or it.get('record_id') or '-'} {it.get('topic') or it.get('title', '')}"[:90] + extra)
    print("✅ 어긋남 없음" if report["ok"] else "⚠️ 확인이 필요한 항목이 있어요")
    if fix and report["missing"]:
        urls = await reconcile.fix_missing(s.data_dir, repo, report)
        print(f"노션에 {len(urls)}건 만듦")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix-missing", action="store_true")
    args = ap.parse_args()
    setup_logging("script")
    new_request_id("r-")
    asyncio.run(main(args.fix_missing))
