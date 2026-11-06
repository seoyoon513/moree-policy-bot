"""설정 통합(A-1): Settings 하나가 .env·환경변수를 읽고, 시작 시 문제를 알려 준다."""
from shared.config import Settings, get_settings

PAGE = "a" * 32
OTHER = "b" * 32


def test_tests_do_not_read_local_env_file():
    # conftest가 ENV_FILE=""로 .env를 끈다 → 개발자 .env(RAG_SOURCE=notion 등)가 섞이지 않는다
    assert Settings.model_config["env_file"] is None
    assert Settings().rag_source == "auto"


def test_env_file_then_env_var_priority(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DATA_DIR=from-file\nGUILD_ID=  123  \nMAX_MESSAGES=50\n", encoding="utf-8")
    s = Settings(_env_file=env)
    assert (s.data_dir, s.guild_id, s.max_messages) == ("from-file", "123", 50)  # 앞뒤 공백 제거, 숫자 변환
    monkeypatch.setenv("DATA_DIR", "from-env")
    assert Settings(_env_file=env).data_dir == "from-env"  # 환경변수(Docker env_file)가 이긴다


def test_claude_model_and_llm_model_are_one_setting(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "m-llm")
    assert Settings().llm_model == "m-llm"
    monkeypatch.delenv("LLM_MODEL")
    monkeypatch.setenv("CLAUDE_MODEL", "m-claude")
    assert Settings().llm_model == "m-claude"


def test_spec_pages_two_views_of_one_value():
    s = Settings(notion_spec_pages=f"북마크=https://www.notion.so/x-{PAGE}, 지도·탐색={PAGE}\n*={OTHER}")
    assert s.spec_pages_by_area() == {"북마크": f"https://www.notion.so/x-{PAGE}", "지도·탐색": PAGE, "*": OTHER}
    from app.rag.spec_index import spec_pages
    assert spec_pages(s) == {PAGE: ["북마크", "지도·탐색"], OTHER: []}


def test_comma_lists():
    s = Settings(spec_reflect_statuses="최종결정, 논의중 ,", notion_pm_user_ids="u1,\nu2")
    assert s.spec_reflect_status_list() == ["최종결정", "논의중"]
    assert s.pm_user_id_list() == ["u1", "u2"]
    assert Settings().spec_reflect_status_list() == ["최종결정"]


def test_usage_log_path_defaults_to_data_dir():
    assert Settings(data_dir="d").resolved_usage_log_path().replace("\\", "/") == "d/usage.jsonl"
    assert Settings(data_dir="d", usage_log_path="/x/u.jsonl").resolved_usage_log_path() == "/x/u.jsonl"


def test_problems_for_bot():
    assert len(Settings().problems("bot")) == 1  # DISCORD_TOKEN (LLM·노션 키는 API에만 필요)
    assert Settings(discord_token="t").problems("bot") == []
    assert any("POLICY_API_URL" in p for p in Settings(discord_token="t", policy_api_url="api:8000").problems("bot"))


def test_problems_notion_pair_and_rag_source():
    ok = dict(discord_token="t", anthropic_api_key="k")
    assert any("NOTION_TOKEN" in p for p in Settings(**ok, notion_token="n").problems("bot"))
    assert Settings(**ok, notion_token="n", notion_data_source_id="d").problems("bot") == []
    assert any("RAG_SOURCE" in p for p in Settings(**ok, rag_source="notoin").problems("bot"))


def test_problems_for_api_keys():
    assert any("OPENAI_API_KEY" in p for p in Settings().problems("api"))
    assert any("ANTHROPIC_API_KEY" in p for p in Settings(embedding_backend="local").problems("api"))
    assert Settings(embedding_backend="local", anthropic_api_key="k").problems("api") == []


def test_admin_token_default_is_a_warning():
    assert Settings().warnings() and Settings().warnings("bot") == []  # 봇은 관리자 토큰을 안 쓴다
    assert Settings(admin_token="secret").warnings() == []


def test_get_settings_cache_is_reset_between_tests(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "only-in-this-test")
    assert get_settings().data_dir == "only-in-this-test"


def test_get_settings_cache_did_not_leak():
    assert get_settings().data_dir == "data"
