"""변환기 CLI."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .core import Converter, ConverterOptions, write_output

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="converter",
        description="Word(.docx) → JSON 변환 (json_schema_rules.md v1.0)",
    )
    parser.add_argument("docx_path", help="입력 .docx 경로")
    parser.add_argument("--division", required=True, help="팀 코드 (예: HE)")
    parser.add_argument("--team", required=True, help="그룹 코드 (예: CAE)")
    parser.add_argument("--year", type=int, required=True, help="연도 (예: 2026)")
    parser.add_argument("--seq", type=int, default=1, help="순번 (기본 1)")
    parser.add_argument(
        "--output-dir",
        default="output",
        help="출력 폴더 (기본 ./output)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="상세 로그 출력",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s | %(message)s",
    )

    docx_path = Path(args.docx_path)
    if not docx_path.exists():
        print(f"오류: 파일을 찾을 수 없음: {docx_path}", file=sys.stderr)
        return 1
    if docx_path.suffix.lower() != ".docx":
        print(f"오류: .docx 파일만 지원: {docx_path}", file=sys.stderr)
        return 1

    opts = ConverterOptions(
        division=args.division.upper(),
        team=args.team.upper(),
        year=args.year,
        seq=args.seq,
        output_dir=Path(args.output_dir),
    )

    logger.info(f"변환 시작: {docx_path}")
    converter = Converter(opts)
    result = converter.convert(str(docx_path))

    json_path, log_path = write_output(result, opts.output_dir)
    logger.info(f"JSON 출력: {json_path}")
    if result.warnings:
        logger.warning(f"경고 {len(result.warnings)}건 → {log_path}")
        if args.verbose:
            for w in result.warnings:
                logger.warning(f"  - {w}")
    else:
        logger.info("경고 없음")

    print(f"\n=== 변환 완료 ===")
    print(f"doc_id     : {result.meta['doc_id']}")
    print(f"sections   : {len(result.sections)}개 (최상위)")
    print(f"figures    : {len(result.figures)}개")
    print(f"tables     : {len(result.tables)}개")
    print(f"sources    : {len(result.sources)}개")
    print(f"warnings   : {len(result.warnings)}건")
    print(f"output     : {json_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
