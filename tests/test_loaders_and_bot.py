"""로더(Notion 페이지 / history.jsonl)와 봇 커맨드 등록 테스트. 네트워크 없이 동작."""
import json

import discord
from discord import app_commands

from app.config import Settings
from app.rag.chunker import chunk_document
from app.rag.notion_loader import JsonlLoader, build_loader, NotionLoader, page_to_document

RECORD = {
    "id": "abc12345", "area": "회원가입·온보딩", "topic": "오프라인 실행 시 진입 플로우", "status": "최종결정",
    "decision": "세션이 있으면 탭바로 진입하고 오류 UI를 띄운다.", "rationale": "온보딩 재노출 이슈 해결",
    "decided_by": "담당자A", "scope": "앱 실행 시점", "external_refs": ["Figma 참고"],
    "open_questions": [{"question": "비로그인 오프라인은?", "ask_to": "담당자A"}],
    "source": {"thread_name": "오프라인 환경 플로우", "thread_url": "https://discord.com/channels/1/2"},
    "approved_by": "yoon", "approved_at": "2026-10-10T18:17:43+09:00",
}


def _rt(text):
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


NOTION_PAGE = {
    "object": "page", "id": "page-1", "url": "https://www.notion.so/page1", "last_edited_time": "2026-10-11T00:00:00Z",
    "properties": {
        "주제": {"type": "title", "title": _rt("리뷰 작성 조건")},
        "기능영역": {"type": "select", "select": {"name": "리뷰"}},
        "상태": {"type": "select", "select": {"name": "논의중"}},
        "결정": {"type": "rich_text", "rich_text": _rt("발도장 인증한 사용자만 작성")},
        "근거": {"type": "rich_text", "rich_text": []},
        "결정자": {"type": "rich_text", "rich_text": _rt("PM")},
        "적용범위": {"type": "rich_text", "rich_text": _rt("행사 상세 > 리뷰 탭")},
        "승인일": {"type": "date", "date": {"start": "2026-10-11T09:00:00+09:00"}},
        "스레드": {"type": "url", "url": "https://discord.com/channels/1/3"},
    },
}


def test_notion_page_to_document_uses_bot_schema():
    d = page_to_document(NOTION_PAGE, Settings())
    assert d.title == "리뷰 작성 조건"
    assert d.metadata["area"] == "리뷰" and d.metadata["status"] == "논의중"
    assert d.metadata["decided_at"] == "2026-10-11"
    assert "결정: 발도장 인증한 사용자만 작성" in d.body
    assert "근거:" not in d.body  # 빈 속성은 생략


def test_jsonl_loader_reads_bot_history(tmp_path):
    (tmp_path / "history.jsonl").write_text(json.dumps(RECORD, ensure_ascii=False) + "\n", encoding="utf-8")
    docs = JsonlLoader(Settings(data_dir=str(tmp_path))).load()
    assert len(docs) == 1
    d = docs[0]
    assert d.page_id == "local-abc12345"
    assert d.url == "https://discord.com/channels/1/2"
    assert "확인필요: • 비로그인 오프라인은? → 담당자A" in d.body
    header = chunk_document(d)[0].text.splitlines()[0]
    assert header.startswith("[정책] 오프라인 실행 시 진입 플로우 | 영역: 회원가입·온보딩 | 상태: 최종결정")


def test_source_auto_selection():
    assert isinstance(build_loader(Settings(notion_token="", notion_data_source_id="")), JsonlLoader)
    assert isinstance(build_loader(Settings(notion_token="t", notion_data_source_id="d")), NotionLoader)
    assert Settings(rag_source="jsonl", notion_token="t", notion_data_source_id="d").resolved_source() == "jsonl"


def test_claude_model_env_is_shared(monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-test-model")
    assert Settings(_env_file=None).llm_model == "claude-test-model"


def test_policy_commands_register_on_client_tree():
    from policy_search import register_policy_commands
    client = discord.Client(intents=discord.Intents.default())
    tree = app_commands.CommandTree(client)
    register_policy_commands(tree)
    names = {c.name for c in tree.get_commands()}
    assert names == {"정책검색", "정책질문"}


def test_reindex_waits_out_running_job(monkeypatch):
    import asyncio
    import policy_search as ps
    calls = []
    responses = iter([(409, {"detail": "running"}), (409, {"detail": "running"}), (202, {"id": "job1"})])

    async def fake_call(method, path, **kw):
        calls.append((method, path, kw["headers"]["X-Admin-Token"]))
        return next(responses)

    monkeypatch.setattr(ps, "_call", fake_call)
    monkeypatch.setattr(ps, "ADMIN_TOKEN", "tok")
    monkeypatch.setattr(ps, "REINDEX_INITIAL_DELAY", 0)
    monkeypatch.setattr(ps, "REINDEX_RETRY_DELAY", 0)
    asyncio.run(ps._reindex())
    assert calls == [("POST", "/admin/sync-jobs", "tok")] * 3
