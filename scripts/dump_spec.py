"""정책서를 정책 ID 단위로 파싱한 결과를 출력한다 (3-2 확인용).

    python -m scripts.dump_spec              # .env NOTION_SPEC_PAGES의 모든 정책서
    python -m scripts.dump_spec <페이지 URL/ID> [--json]
"""
import argparse
import asyncio
import json

from app.rag.spec_loader import entry_dict, load_spec
from config import DATA_DIR, NOTION_SPEC_PAGES
from notion_store import extract_id, get_client


async def dump(page_ids: list[str], as_json: bool) -> None:
    for pid in page_ids:
        doc = await load_spec(get_client(), pid, f"{DATA_DIR}/spec")
        if as_json:
            print(json.dumps({"title": doc.title, "screens": doc.screens, "warnings": doc.warnings,
                              "entries": [entry_dict(e) for e in doc.entries]}, ensure_ascii=False, indent=1))
            continue
        ids = [e for e in doc.entries if e.policy_id]
        print(f"# {doc.title}  ({doc.url})")
        print(f"정책 ID {len(ids)}개 · ID 없는 항목 {len(doc.entries) - len(ids)}개 · 화면 {len(doc.screens)}개")
        for sid, s in doc.screens.items():
            print(f"\n## {s['title']}" + (f" — {s['purpose']}" if s["purpose"] else ""))
            for e in doc.entries:
                if e.screen_id == sid:
                    extra = []
                    if e.extra_block_ids:
                        extra.append(f"이어붙인 행 {len(e.extra_block_ids)}")
                    if e.duplicate_block_ids:
                        extra.append(f"중복 {len(e.duplicate_block_ids)}")
                    print(f"  {e.policy_id or '-':10} {e.name[:30]:30} {' · '.join(extra)}")
        others = [e for e in doc.entries if not e.screen_id]
        if others:
            print("\n## 화면 밖 항목")
            for e in others:
                print(f"  {e.policy_id or '-':10} {e.section_path[:50]} | {e.name[:30]}")
        if doc.warnings:
            print("\n⚠️ 경고")
            for w in doc.warnings:
                print(f"  - {w}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page", nargs="?", help="정책서 페이지 URL 또는 ID (없으면 NOTION_SPEC_PAGES 전부)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    pages = [extract_id(args.page)] if args.page else list(NOTION_SPEC_PAGES.values())
    if not pages:
        raise SystemExit("정책서 페이지를 인자로 주거나 .env에 NOTION_SPEC_PAGES를 설정해 주세요.")
    asyncio.run(dump(pages, args.json))


if __name__ == "__main__":
    main()
