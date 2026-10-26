"""멘션 질문(/정책질문 대체) 테스트. 디스코드 객체는 필요한 속성만 가진 가짜. 데이터는 전부 가짜."""
import asyncio
from types import SimpleNamespace as NS

import pytest

import mention
from policy_search import split_message

BOT = NS(id=999, bot=True, display_name="정책봇")
ROLE = NS(id=555)
USER = NS(id=1, bot=False, display_name="사용자A")


def _msg(content="", *, mentions=(), roles=(), ref=None, author=USER, guild_role=ROLE):
    return NS(content=content, author=author, mentions=list(mentions), role_mentions=list(roles), id=10,
              guild=NS(self_role=guild_role), reference=NS(resolved=ref, message_id=getattr(ref, "id", None)) if ref else None)


def test_addressed_by_user_or_role_mention():
    assert mention.is_addressed(_msg("<@999> 질문", mentions=[BOT]), BOT)
    assert mention.is_addressed(_msg("<@&555> 질문", roles=[ROLE]), BOT)  # 자동완성에서 역할을 고른 경우
    assert not mention.is_addressed(_msg("그냥 대화"), BOT)
    assert not mention.is_addressed(_msg("<@999>", mentions=[BOT], author=NS(id=2, bot=True)), BOT)  # 봇끼리 무시


def test_reply_to_bot_answer_continues_without_ping():
    answer = NS(id=7, author=BOT, content="**Q. 리뷰 누가 써?**\n\n발도장 인증한 사용자만 [1]")
    approval = NS(id=8, author=BOT, content="정리 결과 2건 — 승인해 주세요")
    assert mention.is_addressed(_msg("그럼 비회원은?", ref=answer), BOT)
    assert not mention.is_addressed(_msg("확인", ref=approval), BOT)  # 정리 승인 화면 등 다른 봇 메시지는 아님


def test_extract_question_strips_all_mention_forms():
    assert mention.extract_question("<@999>  오프라인 진입은? <@!999> <@&555>", [999, 555]) == "오프라인 진입은?"
    assert mention.extract_question("<@123> 이 사람 말 맞아?", [999]) == "<@123> 이 사람 말 맞아?"  # 다른 사람 멘션은 둠


def test_parse_bot_answer_round_trips_format_answer():
    data = {"answer": "최신 결론: A [1]", "sources": [{"title": "정책A", "url": "https://notion.so/a"}], "usage": None}
    q, a = mention.parse_bot_answer(mention.format_answer("리뷰 누가 써?", data))
    assert (q, a) == ("리뷰 누가 써?", "최신 결론: A [1]")
    assert mention.parse_bot_answer("일반 메시지") is None


def test_format_context_puts_recent_last_and_caps_length():
    recent = [("A", "폴더 개수 얘기"), ("B", "x" * 1000)]
    ctx = mention.format_context(recent, replied=("이전 질문", "이전 답변"))
    assert ctx.startswith("[스레드 최근 메시지]") and ctx.index("[이전 질문]") > ctx.index("B:")
    assert "x" * (mention.MESSAGE_CHARS + 1) not in ctx  # 메시지당 길이 제한
    assert mention.format_context([]) is None
    long = mention.format_context([("A", "y" * 400)] * 50)
    assert len(long) <= mention.CONTEXT_CHARS


def test_split_message_breaks_on_lines():
    text = "\n".join(f"줄{i} " + "가" * 50 for i in range(100))
    parts = split_message(text, limit=500)
    assert all(len(p) <= 500 for p in parts) and "\n".join(parts) == text
    assert split_message("a" * 1200, limit=500) == ["a" * 500, "a" * 500, "a" * 200]


class FakeChannel:
    def __init__(self):
        self.sent = []

    def typing(self):
        class _T:
            async def __aenter__(s): return None
            async def __aexit__(s, *a): return False
        return _T()

    async def send(self, text, **kw):
        self.sent.append(text)


@pytest.fixture
def ask(monkeypatch):
    calls = []

    def run(content, response=(200, {"answer": "최신 결론: A [1]", "sources": [], "usage": None}), **kw):
        async def fake_api(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return response
        monkeypatch.setattr(mention, "call_api", fake_api)
        msg = _msg(content, mentions=[BOT], **kw)
        msg.channel = FakeChannel()
        replies = []

        async def reply(text, **_):
            replies.append(text)
        msg.reply = reply
        asyncio.run(mention.handle_mention(msg, BOT))
        return replies, calls
    return run


def test_handle_mention_calls_answer_api_with_user_and_context(ask):
    prev = NS(id=7, author=BOT, content="**Q. 리뷰 누가 써?**\n\n발도장 인증한 사용자만 [1]")
    replies, calls = ask("<@999> 그럼 비회원은?", ref=prev)
    method, path, kw = calls[0]
    assert (method, path) == ("POST", "/policies/answers")
    assert kw["json"]["question"] == "그럼 비회원은?"
    assert "[이전 질문] 리뷰 누가 써?" in kw["json"]["context"]
    assert kw["headers"] == {"X-User-Id": "1"}
    assert replies[0].startswith("**Q. 그럼 비회원은?**")


def test_handle_mention_without_question_shows_help(ask):
    replies, calls = ask("<@999>")
    assert calls == [] and replies == [mention.HELP_TEXT]


def test_handle_mention_reports_quota(ask):
    replies, _ = ask("<@999> 질문입니다", response=(429, {"detail": "오늘 한도(30회)를 다 썼어요"}))
    assert replies == ["⏸️ 오늘 한도(30회)를 다 썼어요"]
