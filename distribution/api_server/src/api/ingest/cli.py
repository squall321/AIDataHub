"""Ingestion CLI 진입점.

사용:
    python -m api.ingest <path> [--db-url URL] [--dry-run] [--recursive]

예시:
    python -m api.ingest AI_data/examples/HE-CAE-2026-000001.json --dry-run
    python -m api.ingest AI_data/examples/ --recursive
    python -m api.ingest myfile.json --db-url postgresql+asyncpg://localhost/test
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Sequence

from .loader import copy_figures, iter_json_files, load_and_normalize
from .normalizer import compute_content_hash

logger = logging.getLogger("api.ingest")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m api.ingest",
        description="Ingest JSON record(s) into AI data hub database.",
    )
    p.add_argument(
        "path",
        type=Path,
        help="JSON file or directory containing JSON files.",
    )
    p.add_argument(
        "--db-url",
        default=None,
        help="SQLAlchemy database URL (overrides settings.database_url).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print summary; do not write to DB.",
    )
    p.add_argument(
        "--recursive",
        action="store_true",
        help="Recurse into subdirectories when path is a directory.",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity (-v INFO, -vv DEBUG).",
    )
    p.add_argument(
        "--figures-source",
        type=Path,
        default=None,
        help=(
            "Root dir to look up '{doc_id}/' figure folders. "
            "Default: same dir as each JSON file."
        ),
    )
    p.add_argument(
        "--no-figures",
        action="store_true",
        help="Skip copying figure binaries to FIGURES_DIR.",
    )
    return p


def _setup_logging(verbose: int) -> None:
    level = logging.WARNING
    if verbose >= 2:
        level = logging.DEBUG
    elif verbose >= 1:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _dry_run_summary(file_path: Path) -> dict:
    """파일을 읽고 정규화한 뒤 요약 dict 를 반환한다."""
    record = load_and_normalize(file_path)
    h = compute_content_hash(record.content)
    return {
        "file": str(file_path),
        "id": record.id,
        "data_type": record.data_type,
        "title": record.title,
        "summary": (record.summary or "")[:80],
        "tags": record.tags,
        "agents": record.agents,
        "schema_version": record.schema_version,
        "content_hash": h,
        "content_keys": sorted(list(record.content.keys())),
        "source_file": record.source_file,
        "author": record.author,
        "department": record.department,
        "project": record.project,
        "version": record.version,
    }


def _print_summary(summary: dict) -> None:
    print(f"--- {summary['file']}")
    print(f"  id            : {summary['id']}")
    print(f"  data_type     : {summary['data_type']}")
    print(f"  title         : {summary['title']}")
    print(f"  summary       : {summary['summary']}")
    print(f"  schema_ver    : {summary['schema_version']}")
    print(f"  tags          : {summary['tags']}")
    print(f"  agents        : {summary['agents']}")
    print(f"  content_keys  : {summary['content_keys']}")
    print(f"  content_hash  : {summary['content_hash']}")
    print(f"  source_file   : {summary['source_file']}")
    print(f"  author/dept   : {summary['author']!r} / {summary['department']!r}")
    print(f"  version       : {summary['version']}")


async def _run_db(
    files: list[Path],
    db_url: str | None,
    *,
    copy_figs: bool,
    figures_source: Path | None,
) -> tuple[int, int, int]:
    """파일들을 DB 에 기록한다. (inserted, updated, skipped) 카운트 반환."""
    # 모델/엔진은 lazy import — schema-only 사용자가 DB 의존성에 묶이지 않도록.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from ..config import settings
    from .db_writer import write_record

    if db_url:
        engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    else:
        # 기본 settings.database_url 사용 (api.db.base 와 동일).
        from ..db.base import engine  # type: ignore  # noqa: I001

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    inserted = updated = skipped = 0
    async with sessionmaker() as session:
        for fp in files:
            record = load_and_normalize(fp)
            result = await write_record(session, record)
            if result.action == "inserted":
                inserted += 1
            elif result.action == "updated":
                updated += 1
            else:
                skipped += 1
            print(
                f"[{result.action:>8}] {record.id}  ({fp.name}, "
                f"sections={result.sections_written})"
            )

            # 그림 binary 복사 (옵션 활성화된 경우)
            if copy_figs:
                src_root = figures_source or fp.parent
                try:
                    n_copied = copy_figures(
                        record.id,
                        source_root=src_root,
                        figures_dir=settings.figures_dir,
                    )
                    if n_copied:
                        print(
                            f"  + figures: copied {n_copied} file(s) "
                            f"from {src_root / record.id} → "
                            f"{settings.figures_dir / record.id}"
                        )
                except Exception as e:  # noqa: BLE001
                    print(
                        f"  ! figures copy failed for {record.id}: {e}",
                        file=sys.stderr,
                    )

        await session.commit()

    return inserted, updated, skipped


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _setup_logging(args.verbose)

    path = args.path
    if not path.exists():
        print(f"ERROR: path not found: {path}", file=sys.stderr)
        return 2

    files = list(iter_json_files(path, recursive=args.recursive))
    if not files:
        print(f"WARN: no .json files under {path}", file=sys.stderr)
        return 0

    if args.dry_run:
        ok = err = 0
        for fp in files:
            try:
                summary = _dry_run_summary(fp)
                _print_summary(summary)
                ok += 1
            except Exception as e:  # noqa: BLE001
                print(f"--- {fp}\n  ERROR: {e}", file=sys.stderr)
                err += 1
        print(f"\nDry-run done: ok={ok} error={err} total={len(files)}")
        return 0 if err == 0 else 1

    try:
        inserted, updated, skipped = asyncio.run(
            _run_db(
                files,
                args.db_url,
                copy_figs=not args.no_figures,
                figures_source=args.figures_source,
            )
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("ingestion failed")
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(
        f"\nIngest done: inserted={inserted} updated={updated} skipped={skipped} "
        f"total={len(files)}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
