"""[A + G] Augmentation + Generation: 검색된 청크를 프롬프트에 붙여 LLM이 답하게 한다.

시스템 프롬프트는 정책 조회 프로세스(시간순 정리 / 상태 구분 / 충돌 탐지 /
최신 결정+근거+결정자+범위 / 미확정 사항 확인 질문)를 그대로 반영했다.
"""
from anthropic import Anthropic

from app.rag.models import SearchHit

SYSTEM_PROMPT = """너는 팀의 제품 정책 조회 도우미다. 반드시 <context> 안의 정책 문서만 근거로 답한다.
문서에 없는 내용은 추측하지 말고 "문서에서 확인되지 않음"이라고 쓴다.

답변 형식:
1. **최신 결론**: 가장 최근의 '최종결정' 기준 결론. 결정 근거, 결정자, 적용 범위, 결정일 포함.
2. **히스토리**: 같은 주제의 관련 정책을 시간순으로 (상태: 제안/논의중/최종결정 표시).
3. **충돌/불일치**: 문서끼리 내용이 다르면 무엇이 어떻게 다른지. 없으면 생략.
4. **확인 필요**: 최종결정이 아니거나 근거가 비어 있으면, 담당자에게 물어볼 질문을 1~3개.
각 항목 끝에 근거 문서 번호를 [1], [2]처럼 표시한다."""


def build_context(hits: list[SearchHit]) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        blocks.append(f'<doc id="{i}" title="{h.title}" url="{h.url}">\n{h.text}\n</doc>')
    return "<context>\n" + "\n".join(blocks) + "\n</context>"


class AnswerGenerator:
    def __init__(self, api_key: str, model: str, max_tokens: int):
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def answer(self, question: str, hits: list[SearchHit]) -> str:
        if not hits:
            return "관련 정책 문서를 찾지 못했습니다. 키워드를 바꾸거나 영역 필터를 빼고 다시 시도해 주세요."
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"{build_context(hits)}\n\n질문: {question}"}],
        )
        return "".join(b.text for b in msg.content if b.type == "text")
