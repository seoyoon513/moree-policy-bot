"""Notion '정책 결정 히스토리' DB를 한 번만 생성하고 .env에 ID를 기록한다.

사전 준비:
  1. https://www.notion.so/profile/integrations 에서 Internal integration 생성 → 토큰 복사
  2. DB를 넣을 빈 페이지를 만들고, 페이지 우측 상단 ··· → 연결(Connections) → 만든 integration 추가
  3. .env에 NOTION_TOKEN, NOTION_PARENT_PAGE_ID(페이지 URL 그대로 붙여도 됨) 입력

실행:
  python setup_notion.py      # 처음: DB 생성 / 이미 있으면: 새 속성만 추가
"""
import asyncio
import re
from pathlib import Path

import notion_store
from config import NOTION_DATA_SOURCE_ID, NOTION_PARENT_PAGE_ID, NOTION_TOKEN

ENV_PATH = Path(".env")


def update_env(values: dict[str, str]) -> None:
    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    for key, value in values.items():
        line = f"{key}={value}"
        if re.search(rf"^{key}=.*$", text, flags=re.M):
            text = re.sub(rf"^{key}=.*$", line, text, flags=re.M)
        else:
            text = text.rstrip("\n") + f"\n{line}\n"
    ENV_PATH.write_text(text, encoding="utf-8")


async def main() -> None:
    if not NOTION_TOKEN:
        raise SystemExit(".env에 NOTION_TOKEN을 넣어 주세요.")
    if NOTION_DATA_SOURCE_ID:
        # 이미 만든 DB: 새로 생긴 속성만 추가한다 (예: 재논의대상). 새 DB가 필요하면 .env에서 그 줄을 지우고 다시 실행
        added = await notion_store.ensure_schema()
        print(f"✅ 속성 추가: {', '.join(added)}" if added else "✅ 스키마가 최신이에요. 추가할 속성이 없어요.")
        return
    if not NOTION_PARENT_PAGE_ID:
        raise SystemExit(".env에 NOTION_PARENT_PAGE_ID(DB를 넣을 페이지 URL 또는 ID)를 넣어 주세요.")

    parent_id = notion_store.extract_id(NOTION_PARENT_PAGE_ID)
    try:
        info = await notion_store.create_database(parent_id)
    except Exception as e:
        msg = str(e)
        if "Could not find" in msg or "object_not_found" in msg:
            raise SystemExit(
                "페이지를 찾을 수 없어요. 페이지 ··· → 연결(Connections)에서 integration을 추가했는지 확인해 주세요."
            ) from e
        raise SystemExit(f"DB 생성 실패: {msg}") from e

    update_env({"NOTION_DATABASE_ID": info["database_id"], "NOTION_DATA_SOURCE_ID": info["data_source_id"]})
    print("✅ DB 생성 완료")
    print(f"   URL: {info['url']}")
    print("   .env에 NOTION_DATABASE_ID, NOTION_DATA_SOURCE_ID를 기록했어요. 봇을 재시작하세요.")


if __name__ == "__main__":
    asyncio.run(main())
