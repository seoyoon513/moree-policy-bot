"""승인된 결정 저장.

1) 로컬 JSON에 항상 저장 (백업 겸 오프라인 확인용)
   data/decisions/<기능영역>/<YYYYMMDD-HHMMSS>_<id>.json   결정 1건 = 파일 1개
   data/history.jsonl                                      전체 히스토리 (append-only)
2) Notion이 설정돼 있으면 '정책 결정 히스토리' DB에 페이지로 추가

날짜는 두 가지를 따로 둔다.
- decided_at (결정일): 그 결정의 근거 메시지 중 가장 늦은 메시지 시각. 검색·답변의 시간순 정렬 기준
- approved_at (정리일): 요청자가 승인한 시각. 정리가 늦어져도 결정일이 밀리지 않게 분리했다

같은 스레드를 다시 정리하면(Q-7) 바뀐 결정은 이전 기록을 덮어쓴다. 로컬은 새 버전 줄을 추가하고(append-only 유지),
노션은 같은 페이지의 속성을 갱신하고 이전 버전들을 본문 '변경 이력'에 남긴다.
"""
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import notion_store
from config import DATA_DIR, KST
from summarizer import ThreadMessage

log = logging.getLogger("policy-bot")

EXCERPT_CHARS = 300  # 노션 본문에 넣을 근거 메시지 발췌 길이


@dataclass
class SaveResult:
    record: dict
    path: Path
    notion_url: str | None = None
    notion_error: str | None = None
    previous: list[dict] = field(default_factory=list)  # 덮어쓴 이전 버전들 (변경일 때)

    @property
    def updated(self) -> bool:
        return bool(self.previous)


def _safe_dirname(name: str) -> str:
    return "".join(c if c not in '\\/:*?"<>|' else "_" for c in name)


def messages_by_index(messages: list[ThreadMessage]) -> dict[int, dict]:
    """저장에 필요한 메시지 정보만 남긴다 (링크·작성자·시각·발췌). source에 담겨 승인 시점까지 들고 간다."""
    return {
        m.index: {
            "url": m.jump_url,
            "author": m.author,
            "at": m.created_at.astimezone(KST).isoformat(timespec="seconds"),
            "text": m.content[:EXCERPT_CHARS],
        }
        for m in messages
    }


def decided_at(source_messages: list[int], by_index: dict[int, dict]) -> str | None:
    """근거 메시지 중 가장 늦은 시각. 근거가 없으면 스레드 마지막 메시지 시각."""
    times = [by_index[n]["at"] for n in source_messages if n in by_index] or [m["at"] for m in by_index.values()]
    return max(times, key=datetime.fromisoformat) if times else None


# 요약 단계에서만 쓰는 필드 (기록에는 relation·reopens로 남긴다)
_TRANSIENT = {"source_messages", "prior_id", "unlinked_from", "include"}


def _build_record(d: dict, source: dict, approved_by: str, now: datetime, prior: dict | None = None) -> dict:
    """prior: 덮어쓸 이전 버전 (변경). 있으면 같은 기록 ID를 쓰고 version을 올린다."""
    by_index = source.get("messages_by_index", {})
    src = {k: v for k, v in source.items() if k != "messages_by_index"}
    used = [by_index[n] for n in d.get("source_messages", []) if n in by_index]
    src["message_urls"] = [m["url"] for m in used]
    src["messages"] = used
    approved = now.isoformat(timespec="seconds")
    record = {
        "id": prior["id"] if prior else uuid.uuid4().hex[:8],
        "version": prior.get("version", 1) + 1 if prior else 1,
        **{k: v for k, v in d.items() if k not in _TRANSIENT},
        "source": src,
        "decided_at": decided_at(d.get("source_messages", []), by_index) or approved,
        "approved_by": approved_by,
        "approved_at": approved,
    }
    if d.get("relation") == "reopen" and d.get("prior_id"):
        record["reopens"] = d["prior_id"]  # 이전 최종결정은 그대로 두고 재논의 중임을 연결
    return record


# --- 버전 (재정리·갱신) ---
# history.jsonl은 append-only. 같은 기록 ID가 여러 줄이면 버전이고, 마지막 줄이 현재 버전이다.
def load_versions() -> dict[str, list[dict]]:
    """기록 ID → 버전 목록 (오래된 순)."""
    path = Path(DATA_DIR) / "history.jsonl"
    out: dict[str, list[dict]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("id"):
            out.setdefault(r["id"], []).append(r)
    return out


def current_records(thread_id: str | None = None) -> list[dict]:
    """각 기록의 현재 버전. thread_id를 주면 그 스레드 것만."""
    latest = [vs[-1] for vs in load_versions().values()]
    if thread_id is not None:
        latest = [r for r in latest if str((r.get("source") or {}).get("thread_id")) == str(thread_id)]
    return latest


def summarized_until(records: list[dict]) -> str | None:
    """이 기록들을 정리할 때 읽은 마지막 메시지 시각. 값이 없는 옛 기록만 있으면 None."""
    times = [r["source"]["summarized_until"] for r in records if (r.get("source") or {}).get("summarized_until")]
    return max(times, key=datetime.fromisoformat) if times else None


def new_messages(messages: list[ThreadMessage], since: str | None) -> list[ThreadMessage]:
    if not since:
        return list(messages)
    t = datetime.fromisoformat(since)
    return [m for m in messages if m.created_at > t]


@dataclass
class ResummaryContext:
    """재정리 준비: 이 스레드의 현재 기록과 마지막 정리 시점. 봇(/정책정리)과 일괄 정리가 같이 쓴다."""
    previous: list[dict]
    since: str | None

    @property
    def nothing_new(self) -> bool:
        """전에 정리했고, 그 뒤 새 메시지가 없다 → LLM을 부르지 않는다.
        정리 시점이 없는 옛 기록은 알 수 없으니 다시 요약한다."""
        return bool(self.previous and self.since and not self._new)

    _new: list = field(default_factory=list, repr=False)


def resummary_context(thread_id: str, messages: list[ThreadMessage]) -> ResummaryContext:
    previous = current_records(thread_id)
    since = summarized_until(previous)
    return ResummaryContext(previous, since, new_messages(messages, since))


def thread_source(thread_id: str, thread_name: str, thread_url: str, messages: list[ThreadMessage]) -> dict:
    """저장 레코드의 source. summarized_until = 이번에 읽은 마지막 메시지 시각 (다음 재정리의 기준)."""
    last = max((m.created_at for m in messages), default=None)
    return {
        "thread_id": thread_id,
        "thread_name": thread_name,
        "thread_url": thread_url,
        "summarized_until": last.astimezone(KST).isoformat(timespec="seconds") if last else None,
        "messages_by_index": messages_by_index(messages),
    }


def _write_local(record: dict, now: datetime) -> Path:
    base = Path(DATA_DIR)
    folder = base / "decisions" / _safe_dirname(record["area"])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{now.strftime('%Y%m%d-%H%M%S')}_{record['id']}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    with (base / "history.jsonl").open("a", encoding="utf-8") as hf:
        hf.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


async def save_decisions(decisions: list[dict], *, source: dict, approved_by: str) -> list[SaveResult]:
    """source["messages_by_index"]: messages_by_index()의 결과. 결정일 계산과 근거 발췌에 쓴다.

    결정의 relation에 따라
    - update: 이전 기록을 덮어쓴다 (같은 ID, version+1). 노션은 속성 갱신 + 본문 '변경 이력'에 이전 버전들
    - reopen·new: 새 기록. reopen은 이전 최종결정을 덮지 않고 reopens로 연결만
    - unchanged는 호출하는 쪽에서 빼고 넘긴다
    """
    now = datetime.now(KST)
    versions = load_versions()
    results = []
    for d in decisions:
        history = versions.get(d.get("prior_id") or "") if d.get("relation") == "update" else None
        record = _build_record(d, source, approved_by, now, prior=history[-1] if history else None)
        result = SaveResult(record=record, path=_write_local(record, now), previous=history or [])
        if notion_store.enabled():
            try:
                if history:
                    result.notion_url = await notion_store.update_record(record, history)
                else:
                    result.notion_url = await notion_store.save_record(record)
            except Exception as e:  # 로컬은 저장됐으니 Notion 실패만 보고
                log.exception("Notion 저장 실패 (id=%s)", record["id"])
                result.notion_error = str(e)[:300]
        results.append(result)
    return results
