"""이미 저장된 결정을 원천 정책서에 반영 제안한다 (승인 때 반영을 못 했거나, 규칙을 바꾼 뒤 다시 해볼 때).

    python -m scripts.reflect_saved --record <기록ID> [--record ...]
    python -m scripts.reflect_saved --approved-by <승인자>

노션에 실제로 쓴다. 쓰기 전에 판정만 보려면 scripts.spec_match_preview.
같은 기록으로 같은 행·미분류 페이지에 이미 넣었으면 다시 넣지 않는다 (새 항목 콜아웃은 중복 확인 안 함).
"""
import argparse
import asyncio

import notion_store
from spec_reflect import format_outcomes, reflect_records
from storage import current_records


def load(records: list[str], approved_by: str | None) -> list[dict]:
    """각 기록의 현재 버전 중 조건에 맞는 것."""
    return [r for r in current_records()
            if (records and r["id"] in records) or (approved_by and r.get("approved_by") == approved_by)]


async def run(recs: list[dict]) -> None:
    saved = [(r, await notion_store.find_record_url(r["id"])) for r in recs]
    for line in format_outcomes(await reflect_records(saved, user="reflect_saved")):
        print(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="append", default=[], help="기록 ID (여러 번 가능)")
    ap.add_argument("--approved-by", help="이 승인자로 저장된 기록 전부")
    args = ap.parse_args()
    if not (args.record or args.approved_by):
        raise SystemExit("--record 또는 --approved-by 중 하나는 필요해요.")
    recs = load(args.record, args.approved_by)
    if not recs:
        raise SystemExit("대상 기록이 없어요.")
    asyncio.run(run(recs))


if __name__ == "__main__":
    main()
