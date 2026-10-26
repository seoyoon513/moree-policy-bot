"""승인된 결정 저장.

1) 로컬 JSON에 항상 저장 (백업 겸 오프라인 확인용)
   data/decisions/<기능영역>/<YYYYMMDD-HHMMSS>_<id>.json   결정 1건 = 파일 1개
   data/history.jsonl                                      전체 히스토리 (append-only)
2) Notion이 설정돼 있으면 '정책 결정 히스토리' DB에 페이지로 추가

날짜는 두 가지를 따로 둔다.
- decided_at (결정일): 그 결정의 근거 메시지 중 가장 늦은 메시지 시각. 검색·답변의 시간순 정렬 기준
- approved_at (정리일): 요청자가 승인한 시각. 정리가 늦어져도 결정일이 밀리지 않게 분리했다
"""
import json
import logging
import uuid
from dataclasses import dataclass
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


def _build_record(d: dict, source: dict, approved_by: str, now: datetime) -> dict:
    by_index = source.get("messages_by_index", {})
    src = {k: v for k, v in source.items() if k != "messages_by_index"}
    used = [by_index[n] for n in d.get("source_messages", []) if n in by_index]
    src["message_urls"] = [m["url"] for m in used]
    src["messages"] = used
    approved = now.isoformat(timespec="seconds")
    return {
        "id": uuid.uuid4().hex[:8],
        **{k: v for k, v in d.items() if k != "source_messages"},
        "source": src,
        "decided_at": decided_at(d.get("source_messages", []), by_index) or approved,
        "approved_by": approved_by,
        "approved_at": approved,
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
    """source["messages_by_index"]: messages_by_index()의 결과. 결정일 계산과 근거 발췌에 쓴다."""
    now = datetime.now(KST)
    results = []
    for d in decisions:
        record = _build_record(d, source, approved_by, now)
        result = SaveResult(record=record, path=_write_local(record, now))
        if notion_store.enabled():
            try:
                result.notion_url = await notion_store.save_record(record)
            except Exception as e:  # 로컬은 저장됐으니 Notion 실패만 보고
                log.exception("Notion 저장 실패 (id=%s)", record["id"])
                result.notion_error = str(e)[:300]
        results.append(result)
    return results
