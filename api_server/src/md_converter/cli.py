"""Markdown 변환기 CLI.

사용 예::

    python -m md_converter input.md \\
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

from .core import MarkdownConverter, MarkdownConverterOptions, write_output

logger = logging.getLogger(__name__)


def _split_csv(s: str | None) -> list[str]:
    if not s:
        return []
    return [t.strip() for t in s.split(",") if t.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="md_converter",
        description="Markdown(.md) → DOC JSON 변환 (json_schema_rules.md v1.0)",
    )
    p.add_argument("md_path", type=str, help="입력 .md 경로")
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
        help="meta.tags 콤마 구분 (front matter tags 보다 우선순위 낮음)",
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

    md_path = Path(args.md_path)
    if not md_path.exists():
        print(f"오류: 파일을 찾을 수 없음: {md_path}", file=sys.stderr)
        return 1
    if md_path.suffix.lower() not in (".md", ".markdown"):
        print(f"오류: .md / .markdown 파일만 지원: {md_path}", file=sys.stderr)
        return 1

    opts = MarkdownConverterOptions(
        division=args.division,
        team=args.team,
        year=args.year,
        seq=args.seq,
        output_dir=Path(args.output_dir),
        agents=_split_csv(args.agents),
        tags=_split_csv(args.tags),
    )

    logger.info("변환 시작: %s", md_path)
    converter = MarkdownConverter(opts)
    result = converter.convert(md_path)

    json_path, log_path = write_output(result, opts.output_dir)
    print("=== Markdown 변환 완료 ===")
    print(f"doc_id     : {result.meta['doc_id']}")
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
