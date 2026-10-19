"""/정책검색, /정책질문 슬래시 커맨드 + 승인 후 자동 재인덱싱.
봇은 RAG를 직접 돌리지 않고 FastAPI(app/api)만 호출한다.

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


async def _call(method: str, path: str, **kwargs) -> tuple[int, dict]:
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
        code, data = await _call("POST", "/admin/sync-jobs", json={}, headers={"X-Admin-Token": ADMIN_TOKEN})
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
        code, data = await _call("GET", "/policies/search", params=params)
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

    @tree.command(name="정책질문", description="저장된 정책 결정을 근거로 질문에 답해요")
    @app_commands.describe(질문="예: 오프라인으로 앱 실행하면 어디로 진입하기로 했지?", 영역="기능영역 필터 (선택)")
    @app_commands.choices(영역=AREA_CHOICES)
    async def ask(interaction: discord.Interaction, 질문: str, 영역: app_commands.Choice[str] | None = None):
        await interaction.response.defer(thinking=True)
        payload = {"question": 질문}
        if 영역:
            payload["area"] = 영역.value
        code, data = await _call("POST", "/policies/answers", json=payload)
        if code != 200:
            await interaction.followup.send(f"답변 실패 ({code}): {data.get('detail')}")
            return

        sources = "\n".join(
            f"[{i}] [{s['title']}]({s['url']})" if s.get("url") else f"[{i}] {s['title']}"
            for i, s in enumerate(data["sources"], 1)
        )
        u = data.get("usage")
        footer = (f"\n-# 토큰 in {u['input_tokens']:,} / out {u['output_tokens']:,} · ${u['cost_usd']:.4f} · {u['latency_ms'] / 1000:.1f}s"
                  if u else "")
        text = f"**Q. {질문}**\n\n{data['answer']}\n\n**근거 문서**\n{sources}{footer}"
        for i in range(0, len(text), 1900):  # Discord 메시지 2000자 제한
            await interaction.followup.send(text[i : i + 1900], suppress_embeds=True)
