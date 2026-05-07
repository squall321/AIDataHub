"""Excel(.xlsx) → DATA JSON 직접 변환기.

도커 e2e와 무관한 순수 라이브러리 모듈로, 표 데이터(시트)를 그대로
DATA-{div}-{team}-{year}-{seq}.json 으로 변환한다.

핵심 클래스:
    XlsxConverter: 시트 → DATA JSON 변환기

CLI:
    python -m excel_converter input.xlsx --division HE --team CAE --year 2026 \\
        --start-seq 100 --output-dir output --mode per_sheet
"""
from __future__ import annotations

from .core import ConvertedSheet, XlsxConverter, XlsxConverterOptions
from .units import parse_header_units

__all__ = [
    "ConvertedSheet",
    "XlsxConverter",
    "XlsxConverterOptions",
    "parse_header_units",
]
