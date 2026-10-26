"""/정책검색 슬래시 커맨드 + 정책 API 호출 공용 함수 + 승인 후 자동 재인덱싱.
봇은 RAG를 직접 돌리지 않고 FastAPI(app/api)만 호출한다.
질문 답변은 /정책질문 대신 `@봇` 멘션으로 받는다 (mention.py).

bot.py에서:
    from policy_search import register_policy_commands
    register_policy_commands(bot.tree)
views.py 승인 직후:
    schedule_reindex()

API 서버 주소: .env 의 POLICY_API_URL (기본 http://localhost:8000)
"""
import asyncio
import logging
import os

import aiohttp  # discord.py 의존성이라 이미 설치되어 있음
import discord
from discord import app_commands

from config import AREAS, STATUSES

log = logging.getLogger("policy-bot")

API_URL = os.environ.get("POLICY_API_URL", "http://localhost:8000").rstrip("/")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
API_KEY = os.environ.get("API_KEY", "")  # API 서버가 키를 요구할 때 (X-API-Key)
TIMEOUT = aiohttp.ClientTimeout(total=90)

# Notion은 방금 만든 페이지가 query 결과에 뜨기까지 몇 초 걸릴 수 있어서 잠깐 기다렸다가 동기화
REINDEX_INITIAL_DELAY = 3.0
REINDEX_RETRY_DELAY = 5.0
REINDEX_RETRIES = 6

AREA_CHOICES = [app_commands.Choice(name=a, value=a) for a in AREAS]
STATUS_CHOICES = [app_commands.Choice(name=s, value=s) for s in STATUSES]

_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=TIMEOUT)
    return _session


async def call_api(method: str, path: str, **kwargs) -> tuple[int, dict]:
    try:
        headers = {**({"X-API-Key": API_KEY} if API_KEY else {}), **kwargs.pop("headers", {})}
        async with _get_session().request(method, f"{API_URL}{path}", headers=headers, **kwargs) as r:
            try:
                data = await r.json()
            except aiohttp.ContentTypeError:
                data = {"detail": await r.text()}
            return r.status, data
    except aiohttp.ClientConnectionError:
        return 0, {"detail": f"정책 API 서버({API_URL})에 연결할 수 없어요. uvicorn이 떠 있는지 확인해 주세요."}


async def _reindex() -> None:
    """동기화 작업 생성. 이미 돌고 있으면(409) 그 작업이 이번 결정을 못 읽었을 수 있으니 끝날 때까지 기다렸다 다시 요청."""
    await asyncio.sleep(REINDEX_INITIAL_DELAY)
    for _ in range(REINDEX_RETRIES):
        code, data = await call_api("POST", "/admin/sync-jobs", json={}, headers={"X-Admin-Token": ADMIN_TOKEN})
        if code == 202:
            log.info("재인덱싱 시작 (job=%s)", data.get("id"))
            return
        if code == 409:
            await asyncio.sleep(REINDEX_RETRY_DELAY)
            continue
        log.warning("재인덱싱 요청 실패 (%s): %s", code, data.get("detail"))
        return
    log.warning("재인덱싱: 진행 중인 작업이 계속 있어 이번 요청은 건너뜀")


_background: set[asyncio.Task] = set()


def schedule_reindex() -> None:
    """승인 응답을 막지 않도록 백그라운드로 실행. 실패해도 저장에는 영향 없음."""
    task = asyncio.create_task(_reindex())
    _background.add(task)  # 참조를 들고 있어야 GC로 중간에 사라지지 않음
    task.add_done_callback(_background.discard)


DISCORD_LIMIT = 1900  # 메시지 2000자 제한에 여유


def split_message(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    """줄 단위로 끊어서 나눈다 (링크·단어 중간에서 잘리지 않게). 한 줄이 limit보다 길면 그 줄만 강제로 자른다."""
    parts, buf = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if buf:
                parts.append(buf)
                buf = ""
            parts.append(line[:limit])
            line = line[limit:]
        if buf and len(buf) + 1 + len(line) > limit:
            parts.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        parts.append(buf)
    return parts


async def send_chunked(message: discord.Message, text: str) -> None:
    """첫 조각은 질문에 답장으로, 나머지는 이어서 보낸다. 링크 미리보기는 끈다."""
    parts = split_message(text)
    await message.reply(parts[0], mention_author=False, suppress_embeds=True)
    for part in parts[1:]:
        await message.channel.send(part, suppress_embeds=True)


def _tag(it: dict) -> str:
    return " · ".join(x for x in (it.get("area"), it.get("status"), it.get("decided_at"), it.get("decider")) if x)


def register_policy_commands(tree: app_commands.CommandTree) -> None:
    @tree.command(name="정책검색", description="저장된 정책 결정을 의미 기반으로 검색해요")
    @app_commands.describe(검색어="찾고 싶은 내용", 영역="기능영역 필터 (선택)", 상태="상태 필터 (선택)")
    @app_commands.choices(영역=AREA_CHOICES, 상태=STATUS_CHOICES)
    async def search(
        interaction: discord.Interaction,
        검색어: str,
        영역: app_commands.Choice[str] | None = None,
        상태: app_commands.Choice[str] | None = None,
    ):
        await interaction.response.defer(thinking=True)
        params = {"q": 검색어, "k": 5}
        if 영역:
            params["area"] = 영역.value
        if 상태:
            params["status"] = 상태.value
        code, data = await call_api("GET", "/policies/search", params=params)
        if code != 200:
            await interaction.followup.send(f"검색 실패 ({code}): {data.get('detail')}")
            return
        if not data["items"]:
            await interaction.followup.send("관련 정책을 찾지 못했어요.")
            return

        embed = discord.Embed(title=f"🔎 {검색어}", color=0x5865F2)
        for it in data["items"]:
            link = f"\n[원문 보기]({it['url']})" if it.get("url") else ""
            embed.add_field(
                name=f"{it['title'][:200]}  ({it['score']:.2f})",
                value=f"{_tag(it)}\n{it['snippet'][:700]}{link}"[:1024],
                inline=False,
            )
        await interaction.followup.send(embed=embed)
