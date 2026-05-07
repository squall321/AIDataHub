"""Excel 변환기 CLI.

사용 예::

    python -m excel_converter input.xlsx \\
        --division HE --team CAE --year 2026 \\
        --start-seq 100 --output-dir output \\
        --mode per_sheet --skip-empty --infer-units --header-row 1

불규칙 시트 변환 예::

    # 표가 B5 부터 시작하는 시트
    python -m excel_converter messy.xlsx \\
        --division HE --team CAE --year 2026 \\
        --start-cell B5 --skip-blank-rows --infer-units

데이터 의미 명시 (원칙 6) — _META + _GLOSSARY 시트 사용 예::

    python -m excel_converter battery_test.xlsx \\
        --division HE --team CAE --year 2026 \\
        --infer-units \\
        --meta-sheet _META --glossary-sheet _GLOSSARY
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .core import XlsxConverter, XlsxConverterOptions, write_outputs

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="excel_converter",
        description="Excel(.xlsx) → DATA JSON 직접 변환기",
    )
    p.add_argument("xlsx_path", type=str, help="입력 .xlsx 경로")
    p.add_argument("--division", required=True, help="사업부 코드 (예: HE)")
    p.add_argument("--team", required=True, help="팀 코드 (예: CAE)")
    p.add_argument("--year", type=int, required=True, help="연도 (예: 2026)")
    p.add_argument(
        "--start-seq",
        type=int,
        default=1,
        help="첫 시트의 순번 (이후 시트는 +1, 기본 1)",
    )
    p.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="출력 폴더 (기본 ./output)",
    )
    p.add_argument(
        "--mode",
        choices=("per_sheet", "combined"),
        default="per_sheet",
        help="per_sheet: 시트별 1 JSON / combined: 모든 시트를 한 JSON 으로 병합",
    )
    p.add_argument(
        "--skip-empty",
        action="store_true",
        help="빈 시트/빈 행을 건너뜀",
    )
    p.add_argument(
        "--infer-units",
        action="store_true",
        help="헤더에서 단위(...)를 분리해 별도 units 배열로 저장",
    )
    p.add_argument(
        "--header-row",
        type=int,
        default=1,
        help="헤더가 위치한 1-based 행 번호 (기본 1)",
    )
    p.add_argument(
        "--start-cell",
        "--header-cell",
        dest="start_cell",
        type=str,
        default=None,
        help=(
            "표 좌상단 셀 주소 (예: A5). 지정하면 --header-row 와 column 1 무시. "
            "불규칙 시트(표가 시트 중간에 있을 때) 보정용."
        ),
    )
    p.add_argument(
        "--skip-blank-rows",
        action="store_true",
        help="데이터 사이의 빈 행을 제거 (skip-empty 와 달리 빈 시트는 유지)",
    )
    p.add_argument(
        "--notes",
        type=str,
        default="",
        help="모든 출력 JSON 에 첨부할 메모",
    )
    p.add_argument(
        "--meta-sheet",
        dest="meta_sheet",
        type=str,
        default="_META",
        help=(
            "워크북/시트 컨텍스트가 들어있는 예약 시트 이름 (기본 _META). "
            "이 시트는 데이터로 변환되지 않고 RecordIn 메타로 머지된다. "
            "규칙서 10장 참조."
        ),
    )
    p.add_argument(
        "--glossary-sheet",
        dest="glossary_sheet",
        type=str,
        default="_GLOSSARY",
        help=(
            "컬럼 의미·단위·자료형 정의 시트 이름 (기본 _GLOSSARY). "
            "이 시트도 데이터로 변환되지 않으며, 정의는 모든 데이터 시트에 적용된다. "
            "규칙서 11장 참조."
        ),
    )
    p.add_argument("--verbose", "-v", action="store_true", help="상세 로그")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s | %(message)s",
    )

    xlsx_path = Path(args.xlsx_path)
    if not xlsx_path.exists():
        print(f"오류: 파일을 찾을 수 없음: {xlsx_path}", file=sys.stderr)
        return 1
    if xlsx_path.suffix.lower() != ".xlsx":
        print(f"오류: .xlsx 파일만 지원: {xlsx_path}", file=sys.stderr)
        return 1

    opts = XlsxConverterOptions(
        division=args.division,
        team=args.team,
        year=args.year,
        start_seq=args.start_seq,
        output_dir=Path(args.output_dir),
        mode=args.mode,
        skip_empty=args.skip_empty,
        skip_blank_rows=args.skip_blank_rows,
        infer_units=args.infer_units,
        header_row=args.header_row,
        start_cell=args.start_cell,
        notes=args.notes,
        meta_sheet=args.meta_sheet,
        glossary_sheet=args.glossary_sheet,
    )

    converter = XlsxConverter(opts)
    sheets = converter.convert(xlsx_path)
    if not sheets:
        print("경고: 변환된 시트가 없습니다.", file=sys.stderr)
        return 2

    paths = write_outputs(sheets, opts)
    print("=== Excel 변환 완료 ===")
    print(f"입력      : {xlsx_path}")
    print(f"모드      : {args.mode}")
    print(f"시트 수   : {len(sheets)}")
    for s, p in zip(sheets, paths):
        print(f"  - {s.source_sheet:<24}  rows={len(s.rows):<5} -> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
