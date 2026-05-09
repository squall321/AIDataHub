r"""PDF(.pdf) → DOC JSON 변환기.

[json_schema_rules.md] v1.0 스키마 출력 (data_type=DOC, source_format=pdf).
[pdf_to_json_conversion_rules.md] 의 규칙을 따른다.

설계 핵심:
- ``pdfplumber`` 로 페이지 단위 텍스트/표/문자 메타데이터(폰트 크기) 추출.
- ``pypdf`` 로 /Info(메타데이터) 와 /Outlines(북마크 = TOC) 를 읽는다.
- 헤딩 추론 우선순위:
    1) PDF outline (북마크) 가 있으면 그것을 헤딩으로 사용 — 가장 신뢰.
    2) ``^\d+(\.\d+){0,2}\s+제목`` 패턴 — Word 변환기와 동일.
    3) 폰트 크기 휴리스틱 (line_avg_size > body_avg_size * 1.2).
- 표는 ``page.extract_tables()`` 로 추출 → ``tables[]`` + ``section.table_refs``.
- 그림 추출은 베스트 노력 (pdfplumber 의 ``page.images`` 메타).
- PDF 는 정보 손실이 가장 큰 포맷이다 — 비표준 PDF 는 경고를 명확히 남긴다.
"""
__version__ = "0.1.0"

from .core import PdfConverter, PdfConverterOptions, write_output

__all__ = [
    "PdfConverter",
    "PdfConverterOptions",
    "write_output",
]
