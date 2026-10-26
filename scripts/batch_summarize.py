"""여러 스레드를 한 번에 /정책정리 하는 스크립트 (초기 데이터 채우기용).

    # 1) 읽기 + 요약 → 검토 파일 생성 (아무것도 저장하지 않음)
    python -m scripts.batch_summarize links.txt
    # 2) 검토 파일에서 뺄 것만 "include": false 로 바꾼 뒤 저장
    python -m scripts.batch_summarize --commit data/batch/review-<시각>.json

links.txt: 한 줄에 링크 하나. '#'으로 시작하는 줄은 무시.
  - 스레드 링크  https://discord.com/channels/<서버>/<스레드>
  - 메시지 링크  https://discord.com/channels/<서버>/<스레드>/<메시지>  (스레드로 취급)
  - 포럼/채널 링크 → 그 채널의 스레드 전부 (진행중 + 보관된 것)

Discord에 남는 흔적 없음:
  - 게이트웨이에 접속하지 않고 REST로 '읽기'만 한다 (메시지 전송·반응·입력중 표시·온라인 표시 없음)
  - 스레드에 참여(join)하지 않는다. 봇이 멤버가 아닌 비공개 스레드는 읽지 못하고 '건너뜀'으로 보고만 한다
요약은 /정책정리와 같은 프롬프트(summarizer.py)를 쓰므로 결과 품질이 같다.
"""
import argparse
import asyncio
import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import discord

from config import ANTHROPIC_API_KEY, DATA_DIR, DISCORD_TOKEN, KST, MAX_MESSAGES
from summarizer import SummaryError, ThreadMessage, summarize_thread
from storage import resummary_context, thread_source
from thread_reader import to_thread_messages

LINK_RE = re.compile(r"discord(?:app)?\.com/channels/(\d+)/(\d+)(?:/(\d+))?")
CONCURRENCY = 3  # 동시에 요약할 스레드 수 (Claude 레이트리밋 고려)


def parse_links(path: str) -> list[tuple[int, int]]:
    out, seen = [], set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = LINK_RE.search(line)
        if not m:
            print(f"⚠️  링크 형식 아님, 건너뜀: {line}")
            continue
        key = (int(m.group(1)), int(m.group(2)))
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def already_saved_thread_ids() -> set[str]:
    p = Path(DATA_DIR) / "history.jsonl"
    if not p.exists():
        return set()
    ids = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            ids.add(str(json.loads(line)["source"]["thread_id"]))
        except (ValueError, KeyError, TypeError):
            pass
    return ids


async def expand_channel(client: discord.Client, guild_id: int, ch) -> list[discord.Thread]:
    """포럼/텍스트 채널 → 그 안의 스레드 (진행중 + 보관)."""
    threads: dict[int, discord.Thread] = {}
    guild = await client.fetch_guild(guild_id)
    for t in await guild.active_threads():
        if t.parent_id == ch.id:
            threads[t.id] = t
    try:
        async for t in ch.archived_threads(limit=None):
            threads[t.id] = t
    except discord.Forbidden:
        print(f"⚠️  #{ch.name}: 보관된 스레드 목록 권한 없음 (Read Message History 확인)")
    return list(threads.values())


async def collect(client: discord.Client, thread: discord.Thread) -> list[ThreadMessage]:
    raw: list[discord.Message] = []
    # 텍스트 채널 스레드는 시작 메시지가 부모 채널에 있다 (포럼은 history에 포함)
    try:
        parent = await client.fetch_channel(thread.parent_id)
        if isinstance(parent, discord.TextChannel):
            raw.append(await parent.fetch_message(thread.id))
    except discord.HTTPException:
        pass
    starter_ids = {m.id for m in raw}
    async for m in thread.history(limit=MAX_MESSAGES, oldest_first=True):
        if m.id not in starter_ids:
            raw.append(m)
    return await to_thread_messages(raw)


async def read_and_summarize(links_path: str, force: bool, limit: int | None) -> None:
    targets = parse_links(links_path)
    saved = set() if force else already_saved_thread_ids()
    client = discord.Client(intents=discord.Intents.none())
    await client.login(DISCORD_TOKEN)  # REST 인증만. connect()를 안 하므로 온라인 표시도 안 됨
    try:
        threads: list[discord.Thread] = []
        report: list[dict] = []
        for guild_id, ch_id in targets:
            try:
                ch = await client.fetch_channel(ch_id)
            except discord.Forbidden:
                report.append({"id": ch_id, "skip": "권한 없음 (비공개 스레드면 봇이 멤버가 아님)"})
                continue
            except discord.NotFound:
                report.append({"id": ch_id, "skip": "찾을 수 없음 (링크 확인)"})
                continue
            if isinstance(ch, discord.Thread):
                threads.append(ch)
            elif isinstance(ch, (discord.ForumChannel, discord.TextChannel)):
                found = await expand_channel(client, guild_id, ch)
                print(f"📂 #{ch.name}: 스레드 {len(found)}개")
                threads.extend(found)
            else:
                report.append({"id": ch_id, "skip": f"스레드/채널이 아님 ({type(ch).__name__})"})

        uniq = {t.id: t for t in threads}
        todo = [t for t in uniq.values() if str(t.id) not in saved]
        for t in uniq.values():
            if str(t.id) in saved:
                report.append({"id": t.id, "name": t.name, "skip": "이미 저장된 스레드 (--force로 다시)"})
        if limit:
            todo = todo[:limit]
        print(f"🧵 처리할 스레드 {len(todo)}개 (건너뜀 {len(report)}개)")

        sem = asyncio.Semaphore(CONCURRENCY)
        results: list[dict] = []

        async def one(t: discord.Thread):
            async with sem:
                try:
                    msgs = await collect(client, t)
                except discord.Forbidden:
                    report.append({"id": t.id, "name": t.name, "skip": "메시지 읽기 권한 없음 (비공개 스레드?)"})
                    return
                if not msgs:
                    report.append({"id": t.id, "name": t.name, "skip": "사람이 쓴 메시지 없음"})
                    return
                # 이미 정리한 스레드(--force)는 현재 기록과 비교해 바뀐 것만 남긴다 (Q-7, /정책정리와 같은 규칙)
                ctx = resummary_context(str(t.id), msgs)
                if ctx.nothing_new:
                    report.append({"id": t.id, "name": t.name, "skip": "마지막 정리 이후 새 메시지 없음"})
                    return
                try:
                    decisions = await summarize_thread(t.name, msgs, ctx.previous or None, ctx.since)
                except SummaryError as e:
                    report.append({"id": t.id, "name": t.name, "skip": f"요약 실패: {e}"})
                    if e.raw:  # 왜 깨졌는지 볼 수 있게 원문 저장
                        fail_dir = Path(DATA_DIR) / "batch" / "failed"
                        fail_dir.mkdir(parents=True, exist_ok=True)
                        (fail_dir / f"{t.id}.txt").write_text(e.raw, encoding="utf-8")
                    return
                unchanged = [d for d in decisions if d["relation"] == "unchanged"]
                decisions = [d for d in decisions if d["relation"] != "unchanged"]
                for d in decisions:
                    d["include"] = True
                results.append({
                    "thread_name": t.name,
                    "message_count": len(msgs),
                    "source": thread_source(str(t.id), t.name, t.jump_url, msgs),
                    "decisions": decisions,
                    "unchanged": [{"prior_id": d["prior_id"], "topic": d["topic"]} for d in unchanged],
                })
                extra = f" (변동 없음 {len(unchanged)}건 제외)" if ctx.previous else ""
                print(f"  ✓ {t.name[:40]} — 메시지 {len(msgs)}개 → 결정 {len(decisions)}건{extra}")

        await asyncio.gather(*(one(t) for t in todo))
    finally:
        await client.close()

    results.sort(key=lambda r: r["thread_name"])
    out_dir = Path(DATA_DIR) / "batch"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
    review = out_dir / f"review-{stamp}.json"
    review.write_text(json.dumps({"threads": results, "skipped": report}, ensure_ascii=False, indent=2), encoding="utf-8")

    # 사람이 훑어보기 쉬운 요약본
    lines = [f"# 일괄 요약 검토 ({stamp})", ""]
    n = 0
    for r in results:
        lines.append(f"## {r['thread_name']}  ({r['message_count']}개 메시지) {r['source']['thread_url']}")
        if not r["decisions"]:
            lines.append("- (결정 없음)")
        for d in r["decisions"]:
            n += 1
            rel = {"update": f"🔁 변경(기록 {d.get('prior_id')}) ", "reopen": f"↩️ 재논의(기록 {d.get('prior_id')}) "}
            lines.append(f"- **[{n}] {rel.get(d.get('relation'), '')}[{d['area']}] {d['status']} · {d['topic']}** — {d['decision']}")
        for u in r.get("unchanged") or []:
            lines.append(f"- ➖ 변동 없음 (기록 {u['prior_id']}) · {u['topic']}")
        lines.append("")
    if report:
        lines += ["## 건너뜀", *[f"- {s.get('name', s['id'])}: {s['skip']}" for s in report]]
    (out_dir / f"review-{stamp}.md").write_text("\n".join(lines), encoding="utf-8")

    total = sum(len(r["decisions"]) for r in results)
    print(f"\n결정 {total}건 → 검토 파일: {review}\n               요약본: {review.with_suffix('.md')}")
    print("뺄 결정은 JSON에서 \"include\": false 로 바꾼 뒤:")
    print(f"  python -m scripts.batch_summarize --commit {review}")


async def commit(review_path: str, approved_by: str, force: bool = False, reflect: bool = True) -> None:
    from storage import save_decisions  # 여기서 import: 1단계에선 Notion 설정이 없어도 돌게
    import spec_reflect

    data = json.loads(Path(review_path).read_text(encoding="utf-8"))
    saved = set() if force else already_saved_thread_ids()
    total = notion_fail = 0
    for r in data["threads"]:
        if r["source"]["thread_id"] in saved:
            print(f"  - {r['thread_name']}: 이미 저장됨, 건너뜀")
            continue
        decisions = [{k: v for k, v in d.items() if k != "include"} for d in r["decisions"] if d.get("include", True)]
        if not decisions:
            continue
        source = dict(r["source"])
        if "messages_by_index" not in source:
            print(f"  - {r['thread_name']}: 예전 형식의 검토 파일이라 결정일을 계산할 수 없음, 다시 요약해 주세요")
            continue
        # JSON을 거치면 dict 키가 문자열이 되므로 메시지 번호(int)로 되돌린다
        source["messages_by_index"] = {int(k): v for k, v in source["messages_by_index"].items()}
        # 결정일은 결정마다 근거 메시지 시각으로 계산된다 (storage.py). 승인 시각(정리일)은 지금
        results = await save_decisions(decisions, source=source, approved_by=approved_by)
        total += len(results)
        notion_fail += sum(1 for x in results if x.notion_error)
        print(f"  ✓ {r['thread_name'][:40]} — {len(results)}건 저장")
        if reflect:  # 디스코드 승인 버튼과 같은 반영 단계
            outcomes = await spec_reflect.reflect_records([(x.record, x.notion_url) for x in results], user=approved_by)
            for line in spec_reflect.format_outcomes(outcomes):
                print(f"      {line}")
    print(f"\n저장 {total}건 (Notion 실패 {notion_fail}건)")
    trigger_reindex()


def trigger_reindex() -> None:
    import os
    url = os.environ.get("POLICY_API_URL", "http://localhost:8000").rstrip("/") + "/admin/sync-jobs"
    req = urllib.request.Request(url, data=b"{}", method="POST", headers={
        "Content-Type": "application/json", "X-Admin-Token": os.environ.get("ADMIN_TOKEN", "")})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            print(f"🔄 재인덱싱 시작 ({res.status})")
    except (urllib.error.URLError, OSError) as e:
        print(f"⚠️  API 서버에 재인덱싱 요청 실패({e}). API가 꺼져 있으면: python -m scripts.ingest")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("links", nargs="?", help="스레드/채널 링크 파일")
    ap.add_argument("--commit", metavar="REVIEW_JSON", help="검토 파일을 저장")
    ap.add_argument("--approved-by", default="일괄정리", help="저장 시 승인자 이름")
    ap.add_argument("--force", action="store_true", help="이미 저장된 스레드도 다시 정리 (현재 기록과 비교해 바뀐 결정만 갱신)")
    ap.add_argument("--limit", type=int, help="앞에서부터 N개만 (시험용)")
    ap.add_argument("--no-reflect", action="store_true", help="저장만 하고 원천 정책서 반영 제안은 하지 않음")
    args = ap.parse_args()

    if args.commit:
        asyncio.run(commit(args.commit, args.approved_by, args.force, reflect=not args.no_reflect))
    elif args.links:
        if not DISCORD_TOKEN or not ANTHROPIC_API_KEY:
            raise SystemExit(".env에 DISCORD_TOKEN, ANTHROPIC_API_KEY가 필요해요.")
        asyncio.run(read_and_summarize(args.links, args.force, args.limit))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
