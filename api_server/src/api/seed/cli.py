"""``python -m api.seed`` 진입점.

사용:
    python -m api.seed                         # 표준 에이전트 5종 upsert
    python -m api.seed --dry-run               # DB 변경 없이 계획만 출력
    python -m api.seed --db-url <ASYNC_URL>    # settings.database_url 무시

전략:
    - PostgreSQL/SQLite 모두 동작하도록 dialect-agnostic 한 SELECT-then-INSERT/UPDATE
      방식을 사용한다.
    - ``agent_type`` (PK) 가 이미 존재하면 ``name`` / ``description`` /
      ``common_tags`` / ``data_types`` 필드를 갱신한다 (멱등).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .agents_data import STANDARD_AGENTS

if TYPE_CHECKING:
    from .agents_data import AgentSeed

logger = logging.getLogger("api.seed")


# ---------------------------------------------------------------------------
# Core upsert
# ---------------------------------------------------------------------------
async def seed_agents(
    session: AsyncSession,
    agents: Sequence["AgentSeed"] = STANDARD_AGENTS,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """``agents`` 시드 정의를 DB 에 upsert.

    Returns:
        ``{"inserted": int, "updated": int, "unchanged": int}`` 카운터.
    """
    from api.db.models import Agent  # 함수 단위 import (Agent 1 모델 의존성 격리)

    counters = {"inserted": 0, "updated": 0, "unchanged": 0}

    for spec in agents:
        agent_type = spec["agent_type"]
        existing = (
            await session.execute(select(Agent).where(Agent.agent_type == agent_type))
        ).scalar_one_or_none()

        if existing is None:
            counters["inserted"] += 1
            if not dry_run:
                session.add(
                    Agent(
                        agent_type=agent_type,
                        name=spec["name"],
                        description=spec["description"],
                        common_tags=list(spec["common_tags"]),
                        data_types=list(spec["data_types"]),
                    )
                )
            logger.info("INSERT agent %s", agent_type)
            continue

        # 동일 여부 비교 (멱등 카운팅)
        same = (
            existing.name == spec["name"]
            and existing.description == spec["description"]
            and list(existing.common_tags or []) == list(spec["common_tags"])
            and list(existing.data_types or []) == list(spec["data_types"])
        )
        if same:
            counters["unchanged"] += 1
            logger.info("UNCHANGED agent %s", agent_type)
            continue

        counters["updated"] += 1
        if not dry_run:
            existing.name = spec["name"]
            existing.description = spec["description"]
            existing.common_tags = list(spec["common_tags"])
            existing.data_types = list(spec["data_types"])
        logger.info("UPDATE agent %s", agent_type)

    if not dry_run:
        await session.commit()
    else:
        await session.rollback()

    return counters


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m api.seed",
        description="Seed standard agents (idempotent upsert).",
    )
    p.add_argument(
        "--db-url",
        default=None,
        help="SQLAlchemy async DB URL (overrides settings.database_url).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned changes without writing to DB.",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="-v INFO, -vv DEBUG.",
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


async def _run(db_url: str | None, dry_run: bool) -> dict[str, int]:
    if db_url:
        engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
        owns_engine = True
    else:
        from api.db.base import engine  # type: ignore  # noqa: I001

        owns_engine = False

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            return await seed_agents(session, dry_run=dry_run)
    finally:
        if owns_engine:
            await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _setup_logging(args.verbose)

    try:
        counters = asyncio.run(_run(args.db_url, args.dry_run))
    except Exception as exc:  # noqa: BLE001
        logger.exception("seed failed")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    total = sum(counters.values())
    print(
        f"[{mode}] agents seed done: "
        f"inserted={counters['inserted']} "
        f"updated={counters['updated']} "
        f"unchanged={counters['unchanged']} "
        f"total={total}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["main", "seed_agents"]
