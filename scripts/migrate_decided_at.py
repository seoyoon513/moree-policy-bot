"""기존 기록에 결정일(decided_at)을 채우는 1회성 마이그레이션.

예전에는 '승인일' 하나에 /정책정리는 클릭 시각, 일괄 정리는 스레드 마지막 메시지 시각을 넣어 기준이 섞여 있었다.
이제 결정일 = 근거 메시지 중 가장 늦은 시각, 정리일 = 승인 시각으로 나눈다.
근거 메시지 링크의 Discord ID(snowflake)에 작성 시각이 들어 있어 API 호출 없이 다시 계산할 수 있다.

    python -m scripts.migrate_decided_at                    # 로컬: 미리보기만
    python -m scripts.migrate_decided_at --apply            # 로컬 history.jsonl + decisions/ 갱신 (백업 후)
    python -m scripts.migrate_decided_at --notion           # Notion: 미리보기만
    python -m scripts.migrate_decided_at --notion --apply   # Notion: '결정일'·'정리일' 채우기 ('승인일' 값은 정리일로 복사)
    python -m scripts.migrate_decided_at --notion --apply --drop-old   # 위 + 옛 '승인일' 속성 삭제

'결정일'·'정리일' 속성은 추가만 하므로 예전 코드가 돌고 있어도 깨지지 않는다.
'승인일'을 지우는 --drop-old는 봇·API를 새 코드로 재시작한 뒤에 쓴다. 여러 번 실행해도 결과가 같다.
"""
import argparse
import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path

import discord

from config import DATA_DIR, KST

OLD_PROP, NEW_PROP, APPROVED_PROP = "승인일", "결정일", "정리일"


def decided_at_from_urls(urls: list[str]) -> str | None:
    """메시지 링크 끝의 ID(snowflake)에서 작성 시각을 꺼내 가장 늦은 것을 돌려준다."""
    ids = []
    for u in urls or []:
        tail = u.rstrip("/").rsplit("/", 1)[-1]
        if tail.isdigit():
            ids.append(int(tail))
    if not ids:
        return None
    return discord.utils.snowflake_time(max(ids)).astimezone(KST).isoformat(timespec="seconds")


def migrate_record(r: dict) -> dict:
    """decided_at을 채운 새 record. 근거 링크가 없으면 기존 승인일을 그대로 쓴다."""
    new = dict(r)
    new["decided_at"] = decided_at_from_urls((r.get("source") or {}).get("message_urls")) or r["approved_at"]
    return new


def load_history() -> list[dict]:
    p = Path(DATA_DIR) / "history.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _day(iso: str | None) -> str:
    return (iso or "")[:10]


def report(pairs: list[tuple[dict, dict]]) -> None:
    changed = [(o, n) for o, n in pairs if _day(o["approved_at"]) != _day(n["decided_at"])]
    no_url = sum(1 for o, _ in pairs if not (o.get("source") or {}).get("message_urls"))
    print(f"기록 {len(pairs)}건 · 날짜가 바뀌는 기록 {len(changed)}건 · 근거 링크가 없어 승인일 유지 {no_url}건")
    for o, n in changed[:15]:
        print(f"  {o['id']} {_day(o['approved_at'])} → {_day(n['decided_at'])}  {o['topic'][:40]}")
    if len(changed) > 15:
        print(f"  … 외 {len(changed) - 15}건")


def apply_local(records: list[dict]) -> None:
    base = Path(DATA_DIR)
    backup = base / "backup"
    backup.mkdir(exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
    shutil.copy(base / "history.jsonl", backup / f"history-{stamp}.jsonl")
    (base / "history.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    by_id = {r["id"]: r for r in records}
    updated = 0
    for f in (base / "decisions").glob("*/*.json"):
        rid = f.stem.rsplit("_", 1)[-1]
        if rid in by_id:
            f.write_text(json.dumps(by_id[rid], ensure_ascii=False, indent=2), encoding="utf-8")
            updated += 1
    print(f"✅ 로컬 갱신: history.jsonl {len(records)}건, decisions/ 파일 {updated}개 (백업: {backup}/history-{stamp}.jsonl)")


async def migrate_notion(records: list[dict], apply: bool, drop_old: bool = False) -> None:
    import notion_store
    from app.rag.notion_loader import prop_to_str
    from config import NOTION_DATA_SOURCE_ID

    if not notion_store.enabled():
        raise SystemExit("Notion이 설정돼 있지 않아요 (.env의 NOTION_TOKEN, NOTION_DATA_SOURCE_ID).")
    client = notion_store.get_client()
    ds = await client.data_sources.retrieve(data_source_id=NOTION_DATA_SOURCE_ID)
    props = ds.get("properties", {})

    # 1) 이름을 바꾸기 전에 페이지별 기존 값(=정리일로 옮길 값)을 읽어 둔다
    pages, cursor = [], None
    while True:
        kw = {"data_source_id": NOTION_DATA_SOURCE_ID, "page_size": 100, **({"start_cursor": cursor} if cursor else {})}
        res = await client.data_sources.query(**kw)
        pages += [p for p in res["results"] if p.get("object") == "page" and not p.get("in_trash")]
        if not res.get("has_more"):
            break
        cursor = res["next_cursor"]

    by_id = {r["id"]: r for r in records}
    plan = []  # (page_id, 결정일, 정리일, 주제)
    unknown = 0
    for p in pages:
        pr = p["properties"]
        rid = prop_to_str(pr.get("기록ID"))
        old = prop_to_str(pr.get(APPROVED_PROP)) or prop_to_str(pr.get(OLD_PROP))
        r = by_id.get(rid)
        if not r:  # 사람이 직접 만든 페이지 등: 있는 값을 그대로 옮긴다
            unknown += 1
            decided = prop_to_str(pr.get(NEW_PROP)) or prop_to_str(pr.get(OLD_PROP))
        else:
            decided = r["decided_at"]
        plan.append((p["id"], decided, old, prop_to_str(pr.get("주제"))))

    schema = {name: {"date": {}} for name in (NEW_PROP, APPROVED_PROP) if name not in props}
    print(f"Notion 페이지 {len(pages)}개 (history에 없는 페이지 {unknown}개 — 결정일 유지, 정리일만 채움)")
    print(f"속성 변경: {schema or '없음 (이미 마이그레이션됨)'}")
    changed = [x for x in plan if _day(x[1]) != _day(x[2])]
    print(f"결정일이 정리일과 다른 페이지 {len(changed)}개")
    if not apply:
        print("미리보기만 했어요. 반영하려면 --apply")
        return

    if schema:
        await client.data_sources.update(data_source_id=NOTION_DATA_SOURCE_ID, properties=schema)
    for i, (page_id, decided, approved, _) in enumerate(plan, start=1):
        properties = {}
        if decided:
            properties[NEW_PROP] = {"date": {"start": decided}}
        if approved:
            properties[APPROVED_PROP] = {"date": {"start": approved}}
        if properties:
            await client.pages.update(page_id=page_id, properties=properties)
            await asyncio.sleep(0.35)  # Notion 레이트리밋(초당 약 3회)
        if i % 20 == 0:
            print(f"  … {i}/{len(plan)}")
    if drop_old and OLD_PROP in props:
        await client.data_sources.update(data_source_id=NOTION_DATA_SOURCE_ID, properties={OLD_PROP: None})
        print(f"🗑  '{OLD_PROP}' 속성 삭제 (값은 '{APPROVED_PROP}'에 복사됨)")
    print(f"✅ Notion 갱신 완료: {len(plan)}페이지. 봇·API를 새 코드로 재시작하고 재인덱싱하세요.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 반영 (없으면 미리보기)")
    ap.add_argument("--notion", action="store_true", help="로컬 대신 Notion DB를 갱신")
    ap.add_argument("--drop-old", action="store_true", help="Notion의 옛 '승인일' 속성 삭제 (새 코드 배포 후)")
    args = ap.parse_args()

    old = load_history()
    new = [migrate_record(r) for r in old]
    if args.notion:
        asyncio.run(migrate_notion(new, args.apply, args.drop_old))
        return
    report(list(zip(old, new)))
    if args.apply:
        apply_local(new)
    else:
        print("미리보기만 했어요. 반영하려면 --apply")


if __name__ == "__main__":
    main()
