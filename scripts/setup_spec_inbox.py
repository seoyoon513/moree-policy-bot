"""'정책서 미분류 제안' 페이지를 한 번만 만들고 .env에 NOTION_SPEC_INBOX_PAGE로 기록한다.

맞는 정책서를 못 찾은 반영 제안이 이 페이지에 체크박스로 쌓인다. PM이 반영할 곳을 정하고 체크해서 닫는다.
페이지는 NOTION_PARENT_PAGE_ID(결정 히스토리 DB가 있는 페이지) 아래에 만든다.

    python -m scripts.setup_spec_inbox
"""
import asyncio

import notion_store
from config import NOTION_PARENT_PAGE_ID, NOTION_SPEC_INBOX_PAGE
from setup_notion import update_env

TITLE = "정책서 미분류 제안"
GUIDE = ("정책봇이 맞는 정책서를 찾지 못한 반영 제안이 여기에 쌓여요. "
         "반영할 정책서·위치를 정해 원문에 반영하거나, 반영하지 않기로 했으면 체크해서 닫아 주세요.")


async def main() -> None:
    if NOTION_SPEC_INBOX_PAGE:
        raise SystemExit("이미 NOTION_SPEC_INBOX_PAGE가 있어요. 새로 만들려면 .env에서 그 줄을 지우고 다시 실행하세요.")
    if not NOTION_PARENT_PAGE_ID:
        raise SystemExit(".env에 NOTION_PARENT_PAGE_ID가 필요해요.")
    page = await notion_store.get_client().pages.create(
        parent={"page_id": notion_store.extract_id(NOTION_PARENT_PAGE_ID)},
        icon={"type": "emoji", "emoji": "📥"},
        properties={"title": {"title": [{"type": "text", "text": {"content": TITLE}}]}},
        children=[{"type": "callout", "callout": {"rich_text": [{"type": "text", "text": {"content": GUIDE}}],
                                                  "icon": {"type": "emoji", "emoji": "💡"}, "color": "gray_background"}}],
    )
    update_env({"NOTION_SPEC_INBOX_PAGE": page["id"].replace("-", "")})
    print(f"✅ {TITLE} 페이지 생성: {page.get('url')}\n   .env에 NOTION_SPEC_INBOX_PAGE를 기록했어요.")


if __name__ == "__main__":
    asyncio.run(main())
