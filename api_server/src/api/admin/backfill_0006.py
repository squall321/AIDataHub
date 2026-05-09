"""Migration 0006 백필 스크립트.

배경
====
Migration 0006 은 ``records`` 테이블에 다음 12 개 컬럼을 추가했다:

    classification, status, domain, subject_keywords, source_system,
    language, parent_record_id, derivation, capabilities,
    quality_score, valid_from, valid_until

그러나 Agent 27 의 수정 이전 ``ingest.db_writer.write_record`` 는 ``RecordIn``
의 해당 필드를 ORM 인스턴스에 복사하지 않은 채 INSERT/UPDATE 했기 때문에,
모든 기존 레코드는 PostgreSQL 측 ``server_default`` 값(예: ``classification =
'internal'``, ``status = 'draft'``, ``language = 'ko'``, ``derivation =
'original'``, ``capabilities = []``, ``subject_keywords = []``)으로만 채워졌다.

하지만 ``records.content`` JSONB 안에는 정규화 단계에서 보존된 원본
``meta.classification`` / ``meta.status`` / ``meta.domain`` /
``meta.subject_keywords`` / ``meta.language`` / ``meta.derivation`` /
``meta.quality_score`` / ``meta.valid_from`` / ``meta.valid_until`` 값이 그대로
남아 있다. 이 스크립트는 그 값을 다시 컬럼으로 끌어올린다.

또한 ``capabilities`` 는 ``services.capabilities.compute_capabilities`` 로
``content`` 형태에서 다시 계산할 수 있으므로, 빈 배열인 레코드는 모두
재계산해 채워 넣는다.

동작 모드
=========

* ``--dry-run`` : 변경사항을 출력만 하고 commit 하지 않는다.
* ``diagnose``  : 위치 인자 ``diagnose`` 로 실행하면 백필 없이 카운트만 보고.
* ``--limit N`` : 최대 N 건까지만 처리.

CLI 예
======

::

    # 변경 없이 영향만 보기
    python -m api.admin.backfill_0006 --dry-run

    # diagnose only — 변경 없음.
    python -m api.admin.backfill_0006 diagnose

    # 실제 백필 (commit).
    python -m api.admin.backfill_0006

    # 처음 100 건만.
    python -m api.admin.backfill_0006 --limit 100

멱등(idempotent)
=================

이미 올바른 값을 가진 레코드는 변경 사항이 없으므로 다시 실행해도 안전하다.
컬럼이 ``서버 default`` 값을 그대로 들고 있으면서 ``content.meta`` 에 다른
값이 있을 때만 UPDATE 한다.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("api.admin.backfill_0006")


# ---------------------------------------------------------------------------
# Defaults — DB 측 server_default 와 동일해야 한다.
# ---------------------------------------------------------------------------
DEFAULT_CLASSIFICATION = "internal"
DEFAULT_STATUS = "draft"
DEFAULT_LANGUAGE = "ko"
DEFAULT_DERIVATION = "original"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _meta_of(content: dict[str, Any] | None) -> dict[str, Any]:
    """``content.meta`` 를 dict 로 안전하게 추출."""
    if not isinstance(content, dict):
        return {}
    meta = content.get("meta")
    if isinstance(meta, dict):
        return meta
    return {}


def _coerce_date(v: Any) -> date | None:
    """ISO 형식 문자열 / date 인스턴스 / None 모두 허용."""
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            logger.debug("invalid valid_* date string: %r", v)
            return None
    return None


def _coerce_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if not (0 <= n <= 100):
        return None
    return n


def _str_list(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    out: list[str] = []
    for item in v:
        if isinstance(item, str) and item:
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# Decision: which fields are at default and have a better source value?
# ---------------------------------------------------------------------------
def compute_backfill(record: Any) -> dict[str, Any]:
    """단일 record 에 대해 backfill 후보 컬럼·새 값을 dict 로 반환.

    기존 컬럼 값이 ``server_default`` 값과 같고 (또는 capabilities 의 경우 빈
    배열이고) ``content.meta`` 에 더 정보가 풍부한 값이 들어 있으면 그 값을
    채택한다.

    Returns:
        ``{컬럼명: 새 값}`` 형태. 변경할 것이 없으면 빈 dict.
    """
    from ..services.capabilities import compute_capabilities

    content = record.content if isinstance(record.content, dict) else {}
    meta = _meta_of(content)

    proposals: dict[str, Any] = {}

    # ---- classification -----------------------------------------------------
    if record.classification == DEFAULT_CLASSIFICATION:
        m = meta.get("classification")
        if isinstance(m, str) and m and m != DEFAULT_CLASSIFICATION:
            proposals["classification"] = m

    # ---- status -------------------------------------------------------------
    if record.status == DEFAULT_STATUS:
        m = meta.get("status")
        if isinstance(m, str) and m and m != DEFAULT_STATUS:
            proposals["status"] = m

    # ---- domain (default = NULL) -------------------------------------------
    if record.domain in (None, ""):
        m = meta.get("domain")
        if isinstance(m, str) and m:
            proposals["domain"] = m

    # ---- subject_keywords (default = []) -----------------------------------
    if not record.subject_keywords:
        candidate = _str_list(meta.get("subject_keywords"))
        if candidate:
            proposals["subject_keywords"] = candidate

    # ---- source_system (default = NULL) ------------------------------------
    if record.source_system in (None, ""):
        m = meta.get("source_system")
        if isinstance(m, str) and m:
            proposals["source_system"] = m

    # ---- language -----------------------------------------------------------
    if record.language == DEFAULT_LANGUAGE:
        m = meta.get("language")
        if isinstance(m, str) and m and m != DEFAULT_LANGUAGE:
            proposals["language"] = m

    # ---- derivation ---------------------------------------------------------
    if record.derivation == DEFAULT_DERIVATION:
        m = meta.get("derivation")
        if isinstance(m, str) and m and m != DEFAULT_DERIVATION:
            proposals["derivation"] = m

    # ---- quality_score (default = NULL) ------------------------------------
    if record.quality_score is None:
        n = _coerce_int(meta.get("quality_score"))
        if n is not None:
            proposals["quality_score"] = n

    # ---- valid_from / valid_until (default = NULL) -------------------------
    if record.valid_from is None:
        d = _coerce_date(meta.get("valid_from"))
        if d is not None:
            proposals["valid_from"] = d
    if record.valid_until is None:
        d = _coerce_date(meta.get("valid_until"))
        if d is not None:
            proposals["valid_until"] = d

    # ---- capabilities (default = []) ---------------------------------------
    # capabilities 는 항상 content shape 으로부터 재계산이 가능하므로 빈
    # 배열이면 새 계산값과 비교해 채워 넣는다.
    if not record.capabilities:
        computed = compute_capabilities(content)
        if computed:
            proposals["capabilities"] = computed

    return proposals


# ---------------------------------------------------------------------------
# Stats container
# ---------------------------------------------------------------------------
class BackfillStats:
    __slots__ = (
        "scanned",
        "would_update",
        "updated",
        "default_only",
        "by_field",
    )

    def __init__(self) -> None:
        self.scanned: int = 0
        self.would_update: int = 0
        self.updated: int = 0
        self.default_only: int = 0
        self.by_field: dict[str, int] = {}

    def record_proposals(self, proposals: dict[str, Any]) -> None:
        if not proposals:
            return
        for field in proposals:
            self.by_field[field] = self.by_field.get(field, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "would_update": self.would_update,
            "updated": self.updated,
            "default_only": self.default_only,
            "by_field": dict(sorted(self.by_field.items())),
        }


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------
async def run_backfill(
    session: AsyncSession,
    *,
    dry_run: bool,
    limit: int | None = None,
    diagnose: bool = False,
) -> BackfillStats:
    """모든 ``Record`` 를 스캔해 backfill 을 적용 (또는 dry-run)."""
    from ..db.models import Record

    stmt = select(Record).order_by(Record.id)
    if limit is not None and limit > 0:
        stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).scalars().all()
    stats = BackfillStats()

    for rec in rows:
        stats.scanned += 1
        # default_only: 6 개의 default-값 컬럼이 모두 default 인 경우 (대부분의
        # "버그 영향" 후보).
        looks_default = (
            rec.classification == DEFAULT_CLASSIFICATION
            and rec.status == DEFAULT_STATUS
            and rec.language == DEFAULT_LANGUAGE
            and rec.derivation == DEFAULT_DERIVATION
            and not rec.capabilities
            and not rec.subject_keywords
        )
        if looks_default:
            stats.default_only += 1

        proposals = compute_backfill(rec)
        if not proposals:
            continue
        stats.would_update += 1
        stats.record_proposals(proposals)

        if diagnose or dry_run:
            logger.info(
                "[%s] would set %s",
                rec.id,
                ", ".join(f"{k}={proposals[k]!r}" for k in sorted(proposals)),
            )
            continue

        # apply
        for field, value in proposals.items():
            setattr(rec, field, value)
        stats.updated += 1

    if not dry_run and not diagnose and stats.updated > 0:
        await session.commit()

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _format_summary(stats: BackfillStats, *, dry_run: bool, diagnose: bool) -> str:
    mode = "diagnose" if diagnose else ("dry-run" if dry_run else "live")
    lines = [
        f"Backfill 0006 — mode={mode}",
        f"  scanned       : {stats.scanned}",
        f"  default-only  : {stats.default_only}  (records w/ all 6 key cols still at default)",
        f"  would-update  : {stats.would_update}",
        f"  updated       : {stats.updated}",
    ]
    if stats.by_field:
        lines.append("  by-field counts:")
        for k, v in sorted(stats.by_field.items()):
            lines.append(f"    - {k:<18s}: {v}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="api.admin.backfill_0006",
        description="Backfill metadata columns added in Migration 0006",
    )
    p.add_argument(
        "mode",
        nargs="?",
        default="backfill",
        choices=("backfill", "diagnose"),
        help="'diagnose' 는 카운트만 보고, 'backfill' 은 실제 적용 (기본).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="변경 없이 영향만 출력 (commit 하지 않음).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="최대 N 건까지만 처리.",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="레코드별 로그 줄을 억제 (요약만 출력).",
    )
    return p


async def main_async(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(message)s",
    )

    diagnose = args.mode == "diagnose"

    # ``api.db.base`` 는 import 시 settings.database_url 을 캡처한다. CLI 사용자가
    # 자체 환경변수로 미리 세팅한 상태에서 호출한다고 가정.
    from ..db.base import SessionLocal

    async with SessionLocal() as session:
        stats = await run_backfill(
            session,
            dry_run=args.dry_run,
            limit=args.limit,
            diagnose=diagnose,
        )

    print(_format_summary(stats, dry_run=args.dry_run, diagnose=diagnose))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Sync 진입점 (``python -m api.admin.backfill_0006``)."""
    # Windows ProactorEventLoop 회피.
    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:  # pragma: no cover
            pass
    return asyncio.run(main_async(argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "BackfillStats",
    "compute_backfill",
    "main",
    "main_async",
    "run_backfill",
]
