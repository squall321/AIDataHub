"""Excel(.xlsx) → DATA JSON 직접 변환기.

도커 e2e와 무관한 순수 라이브러리 모듈로, 표 데이터(시트)를 그대로
DATA-{team}-{group}-{year}-{seq}.json 으로 변환한다.

핵심 클래스:
    XlsxConverter: 시트 → DATA JSON 변환기
    XlsxConverterOptions: 변환 옵션 (start_cell, skip_blank_rows 포함)
    IrregularReport: 불규칙 시트 자동 탐지 결과

CLI:
    python -m excel_converter input.xlsx --team HE --group CAE --year 2026 \\
        --start-seq 100 --output-dir output --mode per_sheet \\
        --start-cell A5 --skip-blank-rows
"""
from __future__ import annotations

from .core import (
    ConvertedSheet,
    IrregularReport,
    XlsxConverter,
    XlsxConverterOptions,
    detect_irregular,
    parse_cell_address,
    _parse_meta_sheet,
    _parse_glossary_sheet,
    _extract_workbook_properties,
    _coerce_with_dtype,
)
from .units import parse_header_units

__all__ = [
    "ConvertedSheet",
    "IrregularReport",
    "XlsxConverter",
    "XlsxConverterOptions",
    "detect_irregular",
    "parse_cell_address",
    "parse_header_units",
    "_parse_meta_sheet",
    "_parse_glossary_sheet",
    "_extract_workbook_properties",
    "_coerce_with_dtype",
]
