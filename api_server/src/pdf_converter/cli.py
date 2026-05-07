"""PDF 변환기 CLI.

사용 예::

    python -m pdf_converter input.pdf \\
        --division HE --team CAE --year 2026 --seq 7 \\
        --output-dir output \\
        --agents iga-analyst,doc-curator \\
        --tags KooRemapper,IGA,NURBS
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .core import PdfConverter, PdfConverterOptions, write_output

logger = logging.getLogger(__name__)


def _split_csv(s: str | None) -> list[str]:
    if not s:
        return []
    return [t.strip() for t in s.split(",") if t.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf_converter",
        description="PDF(.pdf) → DOC JSON 변환 (json_schema_rules.md v1.0)",
    )
    p.add_argument("pdf_path", type=str, help="입력 .pdf 경로")
    p.add_argument("--division", required=True, help="사업부 코드 (예: HE)")
    p.add_argument("--team", required=True, help="팀 코드 (예: CAE)")
    p.add_argument("--year", type=int, required=True, help="연도 (예: 2026)")
    p.add_argument("--seq", type=int, default=1, help="순번 (기본 1)")
    p.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="출력 폴더 (기본 ./output)",
    )
    p.add_argument(
        "--agents",
        type=str,
        default="",
        help="agent_scope 콤마 구분 (예: iga-analyst,doc-curator)",
    )
    p.add_argument(
        "--tags",
        type=str,
        default="",
        help="meta.tags 콤마 구분 (PDF /Info.Keywords 가 추가로 병합됨)",
    )
    p.add_argument(
        "--fontsize-ratio",
        type=float,
        default=1.2,
        help="헤딩 판정 폰트 크기 비율 (본문 평균 대비, 기본 1.2)",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="상세 로그",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s | %(message)s",
    )

    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        print(f"오류: 파일을 찾을 수 없음: {pdf_path}", file=sys.stderr)
        return 1
    if pdf_path.suffix.lower() != ".pdf":
        print(f"오류: .pdf 파일만 지원: {pdf_path}", file=sys.stderr)
        return 1

    opts = PdfConverterOptions(
        division=args.division,
        team=args.team,
        year=args.year,
        seq=args.seq,
        output_dir=Path(args.output_dir),
        agents=_split_csv(args.agents),
        tags=_split_csv(args.tags),
        fontsize_heading_ratio=args.fontsize_ratio,
    )

    logger.info("변환 시작: %s", pdf_path)
    converter = PdfConverter(opts)
    try:
        result = converter.convert(pdf_path)
    except RuntimeError as exc:
        print(f"변환 실패: {exc}", file=sys.stderr)
        return 2

    json_path, log_path = write_output(result, opts.output_dir)
    print("=== PDF 변환 완료 ===")
    print(f"doc_id     : {result.meta['doc_id']}")
    print(f"page_count : {result.meta.get('pdf', {}).get('page_count', 0)}")
    print(f"strategy   : {result.meta.get('pdf', {}).get('heading_strategy', 'unknown')}")
    print(f"sections   : {len(result.sections)}개 (최상위)")
    print(f"figures    : {len(result.figures)}개")
    print(f"tables     : {len(result.tables)}개")
    print(f"attachments: {len(result.attachments)}개")
    print(f"warnings   : {len(result.warnings)}건")
    print(f"output     : {json_path}")
    if result.warnings:
        print(f"warn log   : {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
