"""요약 결과 임베드 + 승인 화면 (결정 선택 · 수정 · 제외 · 승인/취소).

요약이 틀렸을 때 다시 /정책정리 하지 않고 저장 전에 고칠 수 있게 한다.
- 결정 선택 → [수정]: 주제·상태·결정·근거·적용범위를 모달로 편집
- [제외]: 그 결정만 저장에서 뺀다 (다시 누르면 포함)
사람이 고친 필드는 record의 edited_fields에 남는다 → 요약 평가셋(Q-6) 만들 때 정답 라벨로 쓸 수 있다.
- 재정리(Q-7): 변경은 "이전 → 이번"을 보여 주고, [🔗 연결 해제]로 덮어쓰지 않고 새 기록으로 저장할 수 있다
"""
import logging

import discord

from app.logging_setup import current_request_id, set_request_id
from config import APPROVAL_TIMEOUT, STATUSES
from policy_search import call_api
from app.domain import ThreadMessage

log = logging.getLogger("policy-bot")

STATUS_COLOR = {"제안": 0x95A5A6, "논의중": 0xF1C40F, "최종결정": 0x2ECC71}
STATUS_EMOJI = {"제안": "💡", "논의중": "💬", "최종결정": "✅"}
EXCLUDED_COLOR = 0x4F545C
MAX_EDITABLE = 25  # 선택 메뉴 옵션 최대 개수


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def relation_fields(d: dict, previous: dict[str, dict]) -> list[tuple[str, str]]:
    """재정리 결정의 '이전 → 이번' 표시. 덮어쓸 내용을 승인 전에 보여 주는 게 잘못 덮어쓰기를 막는 장치다."""
    old = previous.get(d.get("prior_id") or "")
    if d.get("relation") == "update" and old:
        return [(f"🔁 변경 — 이전 기록 {old['id']}을 덮어씀 (이전: {old['status']})",
                 _cut(old["decision"] or "-", 1024))]
    if d.get("relation") == "reopen" and old:
        return [(f"↩️ 재논의 — 이전 최종결정(기록 {old['id']})은 그대로 두고 새 기록으로 저장",
                 _cut(old["decision"] or "-", 1024))]
    if d.get("unlinked_from"):
        return [("🔗 연결 해제됨", f"기록 {d['unlinked_from'][1]}을 덮어쓰지 않고 새 기록으로 저장")]
    return []


def toggle_link(d: dict) -> None:
    """[🔗 연결 해제]: 이전 기록을 덮어쓰지 않고 새 기록으로 저장. 다시 누르면 원래 연결로."""
    if d.get("unlinked_from"):
        d["relation"], d["prior_id"] = d.pop("unlinked_from")
    elif d.get("relation") in ("update", "reopen"):
        d["unlinked_from"] = [d["relation"], d["prior_id"]]
        d["relation"], d["prior_id"] = "new", None


def set_relation_by_status(d: dict, previous: dict[str, dict]) -> None:
    """수정 모달에서 상태를 바꾸면 변경/재논의가 달라질 수 있다 (확정을 논의중으로 덮지 않는다)."""
    old = previous.get(d.get("prior_id") or "")
    if d.get("relation") in ("update", "reopen") and old:
        d["relation"] = "reopen" if old["status"] == "최종결정" and d["status"] != "최종결정" else "update"


def build_embeds(decisions: list[dict], messages: list[ThreadMessage],
                 excluded: set[int] | frozenset = frozenset(),
                 previous: dict[str, dict] | None = None) -> list[discord.Embed]:
    url_by_index = {m.index: m.jump_url for m in messages}
    previous = previous or {}
    embeds = []
    for i, d in enumerate(decisions[:10], start=1):  # 디스코드: 메시지당 임베드 최대 10개
        if i - 1 in excluded:
            # 제외한 결정은 제목만 남겨 자리를 지킨다 (다시 포함할 수 있게)
            e = discord.Embed(title=_cut(f"🚫 (제외) [{d['area']}] {d['topic']}", 256), color=EXCLUDED_COLOR)
            e.set_footer(text=f"{i}/{len(decisions)} · 저장하지 않음")
            embeds.append(e)
            continue
        edited = " · ✏️ 수정됨" if d.get("edited_fields") else ""
        mark = {"update": "🔁 ", "reopen": "↩️ "}.get(d.get("relation"), "")
        e = discord.Embed(
            title=_cut(f"{mark}{STATUS_EMOJI[d['status']]} [{d['area']}] {d['topic']}", 256),
            description=_cut(d["decision"] or "(결정 내용 없음)", 2000),
            color=STATUS_COLOR[d["status"]],
        )
        for name, value in relation_fields(d, previous):
            e.add_field(name=_cut(name, 256), value=value, inline=False)
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
        set_relation_by_status(view.decisions[self.index], view.previous)
        log.info("결정 %d 수정: %s", self.index + 1, changed or "변경 없음")
        await interaction.response.edit_message(**view.render())


# 저장 + 정책서 매칭(LLM)까지 한 요청이라 기본 타임아웃(90초)보다 길게
APPROVAL_API_TIMEOUT = 300


def approval_lines(mention: str, result: dict, edited: int, excluded: int) -> str:
    """API 승인 결과(ApprovalResult) → 디스코드 메시지."""
    saved = result.get("saved") or []
    summary = f"결정 {len(saved)}건 저장됨"
    if edited or excluded:
        summary += f" (수정 {edited}건 · 제외 {excluded}건)"
    lines = [f"✅ {mention} 님이 승인 — {summary}"]
    for r in saved:
        tag = f"🔁 변경 v{r['version']} · " if r.get("updated") else ("↩️ 재논의 · " if r.get("reopens") else "")
        if r.get("notion_url"):
            lines.append(f"• {tag}[{r['topic']}]({r['notion_url']})")
        elif r.get("notion_pending") or r.get("notion_error"):
            # 원장에는 저장됐고 후속 작업 장부에 남아 다시 시도된다 (A-5)
            lines.append(f"• {tag}{r['topic']} — ⏳ 노션 저장 대기 중, 다시 시도할게요 (원장에는 저장됨)")
    if saved and not any(r.get("notion_url") or r.get("notion_error") for r in saved):
        lines.append("(Notion 미설정 — 로컬 JSON에만 저장)")
    if result.get("replayed"):
        lines.append("(이미 승인된 정리라 다시 저장하지 않았어요)")
    return "\n".join(lines + list(result.get("reflect_lines") or []))[:2000]


def approval_error_text(code: int, data: dict) -> str:
    if code == 404:
        return "⌛ 정리 결과가 만료됐어요. 다시 `/정책정리` 해 주세요."
    if code == 0:
        return f"❌ {data.get('detail')}"  # API 서버에 연결 못 함
    return f"❌ 저장하지 못했어요 ({code}): {data.get('detail')}"


class ApprovalView(discord.ui.View):
    def __init__(self, *, owner_id: int, summary_id: str, decisions: list[dict], messages: list[ThreadMessage],
                 previous: list[dict] | None = None, unchanged: int = 0):
        """summary_id: API(POST /summaries)가 보관 중인 정리 결과. 승인할 때 이 ID + 고친 결정만 보낸다.
        previous: 재정리일 때 이 스레드의 현재 기록. unchanged: 변동 없음이라 화면에서 뺀 결정 수.
        이 화면은 UI 상태(선택·수정·제외)만 들고, 저장에 필요한 원본은 API가 가진다 (A-6)."""
        super().__init__(timeout=APPROVAL_TIMEOUT)
        self.owner_id = owner_id
        self.decisions = decisions
        self.summary_id = summary_id
        self.messages = messages
        self.previous = {r["id"]: r for r in previous or []}
        self.unchanged = unchanged
        self.excluded: set[int] = set()
        self.selected = 0
        self.message: discord.Message | None = None
        # 버튼 콜백은 디스코드 이벤트마다 새 작업에서 돈다 → /정책정리 때의 ID를 들고 있다가 다시 설정
        self.request_id = current_request_id()
        self._refresh_controls()

    # --- 화면 ---
    def render(self) -> dict:
        """메시지 수정에 넘길 content·embeds·view."""
        embeds = build_embeds(self.decisions, self.messages, self.excluded, self.previous)
        kept = len(self.decisions) - len(self.excluded)
        parts = [f"📋 메시지 {len(self.messages)}개에서 결정 {len(self.decisions)}건을 정리했어요"]
        if self.previous:
            updates = sum(1 for d in self.decisions if d.get("relation") == "update")
            parts.append(f" (재정리: 변경 {updates}건 · 변동 없음 {self.unchanged}건은 저장 안 함)")
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
        d = self.decisions[self.selected] if self.decisions else {}
        self.toggle_link.label = "🔗 다시 연결" if d.get("unlinked_from") else "🔗 연결 해제"
        self.toggle_link.disabled = excluded or not (d.get("relation") in ("update", "reopen") or d.get("unlinked_from"))
        if not self.previous:  # 처음 정리하는 스레드에는 연결이 없다
            self.remove_item(self.toggle_link)

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

    @discord.ui.button(label="🔗 연결 해제", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_link(self, interaction: discord.Interaction, button: discord.ui.Button):
        toggle_link(self.decisions[self.selected])
        set_relation_by_status(self.decisions[self.selected], self.previous)
        await interaction.response.edit_message(**self.render())

    @discord.ui.button(label="승인하고 저장", style=discord.ButtonStyle.success, row=2)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        set_request_id(self.request_id)
        to_save = self.kept_decisions()
        if not to_save:
            await interaction.response.send_message("저장할 결정이 없어요. 모두 제외했다면 [취소]를 눌러 주세요.",
                                                    ephemeral=True)
            return
        # 중복 클릭 방지: 먼저 버튼을 잠그고 응답을 미룸 (Notion 저장이 3초를 넘길 수 있음)
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(content="💾 저장 중… (정책서 반영 제안까지 10~20초 걸릴 수 있어요)", view=self)
        code, data = await call_api(
            "POST", f"/summaries/{self.summary_id}/approval", timeout=APPROVAL_API_TIMEOUT,
            json={"decisions": to_save, "approved_by": interaction.user.display_name, "user_id": str(interaction.user.id)})
        if code != 200:
            log.warning("승인 저장 실패 (%s): %s", code, data.get("detail"))
            await interaction.edit_original_response(content=approval_error_text(code, data))
            return
        edited = sum(1 for d in to_save if d.get("edited_fields"))
        await interaction.edit_original_response(
            content=approval_lines(interaction.user.mention, data, edited, len(self.excluded)))
        log.info("승인 저장 완료 (summary=%s, 저장 %d건)", self.summary_id, len(data["saved"]),
                 extra={"record_ids": [r["id"] for r in data["saved"]], "approved_by": interaction.user.id})

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
