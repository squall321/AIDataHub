"""Batch ingest CLI for converter-supported files.

사용:
    python -m api.ingest.batch <dir> [--workers N] [--dry-run] [--no-attachments]

동작:
    1. ``<dir>`` 를 재귀 순회하면서 지원 확장자 (.docx / .xlsx / .pptx / .md /
       .markdown / .pdf) 파일을 수집.
    2. asyncio.Semaphore 로 worker 수 제한 (기본 4) 하에 병렬 처리.
    3. 각 파일에 대해 ``converter_dispatch.convert_file`` → ``normalize`` →
       ``write_record`` 흐름을 수행.
    4. 멱등성은 기존 ``content_hash`` 비교 로직이 처리한다 (중복 = ``skipped``).
    5. ``--dry-run`` 은 변환만 수행하고 DB 쓰기를 생략한다.

요약 (stdout):
    ok=3 failed=1 skipped=2 total=6 elapsed=12.3s
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..config import settings
from ..services.converter_dispatch import (
    EXTENSION_MAP,
    ConvertRequest,
    SourceFormat,
    convert_file,
    detect_format,
)
from .loader import copy_attachments
from .normalizer import normalize

logger = logging.getLogger("api.ingest.batch")


SUPPORTED_EXTS = tuple(sorted(EXTENSION_MAP.keys()))


# ---------------------------------------------------------------------------
# Args / file discovery
# ---------------------------------------------------------------------------
@dataclass
class FileResult:
    path: Path
    status: str  # 'ok' | 'failed' | 'skipped' | 'updated'
    record_id: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m api.ingest.batch",
        description="Batch convert + ingest a directory of supported files.",
    )
    p.add_argument(
        "path",
        type=Path,
        help="Directory to walk recursively.",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Concurrent workers (default 4).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Convert + normalize only; skip DB write.",
    )
    p.add_argument(
        "--no-attachments",
        action="store_true",
        help="Skip copying attachment binaries to attachments_dir.",
    )
    p.add_argument(
        "--team",
        default="HE",
        help="Team code applied to all converted files (default HE).",
    )
    p.add_argument(
        "--group",
        default="CAE",
        help="Group code (default CAE).",
    )
    p.add_argument(
        "--year",
        type=int,
        default=None,
        help="Year. Defaults to current calendar year.",
    )
    p.add_argument(
        "--start-seq",
        type=int,
        default=0,
        help="Starting seq (0 = auto-assign per file).",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity (-v INFO, -vv DEBUG).",
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


def discover_files(root: Path) -> list[Path]:
    """``root`` 하위에서 지원 확장자 파일을 평탄하게 모은다."""
    if not root.exists():
        raise FileNotFoundError(root)
    if root.is_file():
        if root.suffix.lower() in SUPPORTED_EXTS:
            return [root]
        return []
    files: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() in SUPPORTED_EXTS:
            files.append(p)
    return files


# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------
async def _process_one(
    fp: Path,
    *,
    team: str,
    group: str,
    year: int,
    start_seq: int,
    output_root: Path,
    dry_run: bool,
    persist_attachments: bool,
    sem: asyncio.Semaphore,
    sessionmaker,
) -> FileResult:
    """단일 파일을 변환·인제스트한다."""
    async with sem:
        t0 = time.monotonic()
        log_prefix = f"[{fp.name}]"
        logger.info("%s start", log_prefix)
        try:
            fmt = detect_format(fp.name)
        except Exception as exc:  # noqa: BLE001
            return FileResult(
                path=fp,
                status="failed",
                error=f"detect_format: {exc}",
                elapsed_seconds=time.monotonic() - t0,
            )

        # output_dir 분리: file 이름 sanitize.
        per_file_out = output_root / fp.stem
        per_file_out.mkdir(parents=True, exist_ok=True)

        # auto-seq 결정.
        eff_seq = start_seq
        if eff_seq <= 0 and not dry_run:
            from ..services.seq import next_seq as _next_seq

            inferred_dt = "DATA" if fmt == SourceFormat.XLSX else "DOC"
            async with sessionmaker() as ses:
                eff_seq = await _next_seq(
                    ses,
                    data_type=inferred_dt,
                    team=team,
                    group=group,
                    year=year,
                )
        elif eff_seq <= 0:
            eff_seq = 1  # dry-run 폴백.

        req = ConvertRequest(
            team=team,
            group=group,
            year=year,
            seq=eff_seq,
            output_dir=per_file_out,
        )
        try:
            payload = convert_file(fp, fmt, req)
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s convert failed", log_prefix)
            return FileResult(
                path=fp,
                status="failed",
                error=f"convert: {exc}",
                elapsed_seconds=time.monotonic() - t0,
            )

        try:
            if isinstance(payload, dict) and not payload.get("source_file"):
                payload["source_file"] = fp.name
            record_in = normalize(payload)
        except Exception as exc:  # noqa: BLE001
            return FileResult(
                path=fp,
                status="failed",
                error=f"normalize: {exc}",
                elapsed_seconds=time.monotonic() - t0,
            )

        if dry_run:
            elapsed = time.monotonic() - t0
            logger.info(
                "%s dry-run done id=%s (%.2fs)",
                log_prefix,
                record_in.id,
                elapsed,
            )
            return FileResult(
                path=fp,
                status="ok",
                record_id=record_in.id,
                elapsed_seconds=elapsed,
            )

        # DB write.
        from .db_writer import write_record

        async with sessionmaker() as session:
            try:
                wr = await write_record(session, record_in)
                await session.commit()
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                return FileResult(
                    path=fp,
                    status="failed",
                    error=f"write_record: {exc}",
                    elapsed_seconds=time.monotonic() - t0,
                )

            # commit 후 embed schedule
            if getattr(wr, "should_embed", False):
                try:
                    from ..services.jobs import maybe_schedule_auto_embed

                    maybe_schedule_auto_embed(wr.record.id)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("%s auto-embed schedule skipped: %s", log_prefix, exc)

            # attachments 복사.
            if persist_attachments:
                try:
                    copy_attachments(
                        wr.record.id,
                        source_root=per_file_out,
                        attachments_dir=settings.attachments_dir,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "%s attachment copy failed: %s", log_prefix, exc
                    )

        elapsed = time.monotonic() - t0
        status = "skipped" if wr.action == "skipped" else "ok"
        logger.info(
            "%s done action=%s id=%s (%.2fs)",
            log_prefix,
            wr.action,
            wr.record.id,
            elapsed,
        )
        return FileResult(
            path=fp,
            status=status,
            record_id=wr.record.id,
            elapsed_seconds=elapsed,
        )


# ---------------------------------------------------------------------------
# Async runner
# ---------------------------------------------------------------------------
async def run_batch(
    files: list[Path],
    *,
    workers: int,
    team: str,
    group: str,
    year: int,
    start_seq: int,
    dry_run: bool,
    persist_attachments: bool,
    output_root: Path,
) -> list[FileResult]:
    """주어진 파일 목록을 병렬 처리한다."""
    if dry_run:
        sessionmaker = None  # type: ignore[assignment]
    else:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from ..db.base import engine

        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    sem = asyncio.Semaphore(max(1, int(workers)))

    tasks = [
        _process_one(
            fp,
            team=team,
            group=group,
            year=year,
            start_seq=start_seq,
            output_root=output_root,
            dry_run=dry_run,
            persist_attachments=persist_attachments,
            sem=sem,
            sessionmaker=sessionmaker,
        )
        for fp in files
    ]
    return await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def _format_summary(results: list[FileResult], total_elapsed: float) -> str:
    ok = sum(1 for r in results if r.status == "ok")
    failed = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped")
    return (
        f"ok={ok} failed={failed} skipped={skipped} total={len(results)} "
        f"elapsed={total_elapsed:.2f}s"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _setup_logging(args.verbose)

    root = args.path
    if not root.exists():
        print(f"ERROR: path not found: {root}", file=sys.stderr)
        return 2

    files = discover_files(root)
    if not files:
        print(f"WARN: no supported files under {root}", file=sys.stderr)
        return 0

    year = args.year or _current_year()
    workers = max(1, int(args.workers))
    output_root = Path(settings.upload_temp_dir) / "batch_ingest"
    output_root.mkdir(parents=True, exist_ok=True)

    print(
        f"Batch ingest: files={len(files)} workers={workers} "
        f"dry_run={args.dry_run} root={root}"
    )

    t_start = time.monotonic()
    try:
        results = asyncio.run(
            run_batch(
                files,
                workers=workers,
                team=args.team,
                group=args.group,
                year=year,
                start_seq=args.start_seq,
                dry_run=args.dry_run,
                persist_attachments=not args.no_attachments,
                output_root=output_root,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("batch ingest failed")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    total_elapsed = time.monotonic() - t_start

    # 파일별 라인 출력.
    for r in results:
        marker = {
            "ok": "OK",
            "failed": "ERR",
            "skipped": "SKIP",
        }.get(r.status, r.status.upper())
        line = f"[{marker:>4}] {r.path.name}"
        if r.record_id:
            line += f"  id={r.record_id}"
        if r.error:
            line += f"  err={r.error}"
        line += f"  ({r.elapsed_seconds:.2f}s)"
        print(line)

    print()
    print(_format_summary(results, total_elapsed))
    failed = sum(1 for r in results if r.status == "failed")
    return 0 if failed == 0 else 1


def _current_year() -> int:
    from datetime import datetime

    return datetime.now().year


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
