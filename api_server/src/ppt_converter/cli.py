"""PPT 변환기 CLI.

사용 예:
    python -m ppt_converter slides.pptx \
        --team HE --group CAE --year 2026 --seq 1 \
        --output-dir output --tags IGA,튜토리얼 --agents iga-analyst
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .core import PptxConverter, PptxConverterOptions, write_output

logger = logging.getLogger(__name__)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ppt_converter",
        description=(
            "PowerPoint(.pptx) → JSON 변환 "
            "(json_schema_rules.md v1.0, data_type=DOC, source_format=pptx)"
        ),
    )
    parser.add_argument("pptx_path", help="입력 .pptx 경로")
    parser.add_argument("--team", required=True, help="팀 코드 (예: HE)")
    parser.add_argument("--group", required=True, help="그룹 코드 (예: CAE)")
    parser.add_argument("--year", type=int, required=True, help="연도 (예: 2026)")
    parser.add_argument("--seq", type=int, default=1, help="순번 (기본 1)")
    parser.add_argument(
        "--output-dir", default="output", help="출력 폴더 (기본 ./output)"
    )
    parser.add_argument(
        "--tags",
        default="",
        help="콤마로 구분된 태그 목록 (예: IGA,튜토리얼)",
    )
    parser.add_argument(
        "--agents",
        default="",
        help="콤마로 구분된 agent_scope (예: iga-analyst,code-assistant)",
    )
    parser.add_argument(
        "--no-extract-images",
        action="store_true",
        help="그림 바이너리 추출 비활성화",
    )
    parser.add_argument(
        "--no-extract-summary",
        action="store_true",
        help="meta.summary 자동 폴백(core.subject / 본문 텍스트) 비활성화",
    )
    parser.add_argument(
        "--no-group-duplicates",
        action="store_true",
        help="연속 동일 제목 자동 그룹화 비활성화 (모든 슬라이드를 평탄한 level 1 로 유지)",
    )
    parser.add_argument(
        "--no-extract-body-headings",
        action="store_true",
        help="본문 안의 \"1.1 …\" H2/H3 번호 패턴 자동 sub-section 승격 비활성화",
    )
    parser.add_argument(
        "--no-infer-caption",
        action="store_true",
        help=(
            "그림 캡션 자동 추정 비활성화 (alt-text / 인접 텍스트박스 분석을 끄고 "
            "기본 placeholder 만 사용)"
        ),
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="상세 로그 출력"
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s | %(message)s",
    )

    pptx_path = Path(args.pptx_path)
    if not pptx_path.exists():
        print(f"오류: 파일을 찾을 수 없음: {pptx_path}", file=sys.stderr)
        return 1
    if pptx_path.suffix.lower() != ".pptx":
        print(f"오류: .pptx 파일만 지원: {pptx_path}", file=sys.stderr)
        return 1

    opts = PptxConverterOptions(
        team=args.team.upper(),
        group=args.group.upper(),
        year=args.year,
        seq=args.seq,
        output_dir=Path(args.output_dir),
        extract_images=not args.no_extract_images,
        tags=_split_csv(args.tags),
        agents=_split_csv(args.agents),
        extract_summary=not args.no_extract_summary,
        group_consecutive_duplicates=not args.no_group_duplicates,
        extract_body_headings=not args.no_extract_body_headings,
        infer_caption_from_neighbor=not args.no_infer_caption,
    )

    logger.info(f"변환 시작: {pptx_path}")
    converter = PptxConverter(opts)
    result = converter.convert(str(pptx_path))

    json_path, log_path = write_output(result, opts.output_dir)
    logger.info(f"JSON 출력: {json_path}")
    if result.warnings:
        logger.warning(f"경고 {len(result.warnings)}건 → {log_path}")
        if args.verbose:
            for w in result.warnings:
                logger.warning(f"  - {w}")
    else:
        logger.info("경고 없음")

    print("\n=== 변환 완료 ===")
    print(f"doc_id      : {result.meta['doc_id']}")
    print(f"sections    : {len(result.sections)}개 (최상위)")
    print(f"figures     : {len(result.figures)}개")
    print(f"tables      : {len(result.tables)}개")
    print(f"attachments : {len(result.attachments)}개")
    print(f"warnings    : {len(result.warnings)}건")
    print(f"output      : {json_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
