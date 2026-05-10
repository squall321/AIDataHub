"""Auto-sequence generation for record IDs.

When a user submits ``/api/convert/ingest`` with ``seq=0`` or empty, the backend
assigns the next available sequence number for the natural key tuple
``(data_type, team, group, year)``.

Implementation:
    - ``next_seq()`` runs ``SELECT COALESCE(MAX(seq), 0) + 1 FROM records WHERE ...``.
    - On PostgreSQL we issue the SELECT inside the caller's transaction;
      ``UNIQUE (data_type, team, group, year, seq)`` (Migration 0001) guards
      against true races by raising IntegrityError on commit, which the caller
      can retry.
    - On SQLite (test env) the same query works under the implicit locking
      behavior — single-writer assumption holds for tests.

Notes:
    Single-writer assumption is fine for the current cycle. Distributed
    concurrent inserters would need an advisory lock or a SERIALIZABLE
    transaction with retry — see ``Items deferred`` in the report.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def next_seq(
    session: AsyncSession,
    *,
    data_type: str,
    team: str,
    group: str,
    year: int,
) -> int:
    """Return ``MAX(seq) + 1`` for the natural key tuple.

    Args:
        session: AsyncSession.
        data_type: ``DOC | DATA | SIM | CAD | LOG | FORM | OTHER``.
        team: team code (uppercased).
        group: group code (uppercased).
        year: 4-digit year.

    Returns:
        Next available seq integer (1 if no rows yet).
    """
    # Lazy import — keeps the module importable when models metadata isn't
    # registered yet (CLI / test edge cases).
    from ..db.models import Record

    stmt = (
        select(func.coalesce(func.max(Record.seq), 0) + 1)
        .where(Record.data_type == data_type)
        .where(Record.team == team.upper())
        .where(Record.group == group.upper())
        .where(Record.year == int(year))
    )
    result = await session.execute(stmt)
    val = int(result.scalar_one() or 1)
    logger.info(
        "next_seq: (%s,%s,%s,%s) -> %d",
        data_type,
        team,
        group,
        year,
        val,
    )
    return val


__all__ = ["next_seq"]
