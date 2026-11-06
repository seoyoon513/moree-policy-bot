"""정책 관리 디스코드 봇 — 디스코드 UI만 맡는다 (A-6).

/정책정리 : 스레드 메시지를 모아 API(POST /summaries)로 정리 → 승인 화면 → 승인 시 API가 저장
/정책검색 : 저장된 결정 검색 (LLM 답변 없음)
@봇 질문  : 저장된 결정을 근거로 자연어 질문에 답변 (mention.py, 읽기 전용)

LLM·노션·원장은 API만 다룬다. 봇은 메시지 수집·화면·HTTP 호출만 한다.
"""
import asyncio
import logging
import socket

import discord
from discord import app_commands

from shared import alerts
from shared.config import get_settings
from shared.logging_setup import new_request_id, setup_logging
from shared.domain import ThreadMessage
from bot.mention import handle_mention, is_addressed
from bot.policy_search import call_api, register_policy_commands
from bot.thread_reader import to_thread_messages
from bot.views import ApprovalView

_s = get_settings()
DISCORD_TOKEN = _s.discord_token
GUILD_ID = _s.guild_id
MAX_MESSAGES = _s.max_messages

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
            await asyncio.to_thread(alerts.notify, get_settings(), startup_message(socket.gethostname()))

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


client = PolicyBot()
register_policy_commands(client.tree)  # /정책검색 (FastAPI 서버 필요)


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


# 긴 스레드는 요약 출력이 8천 토큰까지 가서 기본 타임아웃(90초)보다 길게
SUMMARY_API_TIMEOUT = 300


def summary_payload(thread, messages: list[ThreadMessage], user_id: int) -> dict:
    """POST /summaries 본문 (app/api/schemas.SummaryCreate)."""
    return {
        "thread_id": str(thread.id), "thread_name": thread.name, "thread_url": thread.jump_url,
        "user_id": str(user_id),
        "messages": [{"index": m.index, "author": m.author, "created_at": m.created_at.isoformat(), "content": m.content,
                      "jump_url": m.jump_url, "agreed_by": m.agreed_by} for m in messages],
    }


@client.tree.command(name="정책정리", description="이 스레드의 논의를 정책 결정으로 정리해요")
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

    # 재정리 판정(새 메시지가 있나·달라진 결정만)과 요약은 API가 한다. 결과가 '승인 대기'면 화면을 띄운다
    code, data = await call_api("POST", "/summaries", timeout=SUMMARY_API_TIMEOUT,
                                json=summary_payload(thread, messages, interaction.user.id))
    if code not in (200, 201):
        log.warning("정리 실패 (%s): %s", code, data.get("detail"))
        await interaction.followup.send(f"❌ {data.get('detail') or f'정리하지 못했어요 ({code})'}")
        return
    if data["status"] != "ready":
        await interaction.followup.send(data.get("message") or "새로 저장할 결정이 없어요.")
        return
    view = ApprovalView(owner_id=interaction.user.id, summary_id=data["summary_id"], decisions=data["decisions"],
                        messages=messages, previous=data["previous"], unchanged=data["unchanged"])
    view.message = await interaction.followup.send(**view.render(), wait=True)


@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.exception("커맨드 오류", exc_info=error)
    msg = "처리 중 오류가 났어요. 봇에 이 스레드를 읽을 권한이 있는지 확인해 주세요."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


def run() -> None:
    """진입점 (python -m bot)."""
    setup_logging("bot")
    # 설정 문제는 실행 중 처음 쓰는 순간이 아니라 시작할 때 한 번에 알린다
    _settings = get_settings()
    for w in _settings.warnings("bot"):
        log.warning("설정 확인: %s", w)
    if problems := _settings.problems("bot"):
        raise SystemExit("설정을 확인해 주세요 (.env 또는 환경변수):\n- " + "\n- ".join(problems))
    client.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    run()
