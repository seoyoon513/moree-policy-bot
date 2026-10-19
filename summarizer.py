"""스레드 메시지 → Claude 요약 → 정규화된 결정 목록."""
import json
import re
from dataclasses import dataclass
from datetime import datetime

from anthropic import AsyncAnthropic

from app import usage as usage_log

from config import ANTHROPIC_API_KEY, AREAS, CLAUDE_MODEL, KST, MAX_TRANSCRIPT_CHARS, STATUSES, SUMMARY_MAX_TOKENS
from prompts import SYSTEM_PROMPT, build_user_prompt


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

    def to_line(self) -> str:
        ts = self.created_at.astimezone(KST).strftime("%m-%d %H:%M")
        return f"[#{self.index}] [{ts}] {self.author}: {self.content}"


_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def build_transcript(messages: list[ThreadMessage]) -> str:
    """글자 수 제한을 넘으면 첫 메시지(주제)는 남기고 오래된 중간부터 생략."""
    lines = [m.to_line() for m in messages]
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


def normalize(data: dict, max_index: int) -> list[dict]:
    """필드 누락/잘못된 값을 보정해서 저장·표시 코드가 안전하게 쓰게 한다."""
    out = []
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

        out.append(
            {
                "area": area,
                "topic": topic or "(주제 없음)",
                "status": status,
                "decision": decision or "",
                "rationale": _str_or_none(d.get("rationale")),
                "decided_by": _str_or_none(d.get("decided_by")),
                "scope": _str_or_none(d.get("scope")),
                "external_refs": [str(r).strip() for r in d.get("external_refs") or [] if str(r).strip()],
                "open_questions": questions,
                "source_messages": sources,
            }
        )
    return out


async def summarize_thread(thread_name: str, messages: list[ThreadMessage]) -> list[dict]:
    transcript = build_transcript(messages)
    try:
        with usage_log.timer() as elapsed:
            resp = await _get_client().messages.create(
                model=CLAUDE_MODEL,
                max_tokens=SUMMARY_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_user_prompt(thread_name, transcript)}],
            )
    except Exception as e:  # 네트워크/인증/레이트리밋 등
        raise SummaryError(f"Claude API 호출 실패: {e}") from e

    usage_log.from_anthropic("summarize", CLAUDE_MODEL, resp, elapsed())
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    if getattr(resp, "stop_reason", None) == "max_tokens":
        # 출력이 한도에서 잘리면 JSON이 닫히지 않아 파싱이 엉뚱한 위치에서 실패한다 → 원인을 그대로 알려줌
        raise SummaryError(f"출력이 max_tokens({SUMMARY_MAX_TOKENS})에서 잘림 — 스레드가 길거나 응답이 반복됨", raw=text)
    try:
        return normalize(parse_json(text), max_index=len(messages))
    except SummaryError as e:
        e.raw = text
        raise
