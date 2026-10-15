# 레코드 정형 필터 조회 — REST 라우트와 MCP 도구가 공유하는 단일 쿼리 로직.
"""정형 조건(team/group/doc_type/data_type/tags/agent/q)으로 레코드 목록 조회.

기존에는 routes/records.py 의 list_records 핸들러 안에 인라인으로 있던 stmt
빌드를 서비스로 추출 — REST(`/api/records`)와 MCP `list_records` 도구가 같은
필터·페이징 로직을 호출한다 (단일 진실원천). sql_compat 헬퍼 재사용.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models import Record, RecordSection
from .sql_compat import (
    FTS_OR_MAX_TOKENS,
    ArrayPredicate,
    array_contains,
    array_overlap,
    paginate_rows,
)

log = logging.getLogger(__name__)


def _q_token(tok: str, *, body: bool):
    """낱말 하나 — 제목·요약에 그 글자가 있으면 참. '%'·'_' 는 글자 그대로다.

    body 면 본문 절(record_sections)도 본다. 낱말마다 EXISTS 를 따로 두므로
    두 낱말이 서로 다른 절에 있어도 맞는다.
    """
    terms = [
        Record.title.icontains(tok, autoescape=True),
        Record.summary.icontains(tok, autoescape=True),
    ]
    if body:
        terms.append(exists().where(
            RecordSection.record_id == Record.id,
            RecordSection.content_text.icontains(tok, autoescape=True),
        ))
    return or_(*terms)


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
    # 낱말마다 AND — 통째 구절 ILIKE 는 순서가 바뀌거나 사이에 낱말이 끼면 0건이었다.
    # 「제목에 모든 낱말」 OR 「요약에 모든 낱말」로 짜지 않는다 — 한 낱말은 제목에, 다른 낱말은
    # 요약에 있는 카드를 놓친다. 본문은 스위치를 켰고 전문가 필터가 있을 때만 본다(config 주석).
    body = settings.aidh_records_q_body_search and bool(agents)
    words = (q or "").split()
    if len(words) > FTS_OR_MAX_TOKENS:
        log.warning("query_records: q 는 앞 %d낱말만 조건에 건다 — 뒤 %d낱말은 빠졌다(q %d낱말)",
                    FTS_OR_MAX_TOKENS, len(words) - FTS_OR_MAX_TOKENS, len(words))
    tokens = words[:FTS_OR_MAX_TOKENS]
    if tokens:
        stmt = stmt.where(and_(*[_q_token(t, body=body) for t in tokens]))

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
    """MCP 응답용 경량 요약 (content 본문 제외 — 토큰 절약).

    요약(summary)은 자르지 않는다. 예전엔 앞 200자였고 '…' 도 길이도 없었다 — 본문을 빼는
    것으로 충분하고, 요약을 깎을 까닭은 1백만 토큰 창에 없다.
    """
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
        "summary": rec.summary or "",
    }


__all__ = ["query_records", "to_summary"]
