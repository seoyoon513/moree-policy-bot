"""로더(Notion 페이지 / history.jsonl)와 봇 커맨드 등록 테스트. 네트워크 없이 동작."""
import json

import discord
from discord import app_commands

from shared.config import Settings
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
        "결정일": {"type": "date", "date": {"start": "2026-10-11T09:00:00+09:00"}},
        "스레드": {"type": "url", "url": "https://discord.com/channels/1/3"},
        "기록ID": {"type": "rich_text", "rich_text": _rt("rec00002")},
        "재논의대상": {"type": "rich_text", "rich_text": _rt("rec00001")},
    },
}


def test_notion_page_to_document_uses_bot_schema():
    d = page_to_document(NOTION_PAGE)
    assert d.title == "리뷰 작성 조건"
    assert d.metadata["area"] == "리뷰" and d.metadata["status"] == "논의중"
    assert d.metadata["decided_at"] == "2026-10-11"
    assert "결정: 발도장 인증한 사용자만 작성" in d.body
    assert "근거:" not in d.body  # 빈 속성은 생략
    assert (d.metadata["record_id"], d.metadata["reopens"]) == ("rec00002", "rec00001")


def test_notion_page_without_decided_at_uses_old_approved_date():
    page = {**NOTION_PAGE, "properties": {k: v for k, v in NOTION_PAGE["properties"].items() if k != "결정일"}}
    page["properties"]["승인일"] = {"type": "date", "date": {"start": "2026-09-01T09:00:00+09:00"}}
    assert page_to_document(page).metadata["decided_at"] == "2026-09-01"


def test_jsonl_loader_reads_bot_history(tmp_path):
    (tmp_path / "history.jsonl").write_text(json.dumps(RECORD, ensure_ascii=False) + "\n", encoding="utf-8")
    docs = JsonlLoader(Settings(data_dir=str(tmp_path))).load()
    assert len(docs) == 1
    d = docs[0]
    assert d.page_id == "local-abc12345"
    assert d.url == "https://discord.com/channels/1/2"
    assert (d.metadata["record_id"], d.metadata["reopens"]) == ("abc12345", "")
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
    from bot.policy_search import register_policy_commands
    client = discord.Client(intents=discord.Intents.default())
    tree = app_commands.CommandTree(client)
    register_policy_commands(tree)
    names = {c.name for c in tree.get_commands()}
    assert names == {"정책검색"}  # /정책질문은 멘션 질문으로 대체 (mention.py)


def test_bot_summary_payload_matches_api_schema():
    """봇이 보내는 본문이 API 스키마(SummaryCreate)를 통과하는지 — 봇과 API 사이의 계약 테스트."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.api.schemas import SummaryCreate
    from shared.domain import ThreadMessage
    from bot.main import summary_payload
    thread = SimpleNamespace(id=123, name="가짜 스레드", jump_url="https://discord.com/channels/1/123")
    msgs = [ThreadMessage(1, "가짜", datetime(2026, 6, 1, tzinfo=timezone.utc), "안녕", "https://discord.com/x", ["동료"])]
    body = SummaryCreate.model_validate(summary_payload(thread, msgs, 42))
    assert (body.thread_id, body.user_id, body.messages[0].agreed_by) == ("123", "42", ["동료"])


def test_approval_lines_from_api_result():
    from bot.views import approval_error_text, approval_lines
    result = {"saved": [{"id": "a", "topic": "주제A", "version": 2, "updated": True, "notion_url": "https://n/a"},
                        {"id": "b", "topic": "주제B", "version": 1, "updated": False, "notion_error": "503"}],
              "reflect_lines": ["📚 기준 정책서 반영 제안", "• ✔️ 이미 반영됨 · 주제A"], "replayed": True}
    text = approval_lines("@me", result, edited=1, excluded=0)
    assert "결정 2건 저장됨 (수정 1건 · 제외 0건)" in text and "🔁 변경 v2 · [주제A](https://n/a)" in text
    assert "주제B — ⏳ 노션 저장 대기 중" in text and "다시 저장하지 않았어요" in text and text.endswith("주제A")
    assert "만료" in approval_error_text(404, {})


def test_bot_process_imports_only_shared_not_app():
    """폴더 경계: bot/ → shared/ ← app/. 봇 프로세스는 shared만 쓰고 app(API 쪽: LLM·노션·원장·벡터DB·흐름)은
    한 모듈도 import하지 않는다. 무거운 라이브러리도 마찬가지 (새 프로세스에서 확인)."""
    import subprocess
    import sys
    code = ("import bot.main, sys; print(sorted(m for m in sys.modules if m == 'app' or m.startswith('app.') "
            "or m.split('.')[0] in ('anthropic', 'notion_client', 'chromadb', 'openai', 'fastapi')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={"ENV_FILE": "", "PATH": ""})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", out.stdout + out.stderr


def test_shared_does_not_depend_on_bot_or_app():
    """shared는 양쪽이 쓰는 바닥이라 bot·app 어느 쪽도 import하지 않는다 (순환·경계 침범 방지)."""
    import ast
    from pathlib import Path
    bad = []
    for p in Path(__file__).resolve().parents[1].joinpath("shared").glob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            bad += [f"{p.name}: {n}" for n in names if n.split(".")[0] in ("bot", "app")]
    assert bad == []
