"""PDF(.pdf) → DOC JSON 변환기 단위 테스트.

작은 in-memory PDF 를 ``reportlab`` 으로 생성해 핵심 동작을 검증한다.
``reportlab`` 은 dev-only 의존이며 main requirements 에는 포함하지 않는다.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

# reportlab 은 테스트 전용 fixture 작성에 사용됨 — 부재 시 모듈 단위 skip.
reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table as RLTable,
    TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet  # noqa: E402
from reportlab.lib import colors  # noqa: E402

from pdf_converter.core import PdfConverter, PdfConverterOptions  # noqa: E402
from pdf_converter.parser import (  # noqa: E402
    SECTION_NUM_PATTERN,
    extract_pdf_metadata,
    extract_outline,
    parse_pdf_date,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def opts(tmp_path):
    return PdfConverterOptions(
        team="HE",
        group="CAE",
        year=2026,
        seq=1,
        output_dir=tmp_path,
    )


def _convert(pdf_path: Path, opts: PdfConverterOptions):
    converter = PdfConverter(opts)
    result = converter.convert(pdf_path)
    return result, result.to_dict()


# ---------------------------------------------------------------------------
# PDF 생성 헬퍼
# ---------------------------------------------------------------------------

def _make_simple_pdf(
    path: Path,
    *,
    title: str = "Sample",
    author: str = "qa-bot",
    subject: str = "PDF 변환 테스트 문서",
    keywords: str = "PDF,IGA,CAE",
    body_lines: list[str] | None = None,
) -> Path:
    """평문 본문만 있는 단일 페이지 PDF (캔버스 직접 그리기) — pypdf 메타 포함."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(title)
    c.setAuthor(author)
    c.setSubject(subject)
    c.setKeywords(keywords)
    c.setFont("Helvetica", 12)
    y = 800
    for line in body_lines or ["First paragraph line.", "Second paragraph line."]:
        c.drawString(72, y, line)
        y -= 18
    c.showPage()
    c.save()
    return path


def _make_pdf_with_outline(path: Path) -> Path:
    """PDF + outline (북마크) — pypdf PdfWriter 로 합성."""
    # 1) 본문 PDF 를 reportlab 으로 만든다 (multi-page).
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("With Outline")
    c.setAuthor("qa-bot")
    c.setSubject("Outline 기반 헤딩 테스트")
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, 800, "Chapter 1: Overview")
    c.setFont("Helvetica", 12)
    c.drawString(72, 770, "This is page 1 body text.")
    c.showPage()
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, 800, "Chapter 2: Details")
    c.setFont("Helvetica", 12)
    c.drawString(72, 770, "This is page 2 body text.")
    c.showPage()
    c.save()
    buf.seek(0)

    # 2) pypdf 로 outline 추가
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(buf)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_outline_item("Chapter 1: Overview", 0)
    writer.add_outline_item("Chapter 2: Details", 1)
    # /Info 도 보존
    writer.add_metadata(
        {
            "/Title": "With Outline",
            "/Author": "qa-bot",
            "/Subject": "Outline 기반 헤딩 테스트",
            "/Keywords": "outline,heading,test",
        }
    )
    with open(path, "wb") as f:
        writer.write(f)
    return path


def _make_pdf_with_pattern_headings(path: Path) -> Path:
    """outline 없음 — 본문 안에 ``1. 개요`` / ``1.1 ...`` 패턴 헤딩."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle("Pattern Headings")
    c.setAuthor("qa-bot")
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, 800, "1. Overview")
    c.setFont("Helvetica", 12)
    c.drawString(72, 770, "Overview body text describing scope.")
    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, 740, "1.1 Background")
    c.setFont("Helvetica", 12)
    c.drawString(72, 710, "Background body line.")
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, 670, "2. Method")
    c.setFont("Helvetica", 12)
    c.drawString(72, 640, "Method explanation paragraph.")
    c.showPage()
    c.save()
    return path


def _make_pdf_with_table(path: Path) -> Path:
    """본문 + 표 1개 — pdfplumber.extract_tables() 가 인식하도록 그리드 표."""
    doc = SimpleDocTemplate(str(path), pagesize=A4, title="Table PDF", author="qa-bot")
    styles = getSampleStyleSheet()
    story: list[Any] = []
    story.append(Paragraph("Table Demo", styles["Heading1"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Below is a small bill of materials.", styles["BodyText"]))
    story.append(Spacer(1, 12))
    data = [
        ["name", "qty", "unit"],
        ["bolt", "12", "ea"],
        ["nut", "24", "ea"],
        ["washer", "48", "ea"],
    ]
    tbl = RLTable(data, hAlign="LEFT")
    tbl.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )
    story.append(tbl)
    doc.build(story)
    return path


def _make_blank_pdf(path: Path) -> Path:
    """텍스트가 없는 빈 페이지 PDF (스캔 PDF 대용)."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle("Scanned-only")
    # 텍스트 없음 — 도형만 그림
    c.rect(72, 72, 100, 100)
    c.showPage()
    c.save()
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_basic_text_extraction(opts, tmp_path):
    pdf = _make_simple_pdf(
        tmp_path / "simple.pdf",
        body_lines=[
            "This is the first paragraph.",
            "This is the second paragraph.",
        ],
    )
    result, payload = _convert(pdf, opts)
    assert result.meta["doc_id"] == "DOC-HE-CAE-2026-0000000001"
    assert result.meta["source_format"] == "pdf"
    # 본문 라인이 paragraph blocks 로 변환됨
    # (헤딩 없으므로 가상 '본문' 섹션 1개)
    assert len(result.sections) == 1
    sec = result.sections[0]
    paragraphs = [b.text for b in sec.blocks if b.type == "paragraph"]
    assert any("first paragraph" in p for p in paragraphs)
    assert any("second paragraph" in p for p in paragraphs)


def test_outline_based_headings(opts, tmp_path):
    pdf = _make_pdf_with_outline(tmp_path / "outline.pdf")
    result, _ = _convert(pdf, opts)
    # heading_strategy = outline
    assert result.meta["pdf"]["heading_strategy"] == "outline"
    # 두 개의 최상위 섹션 (Chapter 1, Chapter 2)
    titles = [s.title for s in result.sections]
    assert any("Overview" in t or "Chapter 1" in t for t in titles)
    assert any("Details" in t or "Chapter 2" in t for t in titles)


def test_pattern_based_headings(opts, tmp_path):
    pdf = _make_pdf_with_pattern_headings(tmp_path / "pattern.pdf")
    result, _ = _convert(pdf, opts)
    strategy = result.meta["pdf"]["heading_strategy"]
    # outline 이 없으므로 pattern 또는 pattern+fontsize 가 채택됨.
    assert strategy in ("pattern+fontsize", "fontsize")
    # 두 개의 level-1 섹션 (1. Overview, 2. Method)
    section_ids = [s.id for s in result.sections]
    # 패턴이 정상 동작했다면 "1", "2" 가 추출됨.
    if strategy == "pattern+fontsize":
        assert "1" in section_ids
        assert "2" in section_ids


def test_table_extracted_via_pdfplumber(opts, tmp_path):
    pdf = _make_pdf_with_table(tmp_path / "table.pdf")
    result, _ = _convert(pdf, opts)
    # 최소 1개의 표가 추출됨
    assert len(result.tables) >= 1
    tbl = result.tables[0]
    # headers 의 첫 행이 "name", "qty", "unit" 중 하나라도 포함
    headers_lower = [h.lower() for h in tbl.headers]
    assert any(h in ("name", "qty", "unit") for h in headers_lower)
    # body 행이 1개 이상
    assert len(tbl.rows) >= 1
    # ID 형식: -T001
    assert tbl.id.endswith("-T001")
    # 어떤 섹션의 table_refs 에 연결됨
    found = False

    def _walk(secs):
        nonlocal found
        for s in secs:
            if tbl.id in s.table_refs:
                found = True
            _walk(s.children)

    _walk(result.sections)
    assert found


def test_metadata_from_info_dict(opts, tmp_path):
    pdf = _make_simple_pdf(
        tmp_path / "meta.pdf",
        title="Battery Crash Report",
        author="cae-group",
        subject="배터리 측면 충돌 결과 요약",
        keywords="battery,crash,LS-DYNA",
    )
    result, _ = _convert(pdf, opts)
    meta = result.meta
    assert meta["title"] == "Battery Crash Report"
    assert meta["author"] == "cae-group"
    assert meta["summary"] == "배터리 측면 충돌 결과 요약"
    # keywords → tags 병합
    assert "battery" in meta["tags"]
    assert "crash" in meta["tags"]
    assert "LS-DYNA" in meta["tags"]


def test_pdf_with_no_outline_falls_back_to_pattern(opts, tmp_path):
    pdf = _make_pdf_with_pattern_headings(tmp_path / "fallback.pdf")
    result, _ = _convert(pdf, opts)
    strategy = result.meta["pdf"]["heading_strategy"]
    # outline 없으므로 fontsize 또는 pattern+fontsize 로 떨어짐
    assert strategy != "outline"
    # 본문 라인 중 일부가 paragraph 로 들어가 있음
    all_paras: list[str] = []

    def _walk(secs):
        for s in secs:
            for b in s.blocks:
                if b.type == "paragraph" and b.text:
                    all_paras.append(b.text)
            _walk(s.children)

    _walk(result.sections)
    assert any("body" in p.lower() for p in all_paras)


def test_unsupported_pdf_features_emit_warning(opts, tmp_path):
    """텍스트가 없는 PDF (스캔 PDF 대용) → 경고 발생."""
    pdf = _make_blank_pdf(tmp_path / "blank.pdf")
    result, _ = _convert(pdf, opts)
    # 페이지에 텍스트 없음 → 경고
    joined = " ".join(result.warnings)
    assert ("텍스트" in joined and "스캔" in joined) or "OCR" in joined


# ---------------------------------------------------------------------------
# parser unit tests (helpers)
# ---------------------------------------------------------------------------

def test_parse_pdf_date_iso8601():
    assert parse_pdf_date("D:20260507142530+09'00'") == "2026-05-07T14:25:30+09:00"
    assert parse_pdf_date("D:20260507142530Z") == "2026-05-07T14:25:30Z"
    assert parse_pdf_date("D:20260507") == "2026-05-07T00:00:00"
    assert parse_pdf_date("") is None
    assert parse_pdf_date(None) is None
    assert parse_pdf_date("not a date") is None


def test_section_num_pattern():
    m = SECTION_NUM_PATTERN.match("1.2.3 작동 원리")
    assert m is not None
    assert m.group(1) == "1.2.3"
    assert m.group(2) == "작동 원리"
    # 깊이 4 도 매칭은 됨 (변환기가 collapse 처리)
    m4 = SECTION_NUM_PATTERN.match("1.2.3.4 deeper")
    assert m4 is not None
    # 점 없는 단순 평문은 매칭 안됨
    assert SECTION_NUM_PATTERN.match("just a paragraph") is None


def test_doc_id_format(opts, tmp_path):
    pdf = _make_simple_pdf(tmp_path / "id.pdf", body_lines=["body"])
    result, _ = _convert(pdf, opts)
    assert result.meta["doc_id"] == "DOC-HE-CAE-2026-0000000001"
    # sources 도 동일 prefix
    assert result.sources[0].id == "DOC-HE-CAE-2026-0000000001-S001"
