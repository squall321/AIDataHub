"""Markdown → JSON 변환 핵심 로직.

설계 원칙:
- markdown-it-py 토큰 스트림 한 번 순회 (single pass).
- ``Section.blocks`` 가 본문 등장 순서를 보존한다.
- 표/그림은 본문 흐름에 ``ref`` 블록으로 삽입되며, 데이터는 최상위
  ``tables`` / ``figures`` / ``attachments`` 배열에 저장된다.
- 모든 그림은 ``attachments[kind=figure]`` 로 등록되며, 동시에 ``figures[]``
  에도 캡션·section_ref 와 함께 등록된다 (Word/PPT 변환기와 동일 모양).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from markdown_it import MarkdownIt
from mdit_py_plugins.front_matter import front_matter_plugin

# 변환 모델은 Word 변환기 모델을 그대로 재사용 → 출력 JSON 스키마 통일.
from converter.models import (
    Attachment,
    Block,
    ConversionResult,
    Figure,
    Section,
    Table,
)
from converter.core import _apply_agent_discovery_defaults

from .parser import (
    extract_section_id_from_heading,
    infer_attachment_kind_from_url,
    inline_to_text,
    is_absolute_url,
    parse_figure_caption,
    parse_yaml_front_matter,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

@dataclass
class MarkdownConverterOptions:
    """변환기 옵션."""

    team: str
    group: str
    year: int
    seq: int = 1
    output_dir: Path = field(default_factory=lambda: Path("output"))
    agents: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    extra_meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.team = self.team.upper()
        self.group = self.group.upper()
        if self.year < 1900 or self.year > 9999:
            raise ValueError(f"year out of range: {self.year}")
        if self.seq < 0:
            raise ValueError("seq must be >= 0")
        self.output_dir = Path(self.output_dir)


# ---------------------------------------------------------------------------
# ID helpers — DOC-{team}-{group}-{year}-{seq:06d}
# ---------------------------------------------------------------------------

def _make_doc_id(opts: MarkdownConverterOptions) -> str:
    return f"DOC-{opts.team}-{opts.group}-{opts.year}-{opts.seq:06d}"


def _make_fig_id(doc_id: str, n: int) -> str:
    return f"{doc_id}-F{n:03d}"


def _make_tbl_id(doc_id: str, n: int) -> str:
    return f"{doc_id}-T{n:03d}"


def _make_att_id(doc_id: str, n: int) -> str:
    return f"{doc_id}-A{n:03d}"


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------

class MarkdownConverter:
    """Markdown → DOC JSON 변환기."""

    def __init__(self, options: MarkdownConverterOptions) -> None:
        self.opts = options
        self.doc_id = _make_doc_id(options)
        self.warnings: list[str] = []

        self.section_root: list[Section] = []
        self.section_stack: list[Section] = []
        self.figures: list[Figure] = []
        self.tables: list[Table] = []
        self.attachments: list[Attachment] = []

        self.fig_counter = 0
        self.tbl_counter = 0
        self.att_counter = 0

        # 자동 섹션 번호
        self.l1_counter = 0
        self.l2_counter = 0
        self.l3_counter = 0

        # YAML front matter → meta override
        self.front_matter: dict[str, Any] = {}

        # markdown-it 인스턴스 (CommonMark + tables + front_matter + strike)
        self._md = (
            MarkdownIt("commonmark", {"html": True, "breaks": False})
            .enable("table")
            .enable("strikethrough")
            .use(front_matter_plugin)
        )

    # ---- 외부 API ----

    def convert_text(self, md_text: str, *, source_file: str = "input.md") -> ConversionResult:
        """Markdown 문자열을 변환해 ConversionResult 반환."""
        tokens = self._md.parse(md_text)
        self._process_tokens(tokens, md_text)
        meta = self._build_meta(source_file=source_file)
        return ConversionResult(
            schema_version="1.0",
            meta=meta,
            sections=self.section_root,
            figures=self.figures,
            tables=self.tables,
            sources=[],
            attachments=self.attachments,
            warnings=self.warnings,
        )

    def convert(self, md_path: str | Path) -> ConversionResult:
        md_path = Path(md_path)
        text = md_path.read_text(encoding="utf-8")
        return self.convert_text(text, source_file=md_path.name)

    # ---- 토큰 처리 ----

    def _process_tokens(self, tokens: list[Any], md_text: str) -> None:
        """토큰 목록을 순서대로 소비하며 섹션/블록을 구축."""
        i = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]
            ttype = tok.type

            if ttype == "front_matter":
                self.front_matter = parse_yaml_front_matter(tok.content or "")
                i += 1
                continue

            if ttype == "heading_open":
                tag = tok.tag  # 'h1' .. 'h6'
                level = int(tag[1])
                # heading_open / inline / heading_close
                if i + 1 < n and tokens[i + 1].type == "inline":
                    raw = inline_to_text(tokens[i + 1])
                else:
                    raw = ""
                # h1-h3 → 새 섹션, h4-h6 → 본문 단락 (level 3 content)
                if 1 <= level <= 3:
                    self._open_section(level, raw)
                else:
                    self._ensure_section()
                    self.section_stack[-1].blocks.append(
                        Block(type="paragraph", text=raw, marker=f"h{level} ")
                    )
                # heading_close 까지 스킵
                while i < n and tokens[i].type != "heading_close":
                    i += 1
                i += 1
                continue

            if ttype == "paragraph_open":
                # paragraph_open / inline / paragraph_close
                inline_tok = tokens[i + 1] if i + 1 < n else None
                self._handle_paragraph(inline_tok)
                while i < n and tokens[i].type != "paragraph_close":
                    i += 1
                i += 1
                continue

            if ttype == "fence" or ttype == "code_block":
                lang = (tok.info or "").strip() if ttype == "fence" else ""
                content = tok.content or ""
                self._handle_code(content, lang)
                i += 1
                continue

            if ttype == "blockquote_open":
                end = self._find_close(tokens, i, "blockquote_open", "blockquote_close")
                inner = tokens[i + 1 : end]
                self._handle_blockquote(inner)
                i = end + 1
                continue

            if ttype == "bullet_list_open":
                end = self._find_close(tokens, i, "bullet_list_open", "bullet_list_close")
                self._handle_list(tokens[i + 1 : end], ordered=False)
                i = end + 1
                continue

            if ttype == "ordered_list_open":
                end = self._find_close(tokens, i, "ordered_list_open", "ordered_list_close")
                self._handle_list(tokens[i + 1 : end], ordered=True)
                i = end + 1
                continue

            if ttype == "table_open":
                end = self._find_close(tokens, i, "table_open", "table_close")
                self._handle_table(tokens[i : end + 1])
                i = end + 1
                continue

            if ttype == "hr":
                # 수평선 — 단락 구분자. 무시.
                i += 1
                continue

            if ttype == "html_block":
                # 원시 HTML 블록 → 평문 단락으로 보존
                txt = (tok.content or "").strip()
                if txt:
                    self._ensure_section()
                    self.section_stack[-1].blocks.append(
                        Block(type="paragraph", text=txt)
                    )
                i += 1
                continue

            # 알려지지 않은 토큰은 무시
            i += 1

    @staticmethod
    def _find_close(tokens: list[Any], start: int, open_t: str, close_t: str) -> int:
        """``open_t`` 토큰 위치(start)에 대응하는 ``close_t`` 인덱스 반환."""
        depth = 0
        for j in range(start, len(tokens)):
            t = tokens[j].type
            if t == open_t:
                depth += 1
            elif t == close_t:
                depth -= 1
                if depth == 0:
                    return j
        return len(tokens) - 1

    # ---- 단락 / 그림 처리 ----

    def _handle_paragraph(self, inline_tok: Optional[Any]) -> None:
        if inline_tok is None or inline_tok.type != "inline":
            return

        # 단락 안의 image 토큰 → attachment 로 추출
        images = self._collect_images(inline_tok)
        text_only = inline_to_text(inline_tok).strip()

        # case 1: 단락이 image 만으로 구성됨 (text_only 가 alt 만)
        # case 2: 텍스트와 이미지가 혼재
        if images:
            for alt, src, title in images:
                self._add_figure_attachment(alt=alt, url=src, title=title)
            # 텍스트가 추가로 있다면 단락으로도 등록 (alt 만 있는 단락은 제외)
            non_image_text = self._strip_image_alts(text_only, images)
            if non_image_text.strip():
                # Figure caption 패턴이면 직전 figure 의 캡션으로 교체
                if self._maybe_replace_caption(non_image_text):
                    return
                self._ensure_section()
                self.section_stack[-1].blocks.append(
                    Block(type="paragraph", text=non_image_text.strip())
                )
            return

        if not text_only:
            return

        # Figure caption 패턴이면 직전 figure 의 캡션으로 교체
        if self._maybe_replace_caption(text_only):
            return

        self._ensure_section()
        self.section_stack[-1].blocks.append(Block(type="paragraph", text=text_only))

    def _strip_image_alts(self, text: str, images: list[tuple[str, str, str]]) -> str:
        """inline_to_text 가 image 의 alt 도 텍스트에 포함시키므로 제거."""
        result = text
        for alt, _src, _title in images:
            if alt and alt in result:
                # 첫 등장만 제거 (정확하진 않지만 보통 단순 케이스)
                result = result.replace(alt, "", 1)
        return result

    @staticmethod
    def _collect_images(inline_tok: Any) -> list[tuple[str, str, str]]:
        """inline 토큰의 children 에서 image 토큰 수집 → [(alt, src, title), ...]."""
        out: list[tuple[str, str, str]] = []
        children = getattr(inline_tok, "children", None) or []
        for ch in children:
            if ch.type == "image":
                src = ""
                title = ""
                attrs = ch.attrs or {}
                if isinstance(attrs, dict):
                    src = attrs.get("src", "") or ""
                    title = attrs.get("title", "") or ""
                else:
                    for k, v in attrs:
                        if k == "src":
                            src = v
                        elif k == "title":
                            title = v
                # alt 는 children 의 text 합성
                alt_parts: list[str] = []
                for c in (ch.children or []):
                    if c.type == "text":
                        alt_parts.append(c.content or "")
                alt = "".join(alt_parts)
                out.append((alt, src, title))
        return out

    def _add_figure_attachment(self, *, alt: str, url: str, title: str) -> None:
        """이미지 1개 → figure + attachment(kind=figure) 등록."""
        self._ensure_section()
        section_ref = self.section_stack[-1].id

        self.fig_counter += 1
        fig_id = _make_fig_id(self.doc_id, self.fig_counter)
        caption = (alt or title or "").strip()
        if not caption:
            caption = f"Figure {self.fig_counter}: (캡션 누락 — 검수 필요)"
            self.warnings.append(f"그림 {self.fig_counter}: alt 텍스트 없음 (캡션 누락)")

        fig = Figure(
            id=fig_id,
            number=self.fig_counter,
            caption=caption,
            section_ref=section_ref,
        )

        # URL 처리: 절대 URL 은 보존, 상대 경로는 file_path 후보로 유지
        absolute = is_absolute_url(url)
        if not absolute and url:
            # 상대 경로 — 그대로 두되 정적 마운트 직하 경로로 정규화하지는 않는다.
            # (실제 파일 복사는 파이프라인 후단에서 처리. 변환기는 URL 만 보존.)
            fig.image_path = url
        self.figures.append(fig)

        # 동일 그림을 attachment(kind=figure) 로도 등록
        self.att_counter += 1
        att_id = _make_att_id(self.doc_id, self.att_counter)
        kind = infer_attachment_kind_from_url(url) if url else "figure"
        # MD 의 ![]() 는 항상 그림으로 간주 — kind 는 figure 로 고정.
        if kind not in ("figure", "other"):
            # .pdf 같은 경우 figure 가 아닐 수 있으나, MD 그림 문법은 figure 로 고정.
            pass
        att = Attachment(
            id=att_id,
            number=self.att_counter,
            kind="figure",
            caption=caption,
            section_ref=section_ref,
            extra={"figure_ref": fig_id},
        )
        if absolute:
            att.extra["url"] = url
        elif url:
            att.file_path = url   # POSIX-style (markdown 에서 사용된 경로 그대로)
            att.file_name = url.rsplit("/", 1)[-1]
        if title:
            att.extra["title"] = title
        self.attachments.append(att)

        # blocks 흐름에 figure 위치 표시
        cur = self.section_stack[-1]
        cur.blocks.append(Block(type="figure", ref=fig_id))
        if fig_id not in cur.figure_refs:
            cur.figure_refs.append(fig_id)

    def _maybe_replace_caption(self, text: str) -> bool:
        """단락 텍스트가 ``Figure N: ...`` 패턴이면 직전 figure 의 캡션으로 교체."""
        if not self.figures:
            return False
        cap = parse_figure_caption(text)
        if not cap:
            return False
        last = self.figures[-1]
        if "(캡션 누락" in last.caption or last.caption == "":
            last.caption = cap
            # attachments 의 동일 figure_ref 도 동기화
            for att in self.attachments:
                if att.kind == "figure" and att.extra.get("figure_ref") == last.id:
                    att.caption = cap
                    break
            return True
        # 직전 figure 에 이미 캡션이 있으면 단락으로 그대로 둠
        return False

    # ---- 코드 블록 ----

    def _handle_code(self, content: str, lang: str) -> None:
        self._ensure_section()
        block = Block(type="code", text=content.rstrip("\n"))
        if lang:
            # marker 필드를 빌려 lang 정보를 보존 (스키마는 marker 만 인정)
            block.marker = f"lang:{lang}"
        self.section_stack[-1].blocks.append(block)

    # ---- 블록 인용 ----

    def _handle_blockquote(self, inner_tokens: list[Any]) -> None:
        """blockquote 내부 토큰을 단락(들)로 펼쳐 marker='> ' 부여."""
        i = 0
        while i < len(inner_tokens):
            tok = inner_tokens[i]
            if tok.type == "paragraph_open":
                if i + 1 < len(inner_tokens) and inner_tokens[i + 1].type == "inline":
                    text = inline_to_text(inner_tokens[i + 1]).strip()
                    if text:
                        self._ensure_section()
                        self.section_stack[-1].blocks.append(
                            Block(type="paragraph", text=text, marker="> ")
                        )
                while i < len(inner_tokens) and inner_tokens[i].type != "paragraph_close":
                    i += 1
                i += 1
            else:
                i += 1

    # ---- 리스트 ----

    def _handle_list(self, inner_tokens: list[Any], *, ordered: bool) -> None:
        """list_item_open 들을 순회하며 list_item 블록 생성."""
        i = 0
        index = 1
        while i < len(inner_tokens):
            tok = inner_tokens[i]
            if tok.type == "list_item_open":
                # list_item 의 첫 paragraph 의 inline 만 텍스트로 사용 (단순화)
                end = self._find_close_local(inner_tokens, i, "list_item_open", "list_item_close")
                item_tokens = inner_tokens[i + 1 : end]
                text = self._extract_list_item_text(item_tokens)
                if text.strip():
                    self._ensure_section()
                    marker = f"{index}." if ordered else "•"
                    self.section_stack[-1].blocks.append(
                        Block(type="list_item", text=text.strip(), marker=marker)
                    )
                index += 1
                i = end + 1
            else:
                i += 1

    @staticmethod
    def _extract_list_item_text(tokens: list[Any]) -> str:
        """리스트 항목의 paragraph_open / inline / paragraph_close 의 텍스트를 합성."""
        parts: list[str] = []
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t.type == "paragraph_open":
                if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                    parts.append(inline_to_text(tokens[i + 1]))
                while i < len(tokens) and tokens[i].type != "paragraph_close":
                    i += 1
            i += 1
        return "\n".join(p for p in parts if p)

    @staticmethod
    def _find_close_local(tokens: list[Any], start: int, open_t: str, close_t: str) -> int:
        depth = 0
        for j in range(start, len(tokens)):
            t = tokens[j].type
            if t == open_t:
                depth += 1
            elif t == close_t:
                depth -= 1
                if depth == 0:
                    return j
        return len(tokens) - 1

    # ---- 표 ----

    def _handle_table(self, table_tokens: list[Any]) -> None:
        """table_open ... table_close 블록을 파싱해 Table 생성."""
        headers: list[str] = []
        rows: list[list[Any]] = []

        section: str = "head"  # head | body
        cur_row: list[str] = []
        in_cell = False
        cell_buf: list[str] = []

        for tok in table_tokens:
            t = tok.type
            if t == "thead_open":
                section = "head"
            elif t == "tbody_open":
                section = "body"
            elif t == "tr_open":
                cur_row = []
            elif t == "tr_close":
                if section == "head":
                    headers = list(cur_row)
                else:
                    rows.append(list(cur_row))
            elif t in ("th_open", "td_open"):
                in_cell = True
                cell_buf = []
            elif t in ("th_close", "td_close"):
                in_cell = False
                cur_row.append("".join(cell_buf).strip())
            elif t == "inline" and in_cell:
                cell_buf.append(inline_to_text(tok))

        if not headers and rows:
            # 헤더 없는 표 — 첫 행을 헤더로 강제
            headers = rows.pop(0)
            self.warnings.append("표 헤더 없음 → 첫 행을 헤더로 강제")

        if not headers:
            self.warnings.append("표가 비어 있음")
            return

        self.tbl_counter += 1
        self._ensure_section()
        section_ref = self.section_stack[-1].id
        tbl_id = _make_tbl_id(self.doc_id, self.tbl_counter)

        # 캡션은 MD 표에는 직접 없음 — 자동 생성
        caption = f"Table {self.tbl_counter}"
        tbl = Table(
            id=tbl_id,
            number=self.tbl_counter,
            caption=caption,
            section_ref=section_ref,
            headers=headers,
            rows=rows,
        )
        self.tables.append(tbl)

        cur = self.section_stack[-1]
        cur.blocks.append(Block(type="table", ref=tbl_id))
        if tbl_id not in cur.table_refs:
            cur.table_refs.append(tbl_id)

    # ---- 섹션 ----

    def _open_section(self, level: int, heading_text: str) -> None:
        parsed_id, title = extract_section_id_from_heading(heading_text)
        auto_id = self._next_auto_id(level)
        section_id = parsed_id or auto_id
        if parsed_id and parsed_id != auto_id:
            self.warnings.append(
                f"섹션 번호 불일치: 본문='{parsed_id}', 자동={auto_id}. 본문 값 사용."
            )

        section = Section(id=section_id, level=level, title=title)
        while self.section_stack and self.section_stack[-1].level >= level:
            self.section_stack.pop()

        if not self.section_stack:
            self.section_root.append(section)
        else:
            self.section_stack[-1].children.append(section)
        self.section_stack.append(section)

    def _next_auto_id(self, level: int) -> str:
        if level == 1:
            self.l1_counter += 1
            self.l2_counter = 0
            self.l3_counter = 0
            return f"{self.l1_counter}"
        if level == 2:
            self.l2_counter += 1
            self.l3_counter = 0
            return f"{self.l1_counter}.{self.l2_counter}"
        self.l3_counter += 1
        return f"{self.l1_counter}.{self.l2_counter}.{self.l3_counter}"

    def _ensure_section(self) -> None:
        if self.section_stack:
            return
        self.warnings.append("문서 시작에 # Heading 1 없음 → 가상 '본문' 섹션 추가")
        s = Section(id="1", level=1, title="본문")
        self.section_root.append(s)
        self.section_stack.append(s)
        self.l1_counter = 1

    # ---- meta ----

    def _build_meta(self, *, source_file: str) -> dict[str, Any]:
        fm = self.front_matter or {}
        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

        # title: front_matter.title 우선 → 첫 h1 의 제목
        title = fm.get("title")
        if not title and self.section_root:
            title = self.section_root[0].title
        if not title:
            title = Path(source_file).stem

        # tags: front_matter > options > []
        tags = fm.get("tags") if isinstance(fm.get("tags"), list) else None
        if not tags:
            tags = list(self.opts.tags) if self.opts.tags else []

        # agents
        agents = fm.get("agents") if isinstance(fm.get("agents"), list) else None
        if not agents:
            agents = list(self.opts.agents) if self.opts.agents else []

        summary = fm.get("summary") or ""
        author = fm.get("author") or ""

        meta: dict[str, Any] = {
            "doc_id": self.doc_id,
            "title": str(title),
            "source_format": "md",
            "source_file": source_file,
            "doc_type": fm.get("doc_type", "manual"),
            "created": fm.get("created") or now,
            "modified": fm.get("modified") or now,
            "author": author,
            "department": f"{self.opts.team}-{self.opts.group}",
            "version": str(fm.get("version", "1.0")),
            "tags": tags,
            "summary": summary,
        }
        if agents:
            meta["agent_scope"] = agents

        # 임의의 front matter 잔여 키도 보존 (classification, status 등)
        reserved = {
            "title", "tags", "agents", "summary", "author", "doc_type",
            "created", "modified", "version",
        }
        leftovers = {k: v for k, v in fm.items() if k not in reserved and v is not None}
        if leftovers:
            meta["front_matter_extra"] = leftovers

        # CLI extra_meta 가 있으면 덮어쓰기
        for k, v in (self.opts.extra_meta or {}).items():
            meta[k] = v

        # Migration 0007: agent-discovery 자동 기본값. front_matter / extra_meta
        # 에서 명시적으로 준 값은 위 로직에서 이미 meta 에 들어왔으므로 헬퍼는
        # 빈 자리만 채운다.
        overrides_for_agent: dict[str, Any] = {}
        for k in ("agent_hints", "related_record_ids", "query_examples", "access_pattern"):
            if k in fm and fm.get(k) is not None:
                overrides_for_agent[k] = fm[k]
            if k in (self.opts.extra_meta or {}):
                overrides_for_agent[k] = self.opts.extra_meta[k]
        _apply_agent_discovery_defaults(
            meta,
            overrides=overrides_for_agent,
            data_type_name="Markdown 문서",
            title=str(title),
            tags=meta["tags"],
            section_count=len(self.section_root),
            table_count=len(self.tables),
            figure_count=len(self.figures),
        )

        if not meta["tags"]:
            self.warnings.append("front matter / CLI 에 tags 없음 → tags 비어 있음")
        if not meta["summary"]:
            self.warnings.append("front matter 에 summary 없음 → summary 비어 있음")

        return meta


# ---------------------------------------------------------------------------
# Output writer
# ---------------------------------------------------------------------------

def write_output(
    result: ConversionResult,
    output_dir: Path,
) -> tuple[Path, Path]:
    """JSON 과 경고 로그 저장. (json_path, warnings_path) 반환."""
    output_dir.mkdir(parents=True, exist_ok=True)

    doc_id = result.meta["doc_id"]
    json_path = output_dir / f"{doc_id}.json"
    log_path = output_dir / f"{doc_id}.warnings.log"

    json_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if result.warnings:
        log_path.write_text(
            "\n".join(f"[WARN] {w}" for w in result.warnings),
            encoding="utf-8",
        )
    elif log_path.exists():
        log_path.unlink()

    return json_path, log_path


__all__ = [
    "MarkdownConverter",
    "MarkdownConverterOptions",
    "write_output",
]
