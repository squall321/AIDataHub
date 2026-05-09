"""표준 PDF(.pdf) 예제 생성 스크립트.

생성되는 sample_doc.pdf 가 시연하는 원칙
(pdf_to_json_conversion_rules.md):
- /Info dict 의 Title/Author/Subject/Keywords (8장 — PDF 메타데이터 매핑)
- Outline(북마크) — heading_strategy = "outline" 으로 인식 (5.1 절)
- 검색 가능한 텍스트 (스캔 PDF 가 아님)
- 폰트 크기 차이로 헤딩과 본문 구분 (5.3 절 폰트 휴리스틱 폴백)
- 셀 기반 표 (이미지가 아님 — 6장 표 추출)

CAE/IGA 도메인 테마 — iga_guide.docx 와 일관.

실행 방법
--------
python _generate_pdf.py [출력경로]
기본 출력: 같은 폴더의 sample_doc.pdf
"""
from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas


# 한글 폰트 등록 (시스템에 맞는 CID 폰트 — 윈도우/리눅스 모두에서 동작)
pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
KOR_FONT = "HeiseiMin-W3"


def draw_heading_1(c: canvas.Canvas, x: float, y: float, text: str) -> float:
    """level 1 헤딩 — 큰 폰트(18pt)."""
    c.setFont(KOR_FONT, 18)
    c.drawString(x, y, text)
    return y - 28


def draw_heading_2(c: canvas.Canvas, x: float, y: float, text: str) -> float:
    """level 2 헤딩 — 중간 폰트(14pt)."""
    c.setFont(KOR_FONT, 14)
    c.drawString(x, y, text)
    return y - 22


def draw_body(c: canvas.Canvas, x: float, y: float, text: str) -> float:
    """본문 — 10pt."""
    c.setFont(KOR_FONT, 10)
    # 줄바꿈은 단순 split 으로 처리 (예제용, 충분한 폭이라 가정)
    for line in text.split("\n"):
        c.drawString(x, y, line)
        y -= 14
    return y - 6


def draw_table(
    c: canvas.Canvas,
    x: float,
    y: float,
    headers: list[str],
    rows: list[list[str]],
    col_w: float = 100.0,
    row_h: float = 18.0,
) -> float:
    """셀 기반 표 (이미지가 아닌, 그리드 라인을 가진 텍스트 표)."""
    n_cols = len(headers)
    table_w = col_w * n_cols
    n_rows = len(rows) + 1  # +1 for header

    # 그리드 라인
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)
    for r in range(n_rows + 1):
        ry = y - r * row_h
        c.line(x, ry, x + table_w, ry)
    for col in range(n_cols + 1):
        cx = x + col * col_w
        c.line(cx, y, cx, y - n_rows * row_h)

    # 헤더 텍스트 (볼드 대신 폰트만)
    c.setFont(KOR_FONT, 10)
    for j, h in enumerate(headers):
        c.drawString(x + j * col_w + 4, y - row_h + 5, h)
    # 데이터 행
    for i, row in enumerate(rows, start=1):
        for j, v in enumerate(row):
            c.drawString(x + j * col_w + 4, y - (i + 1) * row_h + 5, str(v))

    return y - (n_rows + 1) * row_h


def build_sample_pdf(out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out_path), pagesize=A4)

    # ── /Info dict ────────────────────────────────────────────
    c.setTitle("IGA 검토 보고서 (2026-04)")
    c.setAuthor("CAE팀")
    c.setSubject("IGA 해석 검증")
    c.setKeywords("IGA, NURBS, KooRemapper")

    page_w, page_h = A4
    left = 60
    top = page_h - 60

    # ── Page 1 ────────────────────────────────────────────────
    y = top
    # 1. 개요 (level 1)
    c.bookmarkPage("sec_1")
    c.addOutlineEntry("1. 개요", "sec_1", level=0, closed=False)
    y = draw_heading_1(c, left, y, "1. 개요")

    # 1.1 배경 (level 2)
    c.bookmarkPage("sec_1_1")
    c.addOutlineEntry("1.1 배경", "sec_1_1", level=1, closed=False)
    y = draw_heading_2(c, left, y, "1.1 배경")
    y = draw_body(
        c,
        left,
        y,
        "이 문서는 KooRemapper 의 IGA 변환 결과 검증 결과를 요약한다.\n"
        "표준 PDF 작성 규칙(outline·검색가능 텍스트·셀 기반 표)을 시연한다.",
    )

    c.showPage()  # 페이지 분리

    # ── Page 2 ────────────────────────────────────────────────
    y = top
    # 2. 결과 (level 1)
    c.bookmarkPage("sec_2")
    c.addOutlineEntry("2. 결과", "sec_2", level=0, closed=False)
    y = draw_heading_1(c, left, y, "2. 결과")

    # 2.1 검증 (level 2)
    c.bookmarkPage("sec_2_1")
    c.addOutlineEntry("2.1 검증", "sec_2_1", level=1, closed=False)
    y = draw_heading_2(c, left, y, "2.1 검증")
    y = draw_body(
        c,
        left,
        y,
        "다음 표는 IGA 와 FEM 결과의 정량 비교이다.",
    )

    # 표 캡션 (표 위)
    c.setFont(KOR_FONT, 10)
    c.drawString(left, y, "Table 1: IGA vs FEM 응력 비교")
    y -= 14

    # 셀 기반 표
    headers = ["항목", "IGA(MPa)", "FEM(MPa)", "차이"]
    rows = [
        ["최대 응력", "250.3", "248.1", "0.9%"],
        ["평균 응력", "180.0", "179.5", "0.3%"],
    ]
    y = draw_table(c, left, y, headers, rows, col_w=110.0)

    c.save()
    return out_path


def main() -> int:
    out = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).resolve().parent / "sample_doc.pdf"
    )
    p = build_sample_pdf(out)
    print(f"[OK] PDF 예제 생성: {p}  ({p.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
