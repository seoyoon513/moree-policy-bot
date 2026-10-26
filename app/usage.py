"""토큰 사용량 측정.

LLM/임베딩 API를 부를 때마다 응답의 usage(토큰 수)를 한 줄씩 data/usage.jsonl에 남긴다.
봇(요약)과 API 서버(임베딩·답변)가 같은 파일에 쓰므로, 기능별·일별 비용을 한 곳에서 본다.

    record("answer", model, input_tokens=..., output_tokens=..., latency_ms=...)
    summarize(days=7)  # 기능별 합계

단가는 .env로 바꿀 수 있다 (USD / 100만 토큰). 기본값은 작성 시점 공개 가격이라
모델을 바꾸거나 가격이 바뀌면 반드시 다시 확인할 것.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

_lock = threading.Lock()


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except ValueError:
        return default


# USD per 1M tokens. (input, output)
def price_table() -> dict[str, tuple[float, float]]:
    return {
        "llm": (_env_float("PRICE_LLM_INPUT_PER_MTOK", 3.0), _env_float("PRICE_LLM_OUTPUT_PER_MTOK", 15.0)),
        # 리랭커 등에 쓰는 작은 모델(이름에 haiku 포함). 단가가 큰 모델과 달라 따로 둔다
        "llm_small": (_env_float("PRICE_SMALL_LLM_INPUT_PER_MTOK", 1.0), _env_float("PRICE_SMALL_LLM_OUTPUT_PER_MTOK", 5.0)),
        "embedding": (_env_float("PRICE_EMBED_PER_MTOK", 0.02), 0.0),
    }


def _price_key(kind: str, model: str) -> str:
    if kind == "llm" and "haiku" in model.lower():
        return "llm_small"
    return kind


def usage_path() -> Path:
    return Path(os.environ.get("USAGE_LOG_PATH", os.path.join(os.environ.get("DATA_DIR", "data"), "usage.jsonl")))


@dataclass
class Usage:
    feature: str            # summarize | answer | embed_query | embed_index | judge
    model: str
    kind: str = "llm"       # llm | embedding
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    user: str | None = None  # 호출한 사람 (Discord 사용자 ID). 사용자별 일일 제한에 쓴다
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def compute_cost(self) -> "Usage":
        inp, out = price_table()[_price_key(self.kind, self.model)]
        # 캐시 읽기는 입력 단가의 10%, 캐시 쓰기는 125% (Anthropic 프롬프트 캐싱 기준)
        self.cost_usd = round(
            (self.input_tokens * inp + self.output_tokens * out
             + self.cache_read_tokens * inp * 0.1 + self.cache_write_tokens * inp * 1.25) / 1_000_000,
            6,
        )
        return self


def record(u: Usage) -> Usage:
    """비용 계산 후 파일에 append. 기록 실패가 본 기능을 막으면 안 되므로 예외는 삼킨다."""
    u.compute_cost()
    if os.environ.get("USAGE_LOG_DISABLED") == "1":
        return u
    try:
        p = usage_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock, p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(u), ensure_ascii=False) + "\n")
    except OSError:
        pass
    return u


def from_anthropic(feature: str, model: str, resp, latency_ms: int, user: str | None = None) -> Usage:
    """anthropic Message 응답의 usage → Usage 기록."""
    us = getattr(resp, "usage", None)
    return record(Usage(
        feature=feature, model=model, kind="llm",
        input_tokens=getattr(us, "input_tokens", 0) or 0,
        output_tokens=getattr(us, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(us, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(us, "cache_creation_input_tokens", 0) or 0,
        latency_ms=latency_ms,
        user=user,
    ))


@contextmanager
def timer():
    """with timer() as t: ...  →  t() 로 경과 ms"""
    start = time.perf_counter()
    yield lambda: int((time.perf_counter() - start) * 1000)


def load(days: int | None = None) -> list[dict]:
    p = usage_path()
    if not p.exists():
        return []
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since and datetime.fromisoformat(r["ts"]) < since:
            continue
        rows.append(r)
    return rows


def summarize(days: int | None = 7) -> dict:
    """기능별 호출 수 / 토큰 / 비용 / 평균 지연 + 일별 비용."""
    rows = load(days)
    by_feature: dict[str, dict] = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                                       "cost_usd": 0.0, "latency_ms_sum": 0})
    by_day: dict[str, float] = defaultdict(float)
    for r in rows:
        f = by_feature[r["feature"]]
        f["calls"] += 1
        f["input_tokens"] += r.get("input_tokens", 0) + r.get("cache_read_tokens", 0) + r.get("cache_write_tokens", 0)
        f["output_tokens"] += r.get("output_tokens", 0)
        f["cost_usd"] += r.get("cost_usd", 0.0)
        f["latency_ms_sum"] += r.get("latency_ms", 0)
        by_day[r["ts"][:10]] += r.get("cost_usd", 0.0)

    features = {}
    for name, f in by_feature.items():
        calls = f["calls"]
        features[name] = {
            "calls": calls,
            "input_tokens": f["input_tokens"],
            "output_tokens": f["output_tokens"],
            "avg_input_tokens": round(f["input_tokens"] / calls),
            "avg_output_tokens": round(f["output_tokens"] / calls),
            "cost_usd": round(f["cost_usd"], 4),
            "avg_cost_usd": round(f["cost_usd"] / calls, 6),
            "avg_latency_ms": round(f["latency_ms_sum"] / calls),
        }
    return {
        "days": days,
        "total_calls": len(rows),
        "total_cost_usd": round(sum(r.get("cost_usd", 0.0) for r in rows), 4),
        "features": features,
        "daily_cost_usd": {d: round(c, 4) for d, c in sorted(by_day.items())},
    }
