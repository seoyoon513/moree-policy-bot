"""저장된 결정으로 정책서 매칭만 다시 돌려 본다. 노션에 아무것도 쓰지 않는다 (3-3 정확도 측정용).

    python -m scripts.spec_match_preview --approved-by 반영테스트
    python -m scripts.spec_match_preview --thread <thread_id> --thread <thread_id>

정답 라벨: data/spec/labels.json  {"<기록ID>": {"action": "same", "policy_id": "XX-06-01", "note": "..."}}
라벨이 있는 기록은 판정(action)과 정책 ID가 맞는지 표시하고 정확도를 낸다. 매칭 비용은 usage에 spec_match로 기록된다.
"""
import argparse
import asyncio
import json
from pathlib import Path

import notion_store
from app.rag.spec_loader import load_spec
from config import DATA_DIR, NOTION_SPEC_PAGES, SPEC_REFLECT_STATUSES
from spec_reflect import match_records
from storage import current_records


def load_records(approved_by: str | None, threads: list[str]) -> list[dict]:
    """각 기록의 현재 버전 중 조건에 맞는 것 (재정리로 덮어쓴 이전 버전은 제외)."""
    out = []
    for r in current_records():
        if approved_by and r.get("approved_by") != approved_by:
            continue
        if threads and str(r.get("source", {}).get("thread_id")) not in threads:
            continue
        if r.get("status") in SPEC_REFLECT_STATUSES:
            out.append(r)
    return out


def is_correct(m, label: dict) -> bool:
    if m.action != label["action"]:
        return False
    return not label.get("policy_id") or m.policy_id == label["policy_id"]


async def preview(records: list[dict]) -> None:
    labels_path = Path(DATA_DIR) / "spec" / "labels.json"
    labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}
    by_page: dict[str, list[dict]] = {}
    for r in records:
        page = NOTION_SPEC_PAGES.get(r["area"]) or NOTION_SPEC_PAGES.get("*")
        if page:
            by_page.setdefault(notion_store.extract_id(page), []).append(r)
    scored = correct = 0
    for page_id, recs in by_page.items():
        doc = await load_spec(notion_store.get_client(), page_id, Path(DATA_DIR) / "spec")
        print(f"# {doc.title} — 결정 {len(recs)}건")
        for r, m in zip(recs, await match_records(doc, recs, user="preview")):
            label = labels.get(r["id"])
            mark = ""
            if label:
                ok = is_correct(m, label)
                scored += 1
                correct += ok
                mark = " ✅" if ok else f" ❌ (정답 {label['action']} {label.get('policy_id') or ''})"
            print(f"\n[{r['id']}] {r['topic']}\n  → {m.action} {m.policy_id or m.screen_id or ''}{mark}")
            if m.proposal:
                print(f"  제안: {m.proposal}")
            print(f"  이유: {m.reason}")
    if scored:
        print(f"\n라벨 기준 정확도: {correct}/{scored}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--approved-by", help="이 승인자로 저장된 기록만")
    ap.add_argument("--thread", action="append", default=[], help="스레드 ID (여러 번 가능)")
    args = ap.parse_args()
    if not (args.approved_by or args.thread):
        raise SystemExit("--approved-by 또는 --thread 중 하나는 필요해요.")
    records = load_records(args.approved_by, args.thread)
    if not records:
        raise SystemExit("대상 기록이 없어요.")
    asyncio.run(preview(records))


if __name__ == "__main__":
    main()
