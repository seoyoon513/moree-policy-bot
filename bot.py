"""정책 관리 디스코드 봇 — MVP.

/정책정리 : 현재 스레드의 논의를 정책 결정으로 요약 → 승인 시 저장
"""
import logging

import discord
from discord import app_commands

from config import ANTHROPIC_API_KEY, DISCORD_TOKEN, GUILD_ID, MAX_MESSAGES
from summarizer import SummaryError, ThreadMessage, summarize_thread
from policy_search import register_policy_commands
from views import ApprovalView, build_embeds

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("policy-bot")

intents = discord.Intents.default()
intents.message_content = True  # 개발자 포털에서 Message Content Intent도 켜야 함


class PolicyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("길드 %s에 커맨드 %d개 등록", GUILD_ID, len(synced))
        else:
            synced = await self.tree.sync()
            log.info("전역 커맨드 %d개 등록 (반영까지 시간 걸릴 수 있음)", len(synced))

    async def on_ready(self):
        log.info("로그인: %s (%s)", self.user, self.user.id if self.user else "-")


bot = PolicyBot()
register_policy_commands(bot.tree)  # /정책검색, /정책질문 (FastAPI 서버 필요)


def _format_content(m: discord.Message) -> str:
    parts = [m.content.strip()] if m.content.strip() else []
    parts += [f"[첨부: {a.filename}]" for a in m.attachments]
    parts += [f"[링크: {e.title or e.url}]" for e in m.embeds if e.title or e.url]
    return " ".join(parts).replace("\n", " / ")


async def collect_thread_messages(thread: discord.Thread) -> list[ThreadMessage]:
    raw: list[discord.Message] = []

    # 텍스트 채널 스레드는 시작 메시지가 부모 채널에 있음 (포럼 스레드는 history에 포함됨)
    starter = thread.starter_message
    if starter is None and isinstance(thread.parent, discord.TextChannel):
        try:
            starter = await thread.parent.fetch_message(thread.id)
        except discord.HTTPException:
            starter = None
    if starter:
        raw.append(starter)

    async for m in thread.history(limit=MAX_MESSAGES, oldest_first=True):
        if starter and m.id == starter.id:
            continue
        raw.append(m)

    result = []
    for m in raw:
        if m.author.bot:
            continue
        content = _format_content(m)
        if not content:
            continue
        result.append(
            ThreadMessage(
                index=len(result) + 1,
                author=m.author.display_name,
                created_at=m.created_at,
                content=content,
                jump_url=m.jump_url,
            )
        )
    return result


@bot.tree.command(name="정책정리", description="이 스레드의 논의를 정책 결정으로 정리해요")
async def summarize_command(interaction: discord.Interaction):
    thread = interaction.channel
    if not isinstance(thread, discord.Thread):
        await interaction.response.send_message("스레드 안에서만 쓸 수 있어요.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    messages = await collect_thread_messages(thread)
    if not messages:
        await interaction.followup.send("정리할 메시지가 없어요.")
        return

    try:
        decisions = await summarize_thread(thread.name, messages)
    except SummaryError as e:
        log.warning("요약 실패: %s", e)
        await interaction.followup.send(f"요약에 실패했어요: {e}")
        return

    if not decisions:
        await interaction.followup.send("이 스레드에서 정책 결정이나 논의를 찾지 못했어요.")
        return

    source = {
        "thread_id": str(thread.id),
        "thread_name": thread.name,
        "thread_url": thread.jump_url,
        "message_urls_by_index": {m.index: m.jump_url for m in messages},
    }
    embeds = build_embeds(decisions, messages)
    shown = len(embeds)
    note = f" (화면에는 {shown}건만 표시, 승인 시 {len(decisions)}건 모두 저장)" if shown < len(decisions) else ""

    view = ApprovalView(owner_id=interaction.user.id, decisions=decisions, source=source)
    view.message = await interaction.followup.send(
        content=f"📋 메시지 {len(messages)}개에서 결정 {len(decisions)}건을 정리했어요{note}. 확인 후 승인해 주세요.",
        embeds=embeds,
        view=view,
        wait=True,
    )


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.exception("커맨드 오류", exc_info=error)
    msg = "처리 중 오류가 났어요. 봇에 이 스레드를 읽을 권한이 있는지 확인해 주세요."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


if __name__ == "__main__":
    if not DISCORD_TOKEN or not ANTHROPIC_API_KEY:
        raise SystemExit(".env에 DISCORD_TOKEN, ANTHROPIC_API_KEY를 설정해 주세요.")
    bot.run(DISCORD_TOKEN, log_handler=None)
