"""`@봇` 멘션 자연어 질문 (기존 /정책질문을 대체).

입력 방식만 바뀌었고 검색·답변은 그대로 FastAPI(`/policies/answers`)를 쓴다.
멘션 질문은 읽기 전용이다. 정리(쓰기)는 /정책정리 명령어로만 한다.

언제 답하나
- 봇 사용자 멘션, 또는 봇 역할 멘션 (자동완성에서 같은 이름의 역할을 고르는 경우가 많다)
- 봇 답변에 단 답장 (핑을 꺼도 대화를 이어갈 수 있게)

맥락 (`context`) — 질문의 지시어를 풀기 위한 것. 근거로 쓰지 않는다
- 답장한 메시지: 봇 답변이면 그때의 Q/A, 다른 사람 메시지면 그 내용
- 스레드 안이면 최근 메시지 N개 (일반 채널은 주제가 섞여 있어 넣지 않는다)
"""
import logging
import re

import discord

from policy_search import call_api, send_chunked

log = logging.getLogger("policy-bot")

RECENT_MESSAGES = 10          # 스레드 맥락으로 넣을 최근 메시지 수
MESSAGE_CHARS = 400           # 맥락 메시지 1개당 최대 글자
CONTEXT_CHARS = 5000          # 맥락 전체 최대 글자 (API 제한 6000보다 작게)
ANSWER_PREFIX = "**Q. "       # 봇 답변의 첫 줄. 답장 대상이 봇 답변인지 알아볼 때도 쓴다

HELP_TEXT = ("질문을 같이 적어 주세요. 예)\n"
             "• @정책봇 오프라인으로 앱 켜면 어디로 진입하기로 했지?\n"
             "• (스레드에서) @정책봇 지금 얘기한 거 정책이랑 맞아?")


def is_addressed(message: discord.Message, bot_user: discord.abc.User) -> bool:
    if message.author.bot:
        return False
    if bot_user in message.mentions:
        return True
    role = message.guild.self_role if message.guild else None
    if role and role in message.role_mentions:
        return True
    # 지워진 메시지(DeletedReferencedMessage)는 author·content가 없다
    ref = message.reference.resolved if message.reference else None
    return getattr(ref, "author", None) == bot_user and getattr(ref, "content", "").startswith(ANSWER_PREFIX)


def extract_question(content: str, ids: list[int]) -> str:
    """멘션 토큰(<@id>, <@!id>, <@&role>)을 지우고 남은 질문."""
    for i in ids:
        content = re.sub(rf"<@[!&]?{i}>", " ", content)
    return " ".join(content.split())


def parse_bot_answer(content: str) -> tuple[str, str] | None:
    """봇 답변 메시지 → (질문, 답변). 형식: '**Q. 질문**\\n\\n답변…'"""
    if not content.startswith(ANSWER_PREFIX):
        return None
    head, _, rest = content.partition("\n")
    question = head[len(ANSWER_PREFIX):].removesuffix("**").strip()
    answer = rest.split("\n**근거 문서**")[0].strip()
    return question, answer


def _cut(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + "…"


def format_context(recent: list[tuple[str, str]], replied: tuple[str, str] | None = None,
                   reply_target: tuple[str, str] | None = None) -> str | None:
    """recent: 스레드 최근 메시지 (작성자, 내용) 오래된 순. 최근 것이 끝에 오게 만든다 (검색어에는 끝부분만 붙음)."""
    parts = []
    if recent:
        parts.append("[스레드 최근 메시지]\n" + "\n".join(f"{a}: {_cut(t, MESSAGE_CHARS)}" for a, t in recent if t.strip()))
    if replied:
        parts.append(f"[이전 질문] {replied[0]}\n[이전 답변] {_cut(replied[1], 1500)}")
    if reply_target:
        parts.append(f"[답장 대상] {reply_target[0]}: {_cut(reply_target[1], MESSAGE_CHARS * 2)}")
    if not parts:
        return None
    text = "\n\n".join(parts)
    return text[-CONTEXT_CHARS:]


async def _resolve_reference(message: discord.Message) -> discord.Message | None:
    if not message.reference or not message.reference.message_id:
        return None
    ref = message.reference.resolved
    if ref is not None and hasattr(ref, "content"):  # 지워진 메시지가 아니면 이미 받아온 것
        return ref
    try:
        return await message.channel.fetch_message(message.reference.message_id)
    except discord.HTTPException:
        return None


async def collect_context(message: discord.Message, bot_user: discord.abc.User) -> str | None:
    replied = reply_target = None
    ref = await _resolve_reference(message)
    if ref is not None:
        if ref.author == bot_user:
            replied = parse_bot_answer(ref.content)
        elif ref.content.strip():
            reply_target = (ref.author.display_name, ref.content)

    recent: list[tuple[str, str]] = []
    if isinstance(message.channel, discord.Thread):
        try:
            async for m in message.channel.history(limit=RECENT_MESSAGES, before=message):
                if m.author.bot or (ref is not None and m.id == ref.id):
                    continue  # 봇 답변은 replied로, 답장 대상은 reply_target으로 따로 넣는다
                recent.append((m.author.display_name, extract_question(m.content, [bot_user.id])))
        except discord.HTTPException:
            log.warning("스레드 최근 메시지를 못 읽음 (channel=%s)", message.channel.id)
        recent.reverse()
    return format_context(recent, replied, reply_target)


def format_answer(question: str, data: dict) -> str:
    sources = "\n".join(
        f"[{i}] [{s['title']}]({s['url']})" if s.get("url") else f"[{i}] {s['title']}"
        for i, s in enumerate(data["sources"], 1)
    )
    u = data.get("usage")
    q = data.get("quota") or {}
    quota = f" · 오늘 {q['used_today']}/{q['daily_limit']}회" if q.get("daily_limit") else ""
    footer = (f"\n-# 토큰 in {u['input_tokens']:,} / out {u['output_tokens']:,} · ${u['cost_usd']:.4f}"
              f" · {u['latency_ms'] / 1000:.1f}s{quota}" if u else "")
    sources_part = f"\n\n**근거 문서**\n{sources}" if sources else ""
    return f"{ANSWER_PREFIX}{question}**\n\n{data['answer']}{sources_part}{footer}"


async def handle_mention(message: discord.Message, bot_user: discord.abc.User) -> None:
    ids = [bot_user.id] + ([message.guild.self_role.id] if message.guild and message.guild.self_role else [])
    question = extract_question(message.content, ids)
    if len(question) < 2:
        await message.reply(HELP_TEXT, mention_author=False)
        return

    async with message.channel.typing():  # 답변까지 수 초 걸리니 '입력 중' 표시
        context = await collect_context(message, bot_user)
        payload = {"question": question[:500], **({"context": context} if context else {})}
        # 사용자별 일일 제한을 위해 Discord 사용자 ID를 함께 보낸다 (헤더는 ASCII만 가능해서 이름 대신 ID)
        code, data = await call_api("POST", "/policies/answers", json=payload,
                                    headers={"X-User-Id": str(message.author.id)})
    if code == 429:
        await message.reply(f"⏸️ {data.get('detail')}", mention_author=False)
        return
    if code != 200:
        await message.reply(f"답변 실패 ({code}): {data.get('detail')}", mention_author=False)
        return
    await send_chunked(message, format_answer(question, data))
