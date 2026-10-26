"""요약 결과 임베드 + 승인 화면 (결정 선택 · 수정 · 제외 · 승인/취소).

요약이 틀렸을 때 다시 /정책정리 하지 않고 저장 전에 고칠 수 있게 한다.
- 결정 선택 → [수정]: 주제·상태·결정·근거·적용범위를 모달로 편집
- [제외]: 그 결정만 저장에서 뺀다 (다시 누르면 포함)
사람이 고친 필드는 record의 edited_fields에 남는다 → 요약 평가셋(Q-6) 만들 때 정답 라벨로 쓸 수 있다.
"""
import logging

import discord

import spec_reflect
from config import APPROVAL_TIMEOUT, STATUSES
from policy_search import schedule_reindex
from storage import save_decisions
from summarizer import ThreadMessage

log = logging.getLogger("policy-bot")

STATUS_COLOR = {"제안": 0x95A5A6, "논의중": 0xF1C40F, "최종결정": 0x2ECC71}
STATUS_EMOJI = {"제안": "💡", "논의중": "💬", "최종결정": "✅"}
EXCLUDED_COLOR = 0x4F545C
MAX_EDITABLE = 25  # 선택 메뉴 옵션 최대 개수


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_embeds(decisions: list[dict], messages: list[ThreadMessage],
                 excluded: set[int] | frozenset = frozenset()) -> list[discord.Embed]:
    url_by_index = {m.index: m.jump_url for m in messages}
    embeds = []
    for i, d in enumerate(decisions[:10], start=1):  # 디스코드: 메시지당 임베드 최대 10개
        if i - 1 in excluded:
            # 제외한 결정은 제목만 남겨 자리를 지킨다 (다시 포함할 수 있게)
            e = discord.Embed(title=_cut(f"🚫 (제외) [{d['area']}] {d['topic']}", 256), color=EXCLUDED_COLOR)
            e.set_footer(text=f"{i}/{len(decisions)} · 저장하지 않음")
            embeds.append(e)
            continue
        edited = " · ✏️ 수정됨" if d.get("edited_fields") else ""
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
        e.set_footer(text=f"{i}/{len(decisions)}{edited}")
        embeds.append(e)

    # 전체 임베드 6000자 제한 대응: 넘으면 뒤에서부터 줄임
    while embeds and sum(len(e) for e in embeds) > 6000:
        embeds.pop()
    return embeds


def apply_edit(d: dict, *, topic: str, status: str, decision: str, rationale: str, scope: str) -> list[str]:
    """모달 입력을 결정에 반영하고, 바뀐 필드 이름을 돌려준다 (edited_fields에 누적)."""
    new = {
        "topic": topic.strip() or d["topic"],
        "status": status if status in STATUSES else d["status"],
        "decision": decision.strip(),
        "rationale": rationale.strip() or None,
        "scope": scope.strip() or None,
    }
    changed = [k for k, v in new.items() if d.get(k) != v]
    d.update(new)
    if changed:
        d["edited_fields"] = sorted(set(d.get("edited_fields", [])) | set(changed))
    return changed


class EditModal(discord.ui.Modal):
    def __init__(self, view: "ApprovalView", index: int):
        super().__init__(title=f"결정 {index + 1} 수정", timeout=APPROVAL_TIMEOUT)
        self.view_ref = view
        self.index = index
        d = view.decisions[index]
        self.topic = discord.ui.TextInput(default=d["topic"], max_length=200)
        self.status = discord.ui.Select(
            options=[discord.SelectOption(label=s, value=s, default=s == d["status"]) for s in STATUSES]
        )
        self.decision = discord.ui.TextInput(style=discord.TextStyle.paragraph, default=d["decision"][:4000],
                                             max_length=4000)
        self.rationale = discord.ui.TextInput(style=discord.TextStyle.paragraph, default=(d["rationale"] or "")[:1000],
                                              max_length=1000, required=False)
        self.scope = discord.ui.TextInput(default=(d["scope"] or "")[:300], max_length=300, required=False)
        # 모달은 최대 5칸. 외부참조·확인필요는 고칠 일이 적어 제외 (틀리면 [제외] 후 재정리)
        for text, component in (("주제", self.topic), ("상태", self.status), ("결정", self.decision),
                                ("근거 (비우면 없음)", self.rationale), ("적용범위 (비우면 없음)", self.scope)):
            self.add_item(discord.ui.Label(text=text, component=component))

    async def on_submit(self, interaction: discord.Interaction):
        view = self.view_ref
        changed = apply_edit(
            view.decisions[self.index],
            topic=self.topic.value, status=self.status.values[0] if self.status.values else "",
            decision=self.decision.value, rationale=self.rationale.value, scope=self.scope.value,
        )
        log.info("결정 %d 수정: %s", self.index + 1, changed or "변경 없음")
        await interaction.response.edit_message(**view.render())


class ApprovalView(discord.ui.View):
    def __init__(self, *, owner_id: int, decisions: list[dict], source: dict, messages: list[ThreadMessage]):
        super().__init__(timeout=APPROVAL_TIMEOUT)
        self.owner_id = owner_id
        self.decisions = decisions
        self.source = source
        self.messages = messages
        self.excluded: set[int] = set()
        self.selected = 0
        self.message: discord.Message | None = None
        self._refresh_controls()

    # --- 화면 ---
    def render(self) -> dict:
        """메시지 수정에 넘길 content·embeds·view."""
        embeds = build_embeds(self.decisions, self.messages, self.excluded)
        kept = len(self.decisions) - len(self.excluded)
        parts = [f"📋 메시지 {len(self.messages)}개에서 결정 {len(self.decisions)}건을 정리했어요"]
        if self.excluded:
            parts.append(f" (제외 {len(self.excluded)}건 → {kept}건 저장)")
        if len(embeds) < len(self.decisions):
            parts.append(f" (화면에는 {len(embeds)}건만 표시, 승인 시 표시 안 된 것도 저장)")
        parts.append(". 틀린 곳은 결정을 골라 수정·제외한 뒤 승인해 주세요.")
        self._refresh_controls()
        return {"content": "".join(parts), "embeds": embeds, "view": self}

    def _refresh_controls(self):
        self.pick.options = [
            discord.SelectOption(
                label=_cut(f"{i + 1}. {d['topic']}", 100),
                description=("🚫 제외됨" if i in self.excluded else d["status"]),
                value=str(i),
                default=i == self.selected,
            )
            for i, d in enumerate(self.decisions[:MAX_EDITABLE])
        ]
        excluded = self.selected in self.excluded
        self.toggle_exclude.label = "↩️ 다시 포함" if excluded else "🚫 제외"
        self.edit.disabled = excluded

    def kept_decisions(self) -> list[dict]:
        return [d for i, d in enumerate(self.decisions) if i not in self.excluded]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("정리를 요청한 사람만 수정·승인할 수 있어요.", ephemeral=True)
            return False
        return True

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    # --- 컨트롤 ---
    @discord.ui.select(placeholder="수정·제외할 결정 고르기", row=0)
    async def pick(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected = int(select.values[0])
        await interaction.response.edit_message(**self.render())

    @discord.ui.button(label="✏️ 수정", style=discord.ButtonStyle.primary, row=1)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditModal(self, self.selected))

    @discord.ui.button(label="🚫 제외", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_exclude(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.excluded ^= {self.selected}
        await interaction.response.edit_message(**self.render())

    @discord.ui.button(label="승인하고 저장", style=discord.ButtonStyle.success, row=2)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        to_save = self.kept_decisions()
        if not to_save:
            await interaction.response.send_message("저장할 결정이 없어요. 모두 제외했다면 [취소]를 눌러 주세요.",
                                                    ephemeral=True)
            return
        # 중복 클릭 방지: 먼저 버튼을 잠그고 응답을 미룸 (Notion 저장이 3초를 넘길 수 있음)
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(content="💾 저장 중…", view=self)
        try:
            results = await save_decisions(to_save, source=self.source, approved_by=interaction.user.display_name)
        except Exception:
            log.exception("저장 실패")
            await interaction.edit_original_response(content="❌ 로컬 저장 중 오류가 났어요. 로그를 확인해 주세요.")
            return

        edited = sum(1 for d in to_save if d.get("edited_fields"))
        summary = f"결정 {len(results)}건 저장됨"
        if edited or self.excluded:
            summary += f" (수정 {edited}건 · 제외 {len(self.excluded)}건)"
        lines = [f"✅ {interaction.user.mention} 님이 승인 — {summary}"]
        for r in results:
            if r.notion_url:
                lines.append(f"• [{r.record['topic']}]({r.notion_url})")
            elif r.notion_error:
                lines.append(f"• {r.record['topic']} — ⚠️ Notion 저장 실패 (로컬에는 저장됨)")
        if not any(r.notion_url or r.notion_error for r in results):
            lines.append("(Notion 미설정 — 로컬 JSON에만 저장)")
        await interaction.edit_original_response(content="\n".join(lines)[:2000])
        schedule_reindex()  # 새 결정을 검색 인덱스에 반영 (백그라운드)

        # 원천 정책서 반영 제안 (정책서가 설정된 영역의 최종결정만). 원문은 고치지 않고 댓글·콜아웃만 넣는다
        if spec_reflect.NOTION_SPEC_PAGES:
            await interaction.edit_original_response(content="\n".join(lines + ["📚 기준 정책서 반영 제안 중…"])[:2000])
            outcomes = await spec_reflect.reflect_records([(r.record, r.notion_url) for r in results],
                                                          user=str(interaction.user.id))
            await interaction.edit_original_response(
                content="\n".join(lines + spec_reflect.format_outcomes(outcomes))[:2000])

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary, row=2)
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
