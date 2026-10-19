"""승인된 결정 저장.

1) 로컬 JSON에 항상 저장 (백업 겸 오프라인 확인용)
   data/decisions/<기능영역>/<YYYYMMDD-HHMMSS>_<id>.json   결정 1건 = 파일 1개
   data/history.jsonl                                      전체 히스토리 (append-only)
2) Notion이 설정돼 있으면 '정책 결정 히스토리' DB에 페이지로 추가
"""
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import notion_store
from config import DATA_DIR, KST

log = logging.getLogger("policy-bot")


@dataclass
class SaveResult:
    record: dict
    path: Path
    notion_url: str | None = None
    notion_error: str | None = None


def _safe_dirname(name: str) -> str:
    return "".join(c if c not in '\\/:*?"<>|' else "_" for c in name)


def _build_record(d: dict, source: dict, approved_by: str, now: datetime) -> dict:
    urls_by_index = source.get("message_urls_by_index", {})
    src = {k: v for k, v in source.items() if k != "message_urls_by_index"}
    src["message_urls"] = [urls_by_index[n] for n in d.get("source_messages", []) if n in urls_by_index]
    return {
        "id": uuid.uuid4().hex[:8],
        **{k: v for k, v in d.items() if k != "source_messages"},
        "source": src,
        "approved_by": approved_by,
        "approved_at": now.isoformat(timespec="seconds"),
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


async def save_decisions(decisions: list[dict], *, source: dict, approved_by: str,
                         approved_at: datetime | None = None) -> list[SaveResult]:
    """approved_at: 지난 스레드를 일괄 저장할 때 '결정이 난 시점'(스레드 마지막 메시지 시각)을 넣는다.
    비우면 지금 시각. 이 값이 Notion '승인일'이 되고, 검색·답변의 시간순 정렬 기준이 된다."""
    now = approved_at.astimezone(KST) if approved_at else datetime.now(KST)
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
