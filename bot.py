"""정책 관리 디스코드 봇 — MVP.

/정책정리 : 현재 스레드의 논의를 정책 결정으로 요약 → 승인 시 저장
/정책검색 : 저장된 결정 검색 (LLM 답변 없음)
@봇 질문  : 저장된 결정을 근거로 자연어 질문에 답변 (mention.py, 읽기 전용)
"""
import asyncio
import logging
import socket

import discord
from discord import app_commands

from app import ops
from app.config import get_settings
from app.logging_setup import new_request_id, setup_logging
from config import DISCORD_TOKEN, GUILD_ID, MAX_MESSAGES
from app.services.storage import resummary_context, thread_source
from app.services.summarizer import SummaryError, ThreadMessage, summarize_thread
from mention import handle_mention, is_addressed
from policy_search import register_policy_commands
from thread_reader import to_thread_messages
from views import ApprovalView

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
        # on_ready는 재연결 때마다 다시 불린다 → 시작 알림은 프로세스당 한 번
        if not getattr(self, "_announced", False):
            self._announced = True
            await asyncio.to_thread(ops.notify, get_settings(), startup_message(socket.gethostname()))

    async def on_message(self, message: discord.Message):
        if self.user and is_addressed(message, self.user):
            new_request_id("m-")  # 멘션 질문 하나 = 요청 하나. API 호출까지 같은 ID로 로그를 묶는다
            log.info("멘션 질문 (message=%s, channel=%s)", message.id, message.channel.id)
            try:
                await handle_mention(message, self.user)
            except Exception:  # 한 질문의 실패가 봇 전체를 멈추지 않게
                log.exception("멘션 질문 처리 실패 (message=%s)", message.id)
                await message.reply("처리 중 오류가 났어요. 잠시 후 다시 물어봐 주세요.", mention_author=False)


def startup_message(host: str) -> str:
    """운영 채널에 남기는 시작 알림. 호스트 이름을 넣어 같은 토큰으로 두 곳에서 띄운 걸 바로 알아챈다."""
    return f"🟢 정책 봇 시작 — 호스트 `{host}`. 다른 호스트의 시작 알림이 같이 보이면 봇이 두 개 떠 있는 거예요."


bot = PolicyBot()
register_policy_commands(bot.tree)  # /정책검색 (FastAPI 서버 필요)


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
    return await to_thread_messages(raw)


@bot.tree.command(name="정책정리", description="이 스레드의 논의를 정책 결정으로 정리해요")
async def summarize_command(interaction: discord.Interaction):
    thread = interaction.channel
    new_request_id("s-")  # 정리 → 승인 → 저장 → 정책서 반영까지 같은 ID (승인 화면이 이어받는다)
    if not isinstance(thread, discord.Thread):
        await interaction.response.send_message("스레드 안에서만 쓸 수 있어요.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    messages = await collect_thread_messages(thread)
    if not messages:
        await interaction.followup.send("정리할 메시지가 없어요.")
        return

    # 재정리: 이 스레드의 현재 기록과 비교해 바뀐 것만 저장한다 (Q-7)
    ctx = resummary_context(str(thread.id), messages)
    if ctx.nothing_new:
        await interaction.followup.send(f"마지막 정리 이후 새 메시지가 없어요. 기존 기록 {len(ctx.previous)}건을 그대로 둘게요.")
        return

    try:
        decisions = await summarize_thread(thread.name, messages, ctx.previous or None, ctx.since)
    except SummaryError as e:
        log.warning("요약 실패: %s", e)
        await interaction.followup.send(f"요약에 실패했어요: {e}")
        return

    changed = [d for d in decisions if d["relation"] != "unchanged"]
    if not changed:
        msg = (f"이전 정리와 달라진 결정이 없어요 (변동 없음 {len(decisions)}건). 기존 기록을 그대로 둘게요."
               if decisions else "이 스레드에서 정책 결정이나 논의를 찾지 못했어요.")
        await interaction.followup.send(msg)
        return

    source = thread_source(str(thread.id), thread.name, thread.jump_url, messages)
    log.info("정리 결과 승인 대기 (thread=%s, 결정 %d건, 변동 없음 %d건)", thread.id, len(changed),
             len(decisions) - len(changed))
    view = ApprovalView(owner_id=interaction.user.id, decisions=changed, source=source, messages=messages,
                        previous=ctx.previous, unchanged=len(decisions) - len(changed))
    view.message = await interaction.followup.send(**view.render(), wait=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.exception("커맨드 오류", exc_info=error)
    msg = "처리 중 오류가 났어요. 봇에 이 스레드를 읽을 권한이 있는지 확인해 주세요."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


if __name__ == "__main__":
    setup_logging("bot")
    # 설정 문제는 실행 중 처음 쓰는 순간이 아니라 시작할 때 한 번에 알린다
    _settings = get_settings()
    for w in _settings.warnings():
        log.warning("설정 확인: %s", w)
    if problems := _settings.problems("bot"):
        raise SystemExit("설정을 확인해 주세요 (.env 또는 환경변수):\n- " + "\n- ".join(problems))
    bot.run(DISCORD_TOKEN, log_handler=None)
