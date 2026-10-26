"""원천 정책서(노션 기능명세서) 로더 — 정책 ID 단위로 쪼갠다.

정책서 구조 (2026-10-26 기능명세서 기준)
- 화면 ID(XX-05)는 접히는 제목("XX-05. 목록 편집")에, 기능 ID(XX-05-03)는 그 아래 "기능 요구사항" 표의 첫 칸에 있다.
- 그래서 정책 하나 = 표의 한 행. 반영 제안 댓글도 이 행(block_id)에 단다.

가져오기(fetch_tree)와 파싱(parse_tree)을 나눴다. parse_tree는 API 없이 도는 순수 함수라 가짜 블록으로 테스트한다.
LLM 호출이 없어서 사용량 기록 대상이 아니다.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

POLICY_ID_RE = re.compile(r"^[A-Z]{2,4}-\d{2}(?:-\d{2})?$")
SCREEN_HEADING_RE = re.compile(r"^\s*([A-Z]{2,4}-\d{2})\b")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
HEADINGS = {"heading_1": 1, "heading_2": 2, "heading_3": 3, "heading_4": 4}


@dataclass
class SpecEntry:
    """정책서의 정책 하나 (ID 표의 한 행, 또는 ID 없는 표의 한 행)."""
    policy_id: str | None      # XX-05-03. ID 없는 표(공통 규칙 등)는 None
    screen_id: str | None      # XX-05 (제목에서)
    screen_title: str
    name: str                  # 기능명 (ID 다음 칸)
    body: str                  # 나머지 칸을 "헤더: 값"으로 이은 것
    section_path: str          # "화면별 상세 명세 > XX-05. 목록 편집 > 기능 요구사항"
    block_id: str              # 표 행 — 수정 제안 댓글을 다는 곳
    table_id: str              # 표 — 새 정책 콜아웃을 이 뒤에 넣는다
    parent_id: str             # 표가 들어 있는 블록(접히는 제목) 또는 페이지
    page_id: str
    last_edited: str = ""
    tags: list[str] = field(default_factory=list)            # 경로 제목의 버전 표시 (1.3.0)
    heading_colors: list[str] = field(default_factory=list)  # 경로 제목의 색. 규칙이 없어 메타데이터로만 둔다
    extra_block_ids: list[str] = field(default_factory=list)      # ID 칸이 비어 이어 붙인 행
    duplicate_block_ids: list[str] = field(default_factory=list)  # 같은 내용이 복사돼 또 있는 행


@dataclass
class SpecDocument:
    page_id: str
    url: str
    title: str
    entries: list[SpecEntry]
    screens: dict[str, dict]   # screen_id → {title, purpose, heading_id}
    warnings: list[str]

    def by_id(self) -> dict[str, SpecEntry]:
        return {e.policy_id: e for e in self.entries if e.policy_id}

    def last_table_of(self, screen_id: str) -> SpecEntry | None:
        """새 정책 콜아웃을 넣을 위치: 그 화면의 마지막 ID 표."""
        found = [e for e in self.entries if e.screen_id == screen_id and e.policy_id]
        return found[-1] if found else None


def plain(rich: list[dict]) -> str:
    return "".join(t.get("plain_text", "") for t in rich or [])


def _block_text(b: dict) -> str:
    return plain(b.get(b["type"], {}).get("rich_text", []))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


class _Parser:
    def __init__(self, page_id: str):
        self.page_id = page_id
        self.entries: list[SpecEntry] = []
        self.screens: dict[str, dict] = {}
        self.warnings: list[str] = []

    # 제목 스택: [(level, text, block_id, color)]
    def walk(self, blocks: list[dict], parent_id: str, stack: list[tuple]) -> None:
        stack = list(stack)
        for b in blocks:
            t = b["type"]
            if t in HEADINGS:
                level = HEADINGS[t]
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, _norm(_block_text(b)), b["id"], b[t].get("color", "default")))
                m = SCREEN_HEADING_RE.match(stack[-1][1])
                if m:  # 화면 목록 표에서 먼저 만들어졌어도 제목·위치는 실제 제목 기준
                    s = self.screens.setdefault(m.group(1), {"title": "", "purpose": "", "heading_id": ""})
                    if not s["heading_id"]:
                        s.update(title=stack[-1][1], heading_id=b["id"])
                if b.get("children"):  # 접히는 제목: 내용이 자식에 있다
                    self.walk(b["children"], b["id"], stack)
            elif t == "table":
                self.table(b, parent_id, stack)
            elif b.get("children") and t not in ("child_page", "child_database"):
                self.walk(b["children"], b["id"], stack)

    def table(self, table: dict, parent_id: str, stack: list[tuple]) -> None:
        rows = [r for r in table.get("children", []) if r["type"] == "table_row"]
        if not rows:
            return
        cells = [[_norm(plain(c)) if i == 0 else plain(c).strip() for i, c in enumerate(r["table_row"]["cells"])]
                 for r in rows]
        header = cells[0] if table["table"].get("has_column_header") else None
        data = list(zip(rows[1:], cells[1:])) if header else list(zip(rows, cells))

        screen_id = next((m.group(1) for _, text, _, _ in reversed(stack) if (m := SCREEN_HEADING_RE.match(text))), None)
        screen_title = self.screens.get(screen_id, {}).get("title", "") if screen_id else ""
        section_path = " > ".join(text for _, text, _, _ in stack)
        tags = sorted({v for _, text, _, _ in stack for v in VERSION_RE.findall(text)})
        colors = [c for _, _, _, c in stack if c != "default"]

        # "항목 | 내용" 표는 정책이 아니라 화면 정보
        if header and header[0] == "항목":
            for _, row in data:
                if len(row) > 1 and row[0] == "화면 목적" and screen_id in self.screens:
                    self.screens[screen_id]["purpose"] = row[1]
            return

        has_id = any(POLICY_ID_RE.match(row[0]) for _, row in data if row)
        # 화면 목록(Screen Index): 화면 제목 밖에서 화면 ID(XX-05)만 나열한 표 → 정책이 아니라 화면 정보
        if has_id and not screen_id and all(SCREEN_HEADING_RE.fullmatch(row[0]) for _, row in data if row and row[0]):
            for _, row in data:
                if row and row[0]:
                    s = self.screens.setdefault(row[0], {"title": "", "purpose": "", "heading_id": ""})
                    s["title"] = s["title"] or " ".join(v for v in row[:2] if v)
                    s["purpose"] = s["purpose"] or (row[2] if len(row) > 2 else "")
            return
        prev: SpecEntry | None = None
        for i, (r, row) in enumerate(data, start=1):
            first = row[0] if row else ""
            rest = list(zip(header[1:], row[1:])) if header else [("", v) for v in row[1:]]
            body = "\n".join(f"{h}: {v}" if h else v for h, v in rest[1:] if v)
            if has_id and not first and prev:
                # ID 칸이 빈 행 = 바로 위 정책에 이어지는 내용
                extra = "\n".join(v for v in row[1:] if v)
                prev.body = (prev.body + "\n" + extra).strip()
                prev.extra_block_ids.append(r["id"])
                continue
            pid = first if has_id and POLICY_ID_RE.match(first) else None
            if has_id and not pid:
                self.warnings.append(f"ID 칸에 ID가 아닌 값: '{first[:20]}' ({section_path})")
            if pid and screen_id and not pid.startswith(screen_id):
                self.warnings.append(f"{pid}: 화면 {screen_id} 아래에 있음 (ID 오타일 수 있음)")
            name = rest[0][1] if rest else ""
            if not has_id:  # ID 없는 표(공통 규칙 등): 첫 칸이 이름
                name = first
                body = "\n".join(f"{h}: {v}" if h else v for h, v in rest if v)
            entry = SpecEntry(
                policy_id=pid, screen_id=screen_id, screen_title=screen_title, name=name, body=body,
                section_path=section_path + ("" if pid else f" #{i}"), block_id=r["id"], table_id=table["id"],
                parent_id=parent_id, page_id=self.page_id, last_edited=r.get("last_edited_time", ""),
                tags=tags, heading_colors=colors,
            )
            self.entries.append(entry)
            prev = entry

    def dedupe(self) -> None:
        """복사하면서 같은 내용이 두 번 들어간 경우: 하나만 남기고 나머지는 duplicate_block_ids로.
        내용이 다르면 둘 다 남기고 경고 (PM이 정리할 대상)."""
        groups: dict[str, list[SpecEntry]] = {}
        for e in self.entries:
            groups.setdefault(e.policy_id or e.section_path, []).append(e)
        keep: list[SpecEntry] = []
        for key, group in groups.items():
            if len(group) == 1:
                keep.append(group[0])
                continue
            # 접히는 제목 안(부모가 페이지가 아님)에 있는 쪽을 대표로
            group.sort(key=lambda e: e.parent_id == self.page_id)
            first = group[0]
            same = [e for e in group[1:] if _norm(e.body) == _norm(first.body) and e.name == first.name]
            differ = [e for e in group[1:] if e not in same]
            first.duplicate_block_ids += [e.block_id for e in same]
            keep.append(first)
            if differ:
                keep += differ
                self.warnings.append(f"{key}: 같은 ID에 내용이 다른 행 {len(group)}개 (충돌)")
        order = {id(e): i for i, e in enumerate(self.entries)}
        self.entries = sorted(keep, key=lambda e: order[id(e)])


def parse_tree(page: dict, blocks: list[dict]) -> SpecDocument:
    """page: pages.retrieve 응답, blocks: fetch_tree 결과 (자식은 block["children"]에 붙어 있음)."""
    p = _Parser(page["id"])
    p.walk(blocks, page["id"], [])
    p.dedupe()
    p.warnings = list(dict.fromkeys(p.warnings))  # 복사된 중복 행이 같은 경고를 또 내지 않게
    title =next((plain(v["title"]) for v in page.get("properties", {}).values() if v.get("type") == "title"), "")
    return SpecDocument(page["id"], page.get("url", ""), title, p.entries, p.screens, p.warnings)


async def fetch_tree(client, block_id: str) -> list[dict]:
    """블록 트리를 통째로 가져온다. 자식은 block["children"]에 붙인다. client: notion_client.AsyncClient"""
    out, cursor = [], None
    while True:
        kw = {"block_id": block_id, "page_size": 100}
        if cursor:
            kw["start_cursor"] = cursor
        res = await client.blocks.children.list(**kw)
        out += res["results"]
        if not res.get("has_more"):
            break
        cursor = res["next_cursor"]
    for b in out:
        if b.get("has_children") and b["type"] not in ("child_page", "child_database"):
            b["children"] = await fetch_tree(client, b["id"])
    return out


async def load_spec(client, page_id: str, cache_dir: str | Path) -> SpecDocument:
    """페이지 수정 시각이 캐시와 같으면 블록을 다시 가져오지 않는다 (명세서 하나가 수십 번의 API 호출)."""
    page = await client.pages.retrieve(page_id=page_id)
    path = Path(cache_dir) / f"{page['id'].replace('-', '')}.json"
    cached = None
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            cached = None
    if cached and cached.get("last_edited") == page.get("last_edited_time"):
        blocks = cached["blocks"]
    else:
        blocks = await fetch_tree(client, page["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_edited": page.get("last_edited_time"), "blocks": blocks},
                                   ensure_ascii=False), encoding="utf-8")
    return parse_tree(page, blocks)


def entry_dict(e: SpecEntry) -> dict:
    return asdict(e)
