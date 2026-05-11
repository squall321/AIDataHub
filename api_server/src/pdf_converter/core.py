"""PDF → JSON 변환 핵심 로직.

설계 원칙:
- pdfplumber 로 페이지를 순회하며 텍스트(라인 단위) + 표를 추출.
- pypdf 로 /Info(메타) 와 /Outlines(북마크) 를 읽는다.
- 헤딩 추론 우선순위:
    1) outline (북마크) 가 있으면 그것을 권위적 헤딩 소스로 사용.
    2) outline 이 없으면 패턴 (``1.2 제목``) + 폰트 크기 휴리스틱 결합.
- 섹션은 최대 3 레벨까지만 (그 이상은 본문 단락으로 collapse).
- 표는 ``page.extract_tables()`` 로 추출 → ``tables[]`` 등록 + 본문 흐름에 ref 블록.
- 그림은 ``page.images`` 메타로 베스트 노력 (실제 이미지 추출은 향후 OCR 통합과 함께).

ID 형식: ``DOC-{team}-{group}-{year}-{seq:010d}``
서브 ID: -F (figure) / -T (table) / -A (attachment).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# 변환 모델은 Word 변환기 모델을 그대로 재사용 → 출력 JSON 스키마 통일.
from converter.models import (
    Attachment,
    Block,
    ConversionResult,
    Figure,
    Section,
    Source,
    Table,
)
from converter.core import _apply_agent_discovery_defaults

from .parser import (
    HeadingCandidate,
    OutlineItem,
    SECTION_NUM_PATTERN,
    TextLine,
    extract_outline,
    extract_pdf_metadata,
    extract_text_with_layout,
    infer_headings_from_fontsize,
    infer_headings_from_pattern,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

@dataclass
class PdfConverterOptions:
    """PDF 변환기 옵션."""

    team: str
    group: str
    year: int
    seq: int = 1
    output_dir: Path = field(default_factory=lambda: Path("output"))
    agents: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    extra_meta: dict[str, Any] = field(default_factory=dict)

    # 폰트 크기 헤딩 휴리스틱 임계 (body_avg * ratio ≤ size 면 헤딩)
    fontsize_heading_ratio: float = 1.2

    # ---- OCR (S6) ------------------------------------------------------
    # ``ocr=True`` 면 텍스트 추출 결과가 비어있는 페이지를 대상으로 pytesseract OCR
    # 을 수행한다. 시스템에 tesseract 바이너리가 설치되어 있어야 한다.
    ocr: bool = False
    ocr_lang: str = "eng"
    ocr_dpi: int = 200

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

def _make_doc_id(opts: PdfConverterOptions) -> str:
    return f"DOC-{opts.team}-{opts.group}-{opts.year}-{opts.seq:010d}"


def _make_fig_id(doc_id: str, n: int) -> str:
    return f"{doc_id}-F{n:03d}"


def _make_tbl_id(doc_id: str, n: int) -> str:
    return f"{doc_id}-T{n:03d}"


def _make_att_id(doc_id: str, n: int) -> str:
    return f"{doc_id}-A{n:03d}"


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------

class PdfConverter:
    """PDF → DOC JSON 변환기.

    사용::

        opts = PdfConverterOptions(team="HE", group="CAE", year=2026)
        conv = PdfConverter(opts)
        result = conv.convert("input.pdf")
        write_output(result, opts.output_dir)
    """

    def __init__(self, options: PdfConverterOptions) -> None:
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

        self._pdf_meta: dict[str, Any] = {}
        self._page_count: int = 0

    # ---- 외부 API ----

    def convert(self, pdf_path: str | Path) -> ConversionResult:
        """PDF 파일을 변환해 ConversionResult 반환."""
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 파일을 찾을 수 없음: {pdf_path}")

        # 1) pypdf 로 메타 + outline 추출 (비밀번호 보호 PDF 거부)
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("pypdf 가 필요합니다. requirements.txt 참조.") from exc

        try:
            reader = PdfReader(str(pdf_path))
        except Exception as exc:
            raise RuntimeError(f"PDF 열기 실패: {exc}") from exc

        if getattr(reader, "is_encrypted", False):
            # 비밀번호 보호 PDF — 빈 비밀번호 시도, 실패하면 거부
            try:
                ok = reader.decrypt("")
                if not ok:
                    raise RuntimeError(
                        "암호로 보호된 PDF 입니다 — 변환 불가. 암호를 제거 후 재시도하세요."
                    )
            except Exception as exc:
                raise RuntimeError(
                    f"암호로 보호된 PDF 입니다 — 변환 불가: {exc}"
                ) from exc

        self._pdf_meta = extract_pdf_metadata(reader)
        outline_items = extract_outline(reader)
        self._page_count = len(reader.pages)

        # 2) pdfplumber 로 페이지 순회: 텍스트(라인) + 표
        try:
            import pdfplumber
        except ImportError as exc:
            raise RuntimeError("pdfplumber 가 필요합니다. requirements.txt 참조.") from exc

        all_lines: list[TextLine] = []
        per_page_tables: list[tuple[int, list[list[Any]]]] = []  # (page_no, table_data)
        per_page_image_count: list[tuple[int, int]] = []         # (page_no, image_count)

        with pdfplumber.open(str(pdf_path)) as pdoc:
            for idx, page in enumerate(pdoc.pages, start=1):
                # 텍스트 라인
                lines = extract_text_with_layout(page, page_number=idx)
                if not lines:
                    self.warnings.append(
                        f"페이지 {idx}: 추출된 텍스트 없음 (스캔 PDF / 이미지 PDF 가능성)"
                    )
                all_lines.extend(lines)

                # 표
                try:
                    tables = page.extract_tables() or []
                except Exception as exc:
                    tables = []
                    self.warnings.append(f"페이지 {idx}: 표 추출 실패 — {exc}")
                for tbl in tables:
                    if tbl:
                        per_page_tables.append((idx, tbl))

                # 이미지 수 (베스트 노력)
                try:
                    imgs = page.images or []
                except Exception:
                    imgs = []
                if imgs:
                    per_page_image_count.append((idx, len(imgs)))

        if not all_lines and self._page_count > 0:
            self.warnings.append(
                "전체 페이지에서 텍스트가 추출되지 않음 — 스캔 PDF 일 가능성. "
                "OCR 처리 후 재변환 권장 (--ocr 옵션 사용)."
            )

        # ---- S6. OCR fallback for scanned PDFs --------------------------
        if getattr(self.opts, "ocr", False):
            try:
                from .ocr import ocr_pdf as _ocr_pdf

                blank_pages = self._collect_blank_pages(all_lines)
                if blank_pages:
                    ocr_map = _ocr_pdf(
                        pdf_path,
                        lang=getattr(self.opts, "ocr_lang", "eng"),
                        dpi=getattr(self.opts, "ocr_dpi", 200),
                        only_pages=blank_pages,
                    )
                    if ocr_map:
                        self._merge_ocr_lines(all_lines, ocr_map)
                        self.warnings.append(
                            f"OCR 처리됨: {sorted(blank_pages)} 페이지"
                        )
                    else:
                        self.warnings.append(
                            "OCR 요청되었으나 의존성 미충족 — pytesseract / pdf2image / "
                            "tesseract 바이너리 설치 확인."
                        )
            except Exception as exc:  # noqa: BLE001
                self.warnings.append(f"OCR 실패: {exc}")

        # 3) 헤딩 추론 (outline 우선 → 패턴 + 폰트 크기 결합)
        headings: list[HeadingCandidate]
        strategy: str
        if outline_items:
            headings = self._headings_from_outline(outline_items, all_lines)
            strategy = "outline"
        else:
            pat = infer_headings_from_pattern(all_lines)
            font = infer_headings_from_fontsize(
                all_lines, threshold_ratio=self.opts.fontsize_heading_ratio
            )
            headings = self._merge_pattern_and_fontsize(pat, font, all_lines)
            strategy = "pattern+fontsize" if pat else "fontsize"
            if not headings:
                self.warnings.append(
                    "헤딩 후보 없음 — outline 없음, 패턴/폰트 크기 휴리스틱 모두 실패. "
                    "전체 본문이 하나의 가상 섹션으로 묶임."
                )
                strategy = "none"
        self._heading_strategy = strategy

        # 4) 본문 흐름을 빌드: 라인 시퀀스를 따라가며 헤딩이면 섹션 열고
        #    아니면 paragraph block 누적. 표는 페이지 순회 후 일괄 등록.
        self._build_sections_from_lines(all_lines, headings)

        # 5) 표 등록 — section_ref 는 표가 발견된 페이지의 마지막 활성 섹션
        for page_no, tbl_data in per_page_tables:
            self._add_table(page_no, tbl_data)

        # 6) 이미지 정보를 attachment(kind=figure) 로 등록 (베스트 노력 — 캡션 없음)
        for page_no, count in per_page_image_count:
            for _ in range(count):
                self._add_image_placeholder(page_no)

        # 7) meta 빌드
        meta = self._build_meta(source_file=pdf_path.name)

        # 8) sources 에 PDF 자체 등록
        sources = self._build_sources(pdf_path)

        return ConversionResult(
            schema_version="1.0",
            meta=meta,
            sections=self.section_root,
            figures=self.figures,
            tables=self.tables,
            sources=sources,
            attachments=self.attachments,
            warnings=self.warnings,
        )

    # ---- outline → HeadingCandidate ----

    def _headings_from_outline(
        self,
        outline: list[OutlineItem],
        all_lines: list[TextLine],
    ) -> list[HeadingCandidate]:
        """outline 의 각 항목에 대해, 가장 가까운 텍스트 라인을 매칭.

        매칭 실패해도 outline 항목은 유지 (page-only 헤딩).
        """
        out: list[HeadingCandidate] = []
        # title 정규화 → page 단위로 라인 매핑 미리
        lines_by_page: dict[int, list[TextLine]] = {}
        for ln in all_lines:
            lines_by_page.setdefault(ln.page_number, []).append(ln)

        for item in outline:
            level = min(max(item.level, 1), 3)
            page_no = (item.page or 0) + 1 if item.page is not None else 1
            title_norm = item.title.strip()
            section_id: Optional[str] = None
            m = SECTION_NUM_PATTERN.match(title_norm)
            if m:
                section_id = m.group(1)
                title_norm = m.group(2).strip()
            cand = HeadingCandidate(
                text=item.title.strip(),
                page_number=page_no,
                level=level,
                source="outline",
                section_id=section_id,
                extra={"title": title_norm},
            )
            out.append(cand)
        return out

    @staticmethod
    def _merge_pattern_and_fontsize(
        pattern_cands: list[HeadingCandidate],
        font_cands: list[HeadingCandidate],
        all_lines: list[TextLine],
    ) -> list[HeadingCandidate]:
        """패턴 후보를 우선하고, 폰트 크기 후보 중 패턴과 겹치지 않는 것만 추가.

        같은 (page_number, text) 가 양쪽에 있으면 패턴 후보를 채택 (level 정확).
        """
        if not pattern_cands and not font_cands:
            return []

        seen: set[tuple[int, str]] = set()
        merged: list[HeadingCandidate] = []
        for c in pattern_cands:
            key = (c.page_number, c.text.strip())
            if key in seen:
                continue
            seen.add(key)
            merged.append(c)
        for c in font_cands:
            key = (c.page_number, c.text.strip())
            if key in seen:
                continue
            # 패턴 후보가 동일 텍스트면 스킵 — 위 루프에서 이미 등록됨.
            # font 후보는 패턴이 없는 큰 글씨 헤딩 (예: 표지 제목, 장 제목).
            seen.add(key)
            merged.append(c)
        # all_lines 등장 순서대로 정렬 — 본문 흐름과 매칭하려면 (page, y_top) 순서.
        line_order: dict[tuple[int, str], int] = {}
        for i, ln in enumerate(all_lines):
            line_order.setdefault((ln.page_number, ln.text.strip()), i)
        merged.sort(key=lambda c: line_order.get((c.page_number, c.text.strip()), 1 << 30))
        return merged

    # ---- OCR 헬퍼 (S6) ----

    def _collect_blank_pages(self, all_lines: list[TextLine]) -> set[int]:
        """텍스트 라인이 거의 없는 페이지를 set 으로 반환 (OCR 후보)."""
        per_page_chars: dict[int, int] = {}
        for ln in all_lines:
            per_page_chars[ln.page_number] = (
                per_page_chars.get(ln.page_number, 0) + len(ln.text or "")
            )
        blanks: set[int] = set()
        for page_no in range(1, self._page_count + 1):
            if per_page_chars.get(page_no, 0) < 5:
                blanks.add(page_no)
        return blanks

    def _merge_ocr_lines(
        self,
        all_lines: list[TextLine],
        ocr_map: dict[int, str],
    ) -> None:
        """OCR 으로 얻은 페이지 텍스트를 ``TextLine`` 으로 합성해 합쳐넣는다."""
        for page_no, raw in ocr_map.items():
            if not raw or not raw.strip():
                continue
            y = 0.0
            for line in raw.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                all_lines.append(
                    TextLine(
                        text=stripped,
                        avg_font_size=10.0,  # OCR 은 폰트 크기 정보 없음 → 본문 평균치
                        page_number=int(page_no),
                        y_top=y,
                    )
                )
                y += 12.0
        # 페이지 순서 유지를 위해 안정 정렬.
        all_lines.sort(key=lambda ln: (ln.page_number, ln.y_top))

    # ---- 본문 빌드 ----

    def _build_sections_from_lines(
        self,
        all_lines: list[TextLine],
        headings: list[HeadingCandidate],
    ) -> None:
        """라인 순서대로 순회하며 헤딩이면 섹션 열고, 그 외엔 단락 블록 추가."""
        # heading 식별 키: (page, text.strip())
        head_map: dict[tuple[int, str], HeadingCandidate] = {}
        for h in headings:
            head_map[(h.page_number, h.text.strip())] = h

        for ln in all_lines:
            key = (ln.page_number, ln.text.strip())
            h = head_map.pop(key, None)
            if h is not None:
                self._open_section(h)
                continue
            # 본문 단락
            text = ln.text.strip()
            if not text:
                continue
            self._ensure_section()
            self.section_stack[-1].blocks.append(
                Block(type="paragraph", text=text)
            )

        # outline 항목인데 라인 매칭 실패한 것들 — 별도 빈 섹션으로라도 등록
        for h in head_map.values():
            self._open_section(h)
            # 안내 단락
            self.section_stack[-1].blocks.append(
                Block(
                    type="paragraph",
                    text=f"(outline 헤딩이지만 본문 라인 매칭 실패 — 페이지 {h.page_number})",
                )
            )

    # ---- 섹션 ----

    def _open_section(self, h: HeadingCandidate) -> None:
        level = min(max(h.level, 1), 3)
        title = h.extra.get("title") if isinstance(h.extra, dict) else None
        if not title:
            title = h.text.strip()

        parsed_id = h.section_id
        auto_id = self._next_auto_id(level)
        section_id = parsed_id or auto_id
        if parsed_id and parsed_id != auto_id:
            # 충돌 — 본문 값을 우선 (Word/MD 와 동일 정책)
            self.warnings.append(
                f"섹션 번호 불일치 (페이지 {h.page_number}): "
                f"본문='{parsed_id}', 자동={auto_id}. 본문 값 사용."
            )

        section = Section(id=section_id, level=level, title=str(title))
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
            if self.l1_counter == 0:
                self.l1_counter = 1
            self.l2_counter += 1
            self.l3_counter = 0
            return f"{self.l1_counter}.{self.l2_counter}"
        if self.l1_counter == 0:
            self.l1_counter = 1
        if self.l2_counter == 0:
            self.l2_counter = 1
        self.l3_counter += 1
        return f"{self.l1_counter}.{self.l2_counter}.{self.l3_counter}"

    def _ensure_section(self) -> None:
        if self.section_stack:
            return
        self.warnings.append(
            "문서 시작에 헤딩 없음 → 가상 '본문' 섹션 추가"
        )
        s = Section(id="1", level=1, title="본문")
        self.section_root.append(s)
        self.section_stack.append(s)
        self.l1_counter = 1

    # ---- 표 ----

    def _add_table(self, page_no: int, raw_table: list[list[Any]]) -> None:
        """pdfplumber 의 raw 표 (list[list[str|None]]) 를 Table 로 등록."""
        # raw 표는 None 셀이 섞일 수 있음 → 빈 문자열로 정규화
        rows = [
            ["" if c is None else str(c).strip() for c in row]
            for row in raw_table
        ]
        rows = [r for r in rows if any(c for c in r)]
        if not rows:
            self.warnings.append(f"페이지 {page_no}: 빈 표 무시")
            return

        headers = rows[0]
        body = rows[1:] if len(rows) > 1 else []
        if not body:
            self.warnings.append(
                f"페이지 {page_no}: 표 데이터 행 없음 (헤더만 존재)"
            )

        self._ensure_section()
        section_ref = self.section_stack[-1].id
        self.tbl_counter += 1
        tbl_id = _make_tbl_id(self.doc_id, self.tbl_counter)
        caption = f"Table {self.tbl_counter} (page {page_no})"
        tbl = Table(
            id=tbl_id,
            number=self.tbl_counter,
            caption=caption,
            section_ref=section_ref,
            headers=headers,
            rows=body,
        )
        self.tables.append(tbl)
        cur = self.section_stack[-1]
        cur.blocks.append(Block(type="table", ref=tbl_id))
        if tbl_id not in cur.table_refs:
            cur.table_refs.append(tbl_id)

    # ---- 그림 (이미지 placeholder) ----

    def _add_image_placeholder(self, page_no: int) -> None:
        """페이지의 이미지 1장을 figure + attachment(kind=figure) 로 등록.

        실제 이미지 바이너리는 추출하지 않음 (베스트 노력) — 향후 OCR/이미지 추출 통합 시 보완.
        """
        self._ensure_section()
        section_ref = self.section_stack[-1].id

        self.fig_counter += 1
        fig_id = _make_fig_id(self.doc_id, self.fig_counter)
        caption = f"Figure {self.fig_counter} (page {page_no}: 캡션 누락 — 검수 필요)"
        self.warnings.append(
            f"페이지 {page_no}: PDF 이미지 발견 — 캡션 추론 미지원 (검수 필요)"
        )

        fig = Figure(
            id=fig_id,
            number=self.fig_counter,
            caption=caption,
            section_ref=section_ref,
        )
        self.figures.append(fig)

        self.att_counter += 1
        att_id = _make_att_id(self.doc_id, self.att_counter)
        att = Attachment(
            id=att_id,
            number=self.att_counter,
            kind="figure",
            caption=caption,
            section_ref=section_ref,
            extra={"figure_ref": fig_id, "page_number": page_no},
        )
        self.attachments.append(att)

        cur = self.section_stack[-1]
        cur.blocks.append(Block(type="figure", ref=fig_id))
        if fig_id not in cur.figure_refs:
            cur.figure_refs.append(fig_id)

    # ---- meta ----

    def _build_meta(self, *, source_file: str) -> dict[str, Any]:
        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        pdf_meta = self._pdf_meta or {}

        # title: /Info.Title > 첫 헤딩 > 파일명 (확장자 제거)
        title = pdf_meta.get("title") or ""
        if not title and self.section_root:
            title = self.section_root[0].title
        if not title:
            title = Path(source_file).stem
        title = str(title).strip()

        author = str(pdf_meta.get("author") or "").strip()

        # subject → summary, keywords → tags
        summary = str(pdf_meta.get("subject") or "").strip()
        keywords_raw = str(pdf_meta.get("keywords") or "").strip()
        kw_tags: list[str] = []
        if keywords_raw:
            for sep in [",", ";", "/", "|"]:
                if sep in keywords_raw:
                    kw_tags = [t.strip() for t in keywords_raw.split(sep) if t.strip()]
                    break
            if not kw_tags:
                kw_tags = [keywords_raw]
        tags = list(self.opts.tags) if self.opts.tags else []
        for t in kw_tags:
            if t not in tags:
                tags.append(t)

        created_iso = pdf_meta.get("creation_date") or None
        modified_iso = pdf_meta.get("modification_date") or None
        # ISO 8601 → date-only (YYYY-MM-DD) 로 단순화 (다른 변환기와 일관)
        def _iso_date(s: Optional[str]) -> str:
            if not s:
                return now
            return s[:10] if len(s) >= 10 else now

        meta: dict[str, Any] = {
            "doc_id": self.doc_id,
            "title": title,
            "source_format": "pdf",
            "source_file": source_file,
            "doc_type": "manual",
            "created": _iso_date(created_iso),
            "modified": _iso_date(modified_iso),
            "author": author,
            "department": f"{self.opts.team}-{self.opts.group}",
            "version": "1.0",
            "tags": tags,
            "summary": summary,
        }
        if self.opts.agents:
            meta["agent_scope"] = list(self.opts.agents)

        # pdf 전용 부가 메타
        pdf_extra: dict[str, Any] = {
            "page_count": self._page_count,
            "heading_strategy": getattr(self, "_heading_strategy", "unknown"),
        }
        if pdf_meta.get("creator"):
            pdf_extra["creator"] = pdf_meta["creator"]
        if pdf_meta.get("producer"):
            pdf_extra["producer"] = pdf_meta["producer"]
        if pdf_meta.get("creation_date"):
            pdf_extra["creation_date"] = pdf_meta["creation_date"]
        if pdf_meta.get("modification_date"):
            pdf_extra["modification_date"] = pdf_meta["modification_date"]
        meta["pdf"] = pdf_extra

        # CLI extra_meta 덮어쓰기
        for k, v in (self.opts.extra_meta or {}).items():
            meta[k] = v

        # Migration 0007: agent-discovery 자동 기본값.
        overrides_for_agent: dict[str, Any] = {}
        for k in ("agent_hints", "related_record_ids", "query_examples", "access_pattern"):
            if k in (self.opts.extra_meta or {}):
                overrides_for_agent[k] = self.opts.extra_meta[k]
        _apply_agent_discovery_defaults(
            meta,
            overrides=overrides_for_agent,
            data_type_name="PDF 문서",
            title=str(title),
            tags=meta["tags"],
            section_count=len(self.section_root),
            table_count=len(self.tables),
            figure_count=len(self.figures),
        )

        if not meta["tags"]:
            self.warnings.append("/Info.Keywords / CLI 에 tags 없음 → tags 비어 있음")
        if not meta["summary"]:
            self.warnings.append("/Info.Subject 없음 → summary 비어 있음")
        if not meta["author"]:
            self.warnings.append("/Info.Author 없음 → author 비어 있음")

        return meta

    # ---- sources ----

    def _build_sources(self, pdf_path: Path) -> list[Source]:
        try:
            size = pdf_path.stat().st_size
        except OSError:
            size = None
        src = Source(
            id=f"{self.doc_id}-S001",
            type="document",
            format="pdf",
            file_name=pdf_path.name,
            file_path=str(pdf_path).replace("\\", "/"),
            size_bytes=size,
            description="원본 PDF",
        )
        return [src]


# ---------------------------------------------------------------------------
# Output writer
# ---------------------------------------------------------------------------

def write_output(
    result: ConversionResult,
    output_dir: Path,
) -> tuple[Path, Path]:
    """JSON 과 경고 로그 저장. (json_path, warnings_path) 반환."""
    output_dir = Path(output_dir)
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
    "PdfConverter",
    "PdfConverterOptions",
    "write_output",
]
