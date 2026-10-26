"""스레드 메시지 → Claude 요약 → 정규화된 결정 목록."""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from anthropic import AsyncAnthropic

from app import usage as usage_log

from config import ANTHROPIC_API_KEY, AREAS, CLAUDE_MODEL, KST, MAX_TRANSCRIPT_CHARS, STATUSES, SUMMARY_MAX_TOKENS
from prompts import SYSTEM_PROMPT, build_user_prompt, format_previous


class SummaryError(Exception):
    def __init__(self, msg: str, raw: str | None = None):
        super().__init__(msg)
        self.raw = raw  # 실패 원인 분석용 모델 원문 응답


@dataclass
class ThreadMessage:
    index: int          # 1부터 시작하는 메시지 번호 (#N)
    author: str
    created_at: datetime
    content: str
    jump_url: str
    agreed_by: list[str] = field(default_factory=list)  # 동의 리액션을 남긴 사람 (작성자 제외)

    def to_line(self, new: bool = False) -> str:
        ts = self.created_at.astimezone(KST).strftime("%m-%d %H:%M")
        line = f"[#{self.index}] [{ts}]{' [새 메시지]' if new else ''} {self.author}: {self.content}"
        if self.agreed_by:
            line += f" [동의 리액션: {', '.join(self.agreed_by)}]"
        return line


_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def build_transcript(messages: list[ThreadMessage], new_after: datetime | None = None) -> str:
    """글자 수 제한을 넘으면 첫 메시지(주제)는 남기고 오래된 중간부터 생략.
    new_after: 재정리일 때 마지막 정리 시점. 그 뒤 메시지에 [새 메시지] 표시."""
    lines = [m.to_line(new=bool(new_after and m.created_at > new_after)) for m in messages]
    if sum(len(l) + 1 for l in lines) <= MAX_TRANSCRIPT_CHARS or len(lines) <= 2:
        return "\n".join(lines)

    head = lines[0]
    budget = MAX_TRANSCRIPT_CHARS - len(head) - 50
    tail: list[str] = []
    for line in reversed(lines[1:]):
        if budget - (len(line) + 1) < 0:
            break
        tail.append(line)
        budget -= len(line) + 1
    tail.reverse()
    skipped = len(lines) - 1 - len(tail)
    return "\n".join([head, f"... (중간 메시지 {skipped}개 생략) ...", *tail])


def parse_json(text: str) -> dict:
    """모델 응답에서 JSON 객체만 뽑아낸다 (코드펜스/앞뒤 설명이 붙어도 처리)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise SummaryError("모델 응답에 JSON이 없어요.")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise SummaryError(f"JSON 파싱 실패: {e}") from e


def _str_or_none(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def extract_urls(refs: list) -> list[str]:
    """외부참조는 URL만 남긴다. "피그마: https://..."처럼 설명이 붙어 와도 URL만 뽑고, URL 없는 항목은 버린다."""
    urls: list[str] = []
    for r in refs:
        for u in _URL_RE.findall(str(r)):
            if u not in urls:
                urls.append(u)
    return urls


RELATIONS = ("new", "update", "unchanged", "reopen")


def _relation(d: dict, status: str, prior: dict[str, dict] | None, linked: set[str]) -> tuple[str, str | None]:
    """재정리에서 이전 기록과의 관계. 애매한 연결은 new로 내린다 (잘못 연결하면 유효한 기록이 덮어써진다).
    - 이전 기록이 없는 정리, 목록에 없는 ID, 이미 다른 결정이 가져간 ID → new
    - unchanged인데 상태가 다르면 → update (상태 변화도 변경이다)
    - update인데 이전이 최종결정이고 이번이 최종결정이 아니면 → reopen (확정을 논의중으로 덮지 않는다)"""
    if not prior:
        return "new", None
    rel = d.get("relation") if d.get("relation") in RELATIONS else "new"
    pid = _str_or_none(d.get("prior_id"))
    if rel == "new" or pid not in prior or pid in linked:
        return "new", None
    old = prior[pid]
    if rel == "unchanged" and old.get("status") != status:
        rel = "update"
    if rel in ("update", "reopen"):
        rel = "reopen" if old.get("status") == "최종결정" and status != "최종결정" else "update"
    return rel, pid


def normalize(data: dict, max_index: int, prior: dict[str, dict] | None = None) -> list[dict]:
    """필드 누락/잘못된 값을 보정해서 저장·표시 코드가 안전하게 쓰게 한다.
    prior: 재정리일 때 이 스레드의 현재 기록 {기록 ID: record}. 결정마다 relation·prior_id가 붙는다."""
    out = []
    linked: set[str] = set()
    for d in data.get("decisions") or []:
        if not isinstance(d, dict):
            continue
        decision = _str_or_none(d.get("decision"))
        topic = _str_or_none(d.get("topic"))
        if not decision and not topic:
            continue

        area = d.get("area") if d.get("area") in AREAS else "기타"
        status = d.get("status") if d.get("status") in STATUSES else "논의중"

        questions = []
        for q in d.get("open_questions") or []:
            if isinstance(q, dict) and _str_or_none(q.get("question")):
                questions.append({"question": q["question"].strip(), "ask_to": _str_or_none(q.get("ask_to"))})
            elif isinstance(q, str) and q.strip():
                questions.append({"question": q.strip(), "ask_to": None})

        sources = []
        for s in d.get("source_messages") or []:
            try:
                n = int(str(s).lstrip("#"))
            except ValueError:
                continue
            if 1 <= n <= max_index and n not in sources:
                sources.append(n)

        relation, prior_id = _relation(d, status, prior, linked)
        if prior_id:
            linked.add(prior_id)
        out.append(
            {
                "relation": relation,
                "prior_id": prior_id,
                "area": area,
                "topic": topic or "(주제 없음)",
                "status": status,
                "decision": decision or "",
                "rationale": _str_or_none(d.get("rationale")),
                "decided_by": _str_or_none(d.get("decided_by")),
                "scope": _str_or_none(d.get("scope")),
                "external_refs": extract_urls(d.get("external_refs") or []),
                "open_questions": questions,
                "source_messages": sources,
            }
        )
    return out


async def summarize_thread(thread_name: str, messages: list[ThreadMessage], previous: list[dict] | None = None,
                           new_after: str | None = None) -> list[dict]:
    """previous: 재정리일 때 이 스레드의 현재 기록. 있으면 결정마다 relation(new/update/unchanged/reopen)이 붙는다.
    new_after: 마지막 정리 시점(ISO). 그 뒤 메시지에 [새 메시지] 표시."""
    transcript = build_transcript(messages, datetime.fromisoformat(new_after) if new_after else None)
    prompt = build_user_prompt(thread_name, transcript, format_previous(previous) if previous else None)
    try:
        with usage_log.timer() as elapsed:
            resp = await _get_client().messages.create(
                model=CLAUDE_MODEL,
                max_tokens=SUMMARY_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
    except Exception as e:  # 네트워크/인증/레이트리밋 등
        raise SummaryError(f"Claude API 호출 실패: {e}") from e

    usage_log.from_anthropic("summarize", CLAUDE_MODEL, resp, elapsed())
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    if getattr(resp, "stop_reason", None) == "max_tokens":
        # 출력이 한도에서 잘리면 JSON이 닫히지 않아 파싱이 엉뚱한 위치에서 실패한다 → 원인을 그대로 알려줌
        raise SummaryError(f"출력이 max_tokens({SUMMARY_MAX_TOKENS})에서 잘림 — 스레드가 길거나 응답이 반복됨", raw=text)
    try:
        return normalize(parse_json(text), max_index=len(messages),
                         prior={r["id"]: r for r in previous} if previous else None)
    except SummaryError as e:
        e.raw = text
        raise
