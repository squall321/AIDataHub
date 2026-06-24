# 레코드 정형 필터 조회 — REST 라우트와 MCP 도구가 공유하는 단일 쿼리 로직.
"""정형 조건(team/group/doc_type/data_type/tags/agent/q)으로 레코드 목록 조회.

기존에는 routes/records.py 의 list_records 핸들러 안에 인라인으로 있던 stmt
빌드를 서비스로 추출 — REST(`/api/records`)와 MCP `list_records` 도구가 같은
필터·페이징 로직을 호출한다 (단일 진실원천). sql_compat 헬퍼 재사용.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Record
from .sql_compat import (
    ArrayPredicate,
    array_contains,
    array_overlap,
    paginate_rows,
    summary_ilike,
)


async def query_records(
    session: AsyncSession,
    *,
    data_type: str | None = None,
    team: str | None = None,
    group: str | None = None,
    doc_type: str | None = None,
    year: int | None = None,
    agents: list[str] | None = None,
    tags: list[str] | None = None,
    q: str | None = None,
    include_deleted: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Record], int]:
    """필터 조건으로 레코드 (rows, total) 반환. updated_at 내림차순."""
    stmt = select(Record)
    if data_type:
        stmt = stmt.where(Record.data_type == data_type)
    if team:
        stmt = stmt.where(Record.team == team)
    if group:
        stmt = stmt.where(Record.group == group)
    if doc_type:
        stmt = stmt.where(Record.doc_type == doc_type)
    if year is not None:
        stmt = stmt.where(Record.year == year)
    if not include_deleted:
        stmt = stmt.where(Record.deleted_at.is_(None))

    py_predicates: list[ArrayPredicate] = []
    if agents:
        pred = array_overlap(Record.agents, agents, session)
        stmt = stmt.where(pred.where_clause)
        if pred.python_filter is not None:
            py_predicates.append(pred)
    if tags:
        pred = array_contains(Record.tags, tags, session)
        stmt = stmt.where(pred.where_clause)
        if pred.python_filter is not None:
            py_predicates.append(pred)
    if q:
        stmt = stmt.where(
            or_(summary_ilike(Record.title, q), summary_ilike(Record.summary, q))
        )

    stmt = stmt.order_by(Record.updated_at.desc(), Record.id.desc())

    rows, total = await paginate_rows(
        session,
        stmt,
        limit=limit,
        offset=offset,
        extra_python_predicates=py_predicates,
    )
    return list(rows), total


def to_summary(rec: Record) -> dict[str, Any]:
    """MCP 응답용 경량 요약 (content 본문 제외 — 토큰 절약)."""
    return {
        "id": rec.id,
        "title": rec.title,
        "data_type": rec.data_type,
        "team": rec.team,
        "group": rec.group,
        "year": rec.year,
        "doc_type": rec.doc_type,
        "tags": list(rec.tags or []),
        "agents": list(rec.agents or []),
        "summary": (rec.summary or "")[:200],
    }


__all__ = ["query_records", "to_summary"]
