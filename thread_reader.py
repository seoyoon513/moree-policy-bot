"""디스코드 메시지 → ThreadMessage 변환. 봇(/정책정리)과 일괄 정리 스크립트가 같은 규칙을 쓰도록 한 곳에 둔다."""
import discord

from config import AGREE_EMOJIS
from summarizer import ThreadMessage


def format_content(m: discord.Message) -> str:
    parts = [m.content.strip()] if m.content.strip() else []
    parts += [f"[첨부: {a.filename}]" for a in m.attachments]
    parts += [f"[링크: {e.title or e.url}]" for e in m.embeds if e.title or e.url]
    return " ".join(parts).replace("\n", " / ")


async def agreed_users(m: discord.Message) -> list[str]:
    """동의 리액션을 남긴 사람 이름 (작성자·봇 제외). 리액션 종류마다 REST 호출 1회라 동의 이모지만 조회한다."""
    names: list[str] = []
    for r in m.reactions:
        if str(r.emoji) not in AGREE_EMOJIS:
            continue
        try:
            async for u in r.users():
                if not u.bot and u.id != m.author.id and u.display_name not in names:
                    names.append(u.display_name)
        except discord.HTTPException:
            continue  # 리액션을 못 읽어도 요약은 진행
    return names


async def to_thread_messages(raw: list[discord.Message]) -> list[ThreadMessage]:
    """봇 메시지·빈 메시지를 빼고 1부터 번호를 매긴다."""
    result: list[ThreadMessage] = []
    for m in raw:
        if m.author.bot:
            continue
        content = format_content(m)
        if not content:
            continue
        result.append(
            ThreadMessage(
                index=len(result) + 1,
                author=m.author.display_name,
                created_at=m.created_at,
                content=content,
                jump_url=m.jump_url,
                agreed_by=await agreed_users(m),
            )
        )
    return result
