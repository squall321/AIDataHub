"""HTML → JSON 변환 핵심 로직.

설계 원칙:
- ``lxml.html`` 트리를 한 번 순회 (single pass).
- ``Section.blocks`` 가 본문 등장 순서를 보존한다.
- 표/그림은 본문 흐름에 ``ref`` 블록으로 삽입되며, 데이터는 최상위
  ``tables`` / ``figures`` / ``attachments`` 배열에 저장된다.
- 모든 그림은 ``attachments[kind=figure]`` 로 등록되며, 동시에 ``figures[]``
  에도 캡션·section_ref 와 함께 등록된다 (Word/PPT/MD 변환기와 동일 모양).
- ``<head>`` 의 ``<title>``, ``<meta name="description"|"author"|"keywords"|...>``
  를 meta 필드로 흡수한다 (MD 의 YAML front matter 와 동일 역할).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from lxml import etree, html as lxml_html

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

# md_converter 의 공통 헬퍼를 그대로 재사용 (헤딩 번호 / 캡션 / URL 추정).
from md_converter.parser import (
    extract_section_id_from_heading,
    infer_attachment_kind_from_url,
    is_absolute_url,
    parse_figure_caption,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

@dataclass
class HtmlConverterOptions:
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
# ID helpers — DOC-{team}-{group}-{year}-{seq:010d}
# ---------------------------------------------------------------------------

def _make_doc_id(opts: HtmlConverterOptions) -> str:
    return f"DOC-{opts.team}-{opts.group}-{opts.year}-{opts.seq:010d}"


def _make_fig_id(doc_id: str, n: int) -> str:
    return f"{doc_id}-F{n:03d}"


def _make_tbl_id(doc_id: str, n: int) -> str:
    return f"{doc_id}-T{n:03d}"


def _make_att_id(doc_id: str, n: int) -> str:
    return f"{doc_id}-A{n:03d}"


# ---------------------------------------------------------------------------
# Block-level tags we care about (in body)
# ---------------------------------------------------------------------------

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_LIST_TAGS = {"ul", "ol"}
_CODE_TAGS = {"pre"}            # <pre> 안의 <code> 텍스트만 추출
_QUOTE_TAGS = {"blockquote"}
_TABLE_TAGS = {"table"}
_FIGURE_TAGS = {"figure", "img"}
_PARA_TAGS = {"p", "div", "section", "article", "main"}

# inline 무시 (텍스트로 평탄화)
_INLINE_FORMAT_TAGS = {
    "b", "strong", "i", "em", "u", "s", "del", "ins", "small",
    "sub", "sup", "mark", "span", "abbr", "cite", "q",
}

_WS_RE = re.compile(r"\s+")


def _norm_ws(s: str) -> str:
    """공백 정규화: 연속 공백/개행 → 단일 공백."""
    if not s:
        return ""
    return _WS_RE.sub(" ", s).strip()


def _text_with_links(elem: etree._Element) -> str:
    """element 의 텍스트를 평문화하되, ``<a href>`` 는 ``[text](url)`` 보존,
    ``<code>`` 는 백틱으로 감싸기. 인라인 서식(b/i/em 등) 은 모두 무시.

    이미지(``<img>``) 는 attachment 로 별도 처리되므로 인라인 텍스트엔 alt 만
    포함. ``<br>`` 은 공백으로.
    """
    out: list[str] = []

    def _walk(node: etree._Element) -> None:
        tag = node.tag if isinstance(node.tag, str) else ""
        # element 자신의 head text
        if node.text:
            out.append(node.text)

        for child in node:
            ctag = child.tag if isinstance(child.tag, str) else ""
            ctag_l = ctag.lower()

            if ctag_l == "a":
                href = child.get("href", "") or ""
                inner = _text_with_links(child)  # 재귀
                if href:
                    out.append(f"[{inner}]({href})")
                else:
                    out.append(inner)
            elif ctag_l == "code":
                code_text = "".join(child.itertext())
                out.append(f"`{code_text}`")
            elif ctag_l == "br":
                out.append(" ")
            elif ctag_l == "img":
                alt = child.get("alt", "") or ""
                if alt:
                    out.append(alt)
            elif ctag_l in _INLINE_FORMAT_TAGS:
                _walk(child)   # 서식 태그는 평탄화
            else:
                # 알 수 없는 자식 — 텍스트만 평탄화
                _walk(child)

            # element 닫힌 후 tail
            if child.tail:
                out.append(child.tail)

    _walk(elem)
    return _norm_ws("".join(out))


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------

class HtmlConverter:
    """HTML → DOC JSON 변환기."""

    def __init__(self, options: HtmlConverterOptions) -> None:
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

        self.l1_counter = 0
        self.l2_counter = 0
        self.l3_counter = 0

        # head 메타
        self.head_meta: dict[str, Any] = {}

    # ---- 외부 API ----

    def convert_text(self, html_text: str, *, source_file: str = "input.html") -> ConversionResult:
        """HTML 문자열을 변환해 ConversionResult 반환."""
        # lxml.html.fromstring 은 fragment 도 받지만, 전체 문서면 head/body 분리해서 사용.
        try:
            tree = lxml_html.fromstring(html_text)
        except (etree.ParserError, ValueError) as e:
            raise ValueError(f"HTML 파싱 실패: {e}") from e

        # head 메타 추출
        self._extract_head_meta(tree)

        # body 결정: <body> 가 있으면 그것, 없으면 root 자체.
        body = tree.find(".//body")
        root = body if body is not None else tree

        # body 의 자식들을 순서대로 처리
        self._process_block(root)

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

    def convert(self, html_path: str | Path) -> ConversionResult:
        html_path = Path(html_path)
        text = html_path.read_text(encoding="utf-8")
        return self.convert_text(text, source_file=html_path.name)

    # ---- head 메타 추출 ----

    def _extract_head_meta(self, tree: etree._Element) -> None:
        # <title>
        t = tree.find(".//head/title")
        if t is not None and (t.text or "").strip():
            self.head_meta["title"] = (t.text or "").strip()

        # <meta name="..." content="...">
        for m in tree.iterfind(".//head/meta"):
            name = (m.get("name") or m.get("property") or "").strip().lower()
            content = (m.get("content") or "").strip()
            if not name or not content:
                continue
            # 표준 매핑
            if name == "description":
                self.head_meta["summary"] = content
            elif name == "author":
                self.head_meta["author"] = content
            elif name == "keywords":
                # 콤마/세미콜론 구분 → list
                parts = [p.strip() for p in re.split(r"[,;]", content) if p.strip()]
                if parts:
                    self.head_meta["tags"] = parts
            elif name in ("agents", "agent_scope", "agent-scope"):
                parts = [p.strip() for p in re.split(r"[,;]", content) if p.strip()]
                if parts:
                    self.head_meta["agents"] = parts
            elif name in ("classification", "status", "domain", "language",
                          "doc_type", "doc-type", "version"):
                key = name.replace("-", "_")
                self.head_meta[key] = content
            elif name in ("created", "modified"):
                self.head_meta[name] = content
            else:
                # 그 외 meta 는 head_meta_extra 에 보존
                extra = self.head_meta.setdefault("_extra", {})
                extra[name] = content

    # ---- 블록 처리 ----

    def _process_block(self, root: etree._Element) -> None:
        """root 의 자식 element 들을 순서대로 처리."""
        # iter children — 단, body 자체가 root 일 때 body 안의 첫 레벨만.
        # 단순화: 깊이 우선이 아닌 너비 우선 1단계 + 재귀로 들어가는 컨테이너 처리.
        for elem in root:
            if not isinstance(elem.tag, str):
                continue   # comment 등
            self._handle_element(elem)

    def _handle_element(self, elem: etree._Element) -> None:
        tag = elem.tag.lower()

        if tag in _HEADING_TAGS:
            level = int(tag[1])
            text = _text_with_links(elem)
            if 1 <= level <= 3:
                self._open_section(level, text)
            else:
                self._ensure_section()
                self.section_stack[-1].blocks.append(
                    Block(type="paragraph", text=text, marker=f"h{level} ")
                )
            return

        if tag == "p":
            text = _text_with_links(elem)
            self._handle_paragraph_text(text)
            # <p> 안에 <img> 가 있으면 별도 추출
            for img in elem.iter("img"):
                self._handle_img(img)
            return

        if tag in _LIST_TAGS:
            ordered = (tag == "ol")
            index = 1
            for li in elem.findall("./li"):
                text = _text_with_links(li)
                if text:
                    self._ensure_section()
                    marker = f"{index}." if ordered else "•"
                    self.section_stack[-1].blocks.append(
                        Block(type="list_item", text=text, marker=marker)
                    )
                index += 1
            return

        if tag in _CODE_TAGS:
            # <pre><code class="language-xxx">...</code></pre> 또는 <pre>...</pre>
            code_elem = elem.find("./code")
            target = code_elem if code_elem is not None else elem
            content = "".join(target.itertext())
            lang = ""
            if code_elem is not None:
                cls = code_elem.get("class", "") or ""
                m = re.search(r"language-([\w+\-]+)", cls)
                if m:
                    lang = m.group(1)
            self._handle_code(content, lang)
            return

        if tag in _QUOTE_TAGS:
            for child in elem:
                if not isinstance(child.tag, str):
                    continue
                if child.tag.lower() == "p":
                    text = _text_with_links(child)
                    if text:
                        self._ensure_section()
                        self.section_stack[-1].blocks.append(
                            Block(type="paragraph", text=text, marker="> ")
                        )
            # blockquote 가 직접 텍스트만 가지는 경우
            direct_text = (elem.text or "").strip()
            if direct_text and not list(elem):
                self._ensure_section()
                self.section_stack[-1].blocks.append(
                    Block(type="paragraph", text=_norm_ws(direct_text), marker="> ")
                )
            return

        if tag in _TABLE_TAGS:
            self._handle_table(elem)
            return

        if tag == "img":
            self._handle_img(elem)
            return

        if tag == "figure":
            # <figure> 안의 img + figcaption
            img = elem.find(".//img")
            figcap = elem.find(".//figcaption")
            cap_text = _text_with_links(figcap) if figcap is not None else ""
            if img is not None:
                self._handle_img(img, override_caption=cap_text)
            return

        if tag == "hr":
            return   # 수평선은 무시

        if tag in _PARA_TAGS:
            # 컨테이너 — 재귀 진입
            # 단, 직접 텍스트가 있으면 단락으로
            direct = (elem.text or "").strip()
            children = [c for c in elem if isinstance(c.tag, str)]
            if not children and direct:
                self._handle_paragraph_text(_norm_ws(direct))
                return
            # 자식 요소들을 다시 처리
            self._process_block(elem)
            # 컨테이너 직속 텍스트는 무시 (대부분 공백)
            return

        # 알 수 없는 블록 — 텍스트만 단락으로
        text = _text_with_links(elem)
        if text:
            self._handle_paragraph_text(text)

    # ---- 단락 / 그림 ----

    def _handle_paragraph_text(self, text: str) -> None:
        if not text:
            return
        # Figure caption 패턴이면 직전 figure 의 캡션으로 교체
        if self._maybe_replace_caption(text):
            return
        self._ensure_section()
        self.section_stack[-1].blocks.append(Block(type="paragraph", text=text))

    def _handle_img(self, img: etree._Element, *, override_caption: str = "") -> None:
        src = img.get("src", "") or ""
        alt = img.get("alt", "") or ""
        title = img.get("title", "") or ""
        self._add_figure_attachment(
            alt=override_caption or alt, url=src, title=title,
        )

    def _add_figure_attachment(self, *, alt: str, url: str, title: str) -> None:
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

        absolute = is_absolute_url(url)
        if not absolute and url:
            fig.image_path = url
        self.figures.append(fig)

        self.att_counter += 1
        att_id = _make_att_id(self.doc_id, self.att_counter)
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
            att.file_path = url
            att.file_name = url.rsplit("/", 1)[-1]
        if title:
            att.extra["title"] = title
        self.attachments.append(att)

        cur = self.section_stack[-1]
        cur.blocks.append(Block(type="figure", ref=fig_id))
        if fig_id not in cur.figure_refs:
            cur.figure_refs.append(fig_id)

    def _maybe_replace_caption(self, text: str) -> bool:
        if not self.figures:
            return False
        cap = parse_figure_caption(text)
        if not cap:
            return False
        last = self.figures[-1]
        if "(캡션 누락" in last.caption or last.caption == "":
            last.caption = cap
            for att in self.attachments:
                if att.kind == "figure" and att.extra.get("figure_ref") == last.id:
                    att.caption = cap
                    break
            return True
        return False

    # ---- 코드 ----

    def _handle_code(self, content: str, lang: str) -> None:
        self._ensure_section()
        block = Block(type="code", text=content.rstrip("\n"))
        if lang:
            block.marker = f"lang:{lang}"
        self.section_stack[-1].blocks.append(block)

    # ---- 표 ----

    def _handle_table(self, table_elem: etree._Element) -> None:
        headers: list[str] = []
        rows: list[list[Any]] = []

        # thead 우선, 없으면 첫 tr 가 헤더
        thead = table_elem.find("./thead")
        if thead is not None:
            for tr in thead.findall(".//tr"):
                cells: list[str] = []
                for cell in tr.findall("./th") + tr.findall("./td"):
                    cells.append(_text_with_links(cell))
                if cells:
                    headers = cells
                    break

        # tbody (또는 thead 없을 때 table 직속 tr)
        body_trs: list[etree._Element] = []
        tbody = table_elem.find("./tbody")
        if tbody is not None:
            body_trs = tbody.findall("./tr")
        else:
            body_trs = table_elem.findall("./tr")

        for tr in body_trs:
            ths = tr.findall("./th")
            tds = tr.findall("./td")
            if ths and not tds and not headers:
                # th 만 있는 첫 tr → 헤더
                headers = [_text_with_links(c) for c in ths]
                continue
            cells = [_text_with_links(c) for c in (tr.findall("./th") + tr.findall("./td"))]
            if cells:
                rows.append(cells)

        # 헤더 추출 실패 + rows 가 있다 → 첫 행을 헤더로 강제
        if not headers and rows:
            headers = rows.pop(0)
            self.warnings.append("표 헤더 없음 → 첫 행을 헤더로 강제")

        if not headers:
            self.warnings.append("표가 비어 있음")
            return

        self.tbl_counter += 1
        self._ensure_section()
        section_ref = self.section_stack[-1].id
        tbl_id = _make_tbl_id(self.doc_id, self.tbl_counter)

        # <caption> 이 있으면 캡션으로
        caption_elem = table_elem.find("./caption")
        if caption_elem is not None:
            caption = _text_with_links(caption_elem) or f"Table {self.tbl_counter}"
        else:
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
        self.warnings.append("문서 시작에 <h1> 없음 → 가상 '본문' 섹션 추가")
        s = Section(id="1", level=1, title="본문")
        self.section_root.append(s)
        self.section_stack.append(s)
        self.l1_counter = 1

    # ---- meta ----

    def _build_meta(self, *, source_file: str) -> dict[str, Any]:
        hm = self.head_meta or {}
        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

        title = hm.get("title")
        if not title and self.section_root:
            title = self.section_root[0].title
        if not title:
            title = Path(source_file).stem

        tags = hm.get("tags") if isinstance(hm.get("tags"), list) else None
        if not tags:
            tags = list(self.opts.tags) if self.opts.tags else []

        agents = hm.get("agents") if isinstance(hm.get("agents"), list) else None
        if not agents:
            agents = list(self.opts.agents) if self.opts.agents else []

        summary = hm.get("summary") or ""
        author = hm.get("author") or ""

        meta: dict[str, Any] = {
            "doc_id": self.doc_id,
            "title": str(title),
            "source_format": "html",
            "source_file": source_file,
            "doc_type": hm.get("doc_type", "manual"),
            "created": hm.get("created") or now,
            "modified": hm.get("modified") or now,
            "author": author,
            "department": f"{self.opts.team}-{self.opts.group}",
            "version": str(hm.get("version", "1.0")),
            "tags": tags,
            "summary": summary,
        }
        if agents:
            meta["agent_scope"] = agents

        # 임의의 head meta 잔여 키 보존
        reserved = {
            "title", "tags", "agents", "summary", "author", "doc_type",
            "created", "modified", "version", "_extra",
        }
        leftovers = {k: v for k, v in hm.items() if k not in reserved and v is not None}
        if leftovers:
            meta["head_meta_extra"] = leftovers
        if hm.get("_extra"):
            meta.setdefault("head_meta_extra", {}).update(hm["_extra"])

        for k, v in (self.opts.extra_meta or {}).items():
            meta[k] = v

        # Migration 0007: agent-discovery 자동 기본값.
        overrides_for_agent: dict[str, Any] = {}
        for k in ("agent_hints", "related_record_ids", "query_examples", "access_pattern"):
            if k in hm and hm.get(k) is not None:
                overrides_for_agent[k] = hm[k]
            if k in (self.opts.extra_meta or {}):
                overrides_for_agent[k] = self.opts.extra_meta[k]
        _apply_agent_discovery_defaults(
            meta,
            overrides=overrides_for_agent,
            data_type_name="HTML 문서",
            title=str(title),
            tags=meta["tags"],
            section_count=len(self.section_root),
            table_count=len(self.tables),
            figure_count=len(self.figures),
        )

        if not meta["tags"]:
            self.warnings.append("head meta / CLI 에 tags 없음 → tags 비어 있음")
        if not meta["summary"]:
            self.warnings.append("head meta 에 description 없음 → summary 비어 있음")

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
    "HtmlConverter",
    "HtmlConverterOptions",
    "write_output",
]
