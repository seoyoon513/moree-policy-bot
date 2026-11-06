import os

import pytest

# 로컬 .env를 읽지 않는다: 개발자 .env 값(RAG_SOURCE=notion 등)이 테스트 결과를 바꾸지 않게.
# app.config가 import되기 전에 정해야 하므로 맨 위에 둔다.
os.environ["ENV_FILE"] = ""
# 테스트가 실제 data/usage.jsonl에 기록을 남기지 않도록 (필요한 테스트만 fixture로 켠다)
os.environ.setdefault("USAGE_LOG_DISABLED", "1")
# 셸에 남아 있을 수 있는 값도 테스트 기본값으로
os.environ["API_KEY"] = ""
os.environ["RERANK_ENABLED"] = "false"

from shared.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_settings():
    """get_settings()는 lru_cache라, monkeypatch.setenv로 바꾼 값이 다음 테스트로 새지 않게 매번 비운다."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
