"""요약 결과 임베드 + 승인/취소 버튼."""
import logging

import discord

from config import APPROVAL_TIMEOUT
from policy_search import schedule_reindex
from storage import save_decisions
from summarizer import ThreadMessage

log = logging.getLogger("policy-bot")

STATUS_COLOR = {"제안": 0x95A5A6, "논의중": 0xF1C40F, "최종결정": 0x2ECC71}
STATUS_EMOJI = {"제안": "💡", "논의중": "💬", "최종결정": "✅"}


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_embeds(decisions: list[dict], messages: list[ThreadMessage]) -> list[discord.Embed]:
    url_by_index = {m.index: m.jump_url for m in messages}
    embeds = []
    for i, d in enumerate(decisions[:10], start=1):  # 디스코드: 메시지당 임베드 최대 10개
        e = discord.Embed(
            title=_cut(f"{STATUS_EMOJI[d['status']]} [{d['area']}] {d['topic']}", 256),
            description=_cut(d["decision"] or "(결정 내용 없음)", 2000),
            color=STATUS_COLOR[d["status"]],
        )
        e.add_field(name="상태", value=d["status"], inline=True)
        e.add_field(name="결정자", value=d["decided_by"] or "미확인", inline=True)
        e.add_field(name="적용 범위", value=_cut(d["scope"] or "미확인", 1024), inline=True)
        if d["rationale"]:
            e.add_field(name="근거", value=_cut(d["rationale"], 1024), inline=False)
        if d["external_refs"]:
            e.add_field(name="외부 참조", value=_cut("\n".join(f"• {r}" for r in d["external_refs"]), 1024), inline=False)
        if d["open_questions"]:
            qs = "\n".join(
                f"• {q['question']}" + (f" → **{q['ask_to']}**" if q["ask_to"] else "") for q in d["open_questions"]
            )
            e.add_field(name="❓ 확인 필요", value=_cut(qs, 1024), inline=False)
        if d["source_messages"]:
            links = " ".join(f"[#{n}]({url_by_index[n]})" for n in d["source_messages"] if n in url_by_index)
            if links:
                e.add_field(name="근거 메시지", value=_cut(links, 1024), inline=False)
        e.set_footer(text=f"{i}/{len(decisions)}")
        embeds.append(e)

    # 전체 임베드 6000자 제한 대응: 넘으면 뒤에서부터 줄임
    while embeds and sum(len(e) for e in embeds) > 6000:
        embeds.pop()
    return embeds


class ApprovalView(discord.ui.View):
    def __init__(self, *, owner_id: int, decisions: list[dict], source: dict):
        super().__init__(timeout=APPROVAL_TIMEOUT)
        self.owner_id = owner_id
        self.decisions = decisions
        self.source = source
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("정리를 요청한 사람만 승인/취소할 수 있어요.", ephemeral=True)
            return False
        return True

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="승인하고 저장", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 중복 클릭 방지: 먼저 버튼을 잠그고 응답을 미룸 (Notion 저장이 3초를 넘길 수 있음)
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(content="💾 저장 중…", view=self)
        try:
            results = await save_decisions(
                self.decisions, source=self.source, approved_by=interaction.user.display_name
            )
        except Exception:
            log.exception("저장 실패")
            await interaction.edit_original_response(content="❌ 로컬 저장 중 오류가 났어요. 로그를 확인해 주세요.")
            return

        lines = [f"✅ {interaction.user.mention} 님이 승인 — 결정 {len(results)}건 저장됨"]
        for r in results:
            if r.notion_url:
                lines.append(f"• [{r.record['topic']}]({r.notion_url})")
            elif r.notion_error:
                lines.append(f"• {r.record['topic']} — ⚠️ Notion 저장 실패 (로컬에는 저장됨)")
        if not any(r.notion_url or r.notion_error for r in results):
            lines.append("(Notion 미설정 — 로컬 JSON에만 저장)")
        await interaction.edit_original_response(content="\n".join(lines)[:2000])
        schedule_reindex()  # 새 결정을 검색 인덱스에 반영 (백그라운드)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(
            content="🚫 취소됨 — 저장하지 않았어요. 내용을 보완한 뒤 다시 `/정책정리` 해주세요.", view=self
        )

    async def on_timeout(self):
        self._disable_all()
        if self.message:
            try:
                await self.message.edit(content="⌛ 승인 시간이 지나 저장하지 않았어요. 다시 `/정책정리` 해주세요.", view=self)
            except discord.HTTPException:
                pass
