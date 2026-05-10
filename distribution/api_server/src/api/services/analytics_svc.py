"""크로스 레코드 분석 로직.

- ``distribution`` : ``data_type``/``team``/``group``/``year`` 별 카운트
- ``common_tags``  : 에이전트 범위 내 상위 태그 빈도
- ``cross_agent``  : 두 에이전트 모두 사용하는 레코드 교집합
- ``timeline``     : 연도별 월간 레코드 카운트

ARRAY 술어는 :mod:`api.services.sql_compat` 헬퍼 경유.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Record

from .sql_compat import array_overlap, array_unnest_count

log = logging.getLogger(__name__)


async def distribution(session: AsyncSession) -> dict:
    """``data_type``/``team``/``group``/``year`` 별 레코드 수."""
    by_type: dict[str, int] = {}
    by_division: dict[str, int] = {}
    by_team: dict[str, int] = {}
    by_year: dict[str, int] = {}

    rows = (
        await session.execute(
            select(Record.data_type, func.count()).group_by(Record.data_type)
        )
    ).all()
    by_type = {k: int(v) for k, v in rows}

    rows = (
        await session.execute(
            select(Record.team, func.count()).group_by(Record.team)
        )
    ).all()
    by_division = {k: int(v) for k, v in rows}

    rows = (
        await session.execute(select(Record.group, func.count()).group_by(Record.group))
    ).all()
    by_team = {k: int(v) for k, v in rows}

    rows = (
        await session.execute(select(Record.year, func.count()).group_by(Record.year))
    ).all()
    by_year = {str(k): int(v) for k, v in rows}

    return {
        "by_type": by_type,
        "by_division": by_division,
        "by_team": by_team,
        "by_year": by_year,
    }


async def common_tags(
    session: AsyncSession, agent: str, *, limit: int = 20
) -> list[dict]:
    """에이전트 범위 내 상위 태그 빈도.

    PG 의 ``unnest(tags) GROUP BY`` 가 가장 효율적이지만, 다이얼렉트 호환을
    위해 헬퍼가 dialect-aware 폴백을 제공한다.
    """
    pred = array_overlap(Record.agents, [agent], session)
    py_preds = [pred] if pred.python_filter is not None else []
    rows = await array_unnest_count(
        session,
        Record.tags,
        where_clauses=[pred.where_clause],
        python_predicates=py_preds,
        limit=limit,
    )
    return [{"tag": tag, "count": count} for tag, count in rows]


async def cross_agent(
    session: AsyncSession, agents: Sequence[str]
) -> dict:
    """주어진 에이전트들이 모두 다루는 레코드 교집합."""
    agents = list(agents)
    if not agents:
        return {"agents": [], "shared_records": [], "count": 0}

    id_sets: list[set[str]] = []
    for ag in agents:
        pred = array_overlap(Record.agents, [ag], session)
        if pred.python_filter is not None:
            row_objs = (
                (await session.execute(select(Record).where(pred.where_clause)))
                .scalars()
                .unique()
                .all()
            )
            row_objs = pred.apply_python(row_objs)
            id_sets.append({r.id for r in row_objs})
        else:
            rows = (
                (await session.execute(select(Record.id).where(pred.where_clause)))
                .scalars()
                .all()
            )
            id_sets.append(set(rows))

    if not id_sets:
        shared_ids: set[str] = set()
    else:
        shared_ids = id_sets[0]
        for s in id_sets[1:]:
            shared_ids = shared_ids & s

    shared: list[dict] = []
    if shared_ids:
        rows = (
            (await session.execute(select(Record).where(Record.id.in_(shared_ids))))
            .scalars()
            .all()
        )
        shared = [
            {"id": r.id, "title": r.title, "data_type": r.data_type}
            for r in rows
        ]
    return {
        "agents": agents,
        "shared_records": shared,
        "count": len(shared),
    }


async def usage_top(
    session: AsyncSession, *, limit: int = 20
) -> list[dict]:
    """``read_count`` 상위 레코드 목록 (Migration 0008).

    최근 접근 시각(``last_accessed_at``)을 보조 정렬 키로 사용한다.
    soft-deleted 레코드는 제외.
    """
    limit = max(1, min(int(limit or 20), 100))
    stmt = (
        select(
            Record.id,
            Record.title,
            Record.data_type,
            Record.read_count,
            Record.last_accessed_at,
        )
        .where(Record.deleted_at.is_(None))
        .order_by(
            Record.read_count.desc(),
            Record.last_accessed_at.desc(),
            Record.id.desc(),
        )
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "data_type": r.data_type,
            "read_count": int(r.read_count or 0),
            "last_accessed_at": (
                r.last_accessed_at.isoformat() if r.last_accessed_at else None
            ),
        }
        for r in rows
    ]


async def timeline(session: AsyncSession, year: int) -> dict:
    """연도별 월간 레코드 생성 카운트."""
    stmt = (
        select(extract("month", Record.created_at).label("month"), func.count())
        .where(Record.year == year)
        .group_by("month")
        .order_by("month")
    )
    rows = (await session.execute(stmt)).all()
    monthly_map = {int(month): int(count) for month, count in rows if month is not None}
    monthly = [
        {"month": m, "count": monthly_map.get(m, 0)} for m in range(1, 13)
    ]
    return {"year": year, "monthly": monthly}


__all__ = [
    "common_tags",
    "cross_agent",
    "distribution",
    "timeline",
    "usage_top",
]
