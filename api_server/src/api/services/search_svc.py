"""검색 비즈니스 로직.

- ``tag_search``       : 모든 태그를 포함하는 레코드 (ARRAY @> on PG, 파이썬 후필터 on SQLite)
- ``fts_search``       : 본문/요약 텍스트 ILIKE 기반 단순 FTS
- ``data_for_agent``   : ``/api/data`` 엔드포인트의 핵심 로직

ARRAY/JSONB 등 방언 의존 표현은 모두 :mod:`api.services.sql_compat` 의
헬퍼를 경유한다. 이 모듈은 직접 ``op('@>')`` / ``func.unnest`` 등을 호출하지
않는다.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import AgentRecord, Record, RecordSection

from .sql_compat import (
    ArrayPredicate,
    array_contains,
    array_overlap,
    fts_match,
    paginate_rows,
    summary_ilike,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backward-compat re-exports (old name → new helper).
# Routes 와 다른 서비스 모듈에서 ``from .search_svc import array_overlap`` 형태로
# 참조하던 코드가 있을 수 있으므로 호환을 유지한다.
# ---------------------------------------------------------------------------
def array_contains_all(
    column, values: Sequence[str], session: AsyncSession
) -> ArrayPredicate:
    return array_contains(column, values, session)


def array_overlap_compat(
    column, values: Sequence[str], session: AsyncSession
) -> ArrayPredicate:
    return array_overlap(column, values, session)


# ---------------------------------------------------------------------------
# Tag search
# ---------------------------------------------------------------------------
async def tag_search(
    session: AsyncSession,
    tags: Sequence[str],
    *,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Record], int]:
    """태그 모두 포함(AND) 검색.

    ``Record.tags @> [tags...]`` 의미. SQLite 에서는 파이썬 후필터로 동등한
    동작을 보장한다.
    """
    pred = array_contains(Record.tags, list(tags), session)
    stmt = (
        select(Record)
        .where(pred.where_clause)
        .order_by(Record.updated_at.desc(), Record.id.desc())
    )
    pyfilters = [pred] if pred.python_filter is not None else []
    return await paginate_rows(
        session, stmt, limit=limit, offset=offset, extra_python_predicates=pyfilters
    )


# ---------------------------------------------------------------------------
# FTS-ish search (ILIKE on summary + section text)
# ---------------------------------------------------------------------------
async def fts_search(
    session: AsyncSession,
    q: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """텍스트 검색.

    PostgreSQL: ``to_tsvector('simple', col) @@ plainto_tsquery('simple', q)``
    SQLite (테스트): ``ILIKE %q%`` 폴백.

    ``fts_match`` 헬퍼가 dialect 분기를 담당한다.
    """
    if not q.strip():
        return [], 0

    section_stmt = (
        select(
            Record.id.label("record_id"),
            Record.title.label("title"),
            Record.data_type.label("data_type"),
            Record.tags.label("tags"),
            RecordSection.section_id.label("section_id"),
            RecordSection.title.label("section_title"),
            RecordSection.content_text.label("content_text"),
        )
        .join(RecordSection, RecordSection.record_id == Record.id)
        .where(fts_match(RecordSection.content_text, q, session))
    )

    record_stmt = select(Record).where(
        or_(
            fts_match(Record.title, q, session),
            fts_match(Record.summary, q, session),
        )
    )

    section_rows = (await session.execute(section_stmt.limit(limit * 3))).all()
    record_rows = (await session.execute(record_stmt.limit(limit * 3))).scalars().all()

    seen: set[tuple[str, str | None]] = set()
    items: list[dict] = []
    for row in section_rows:
        key = (row.record_id, row.section_id)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "record_id": row.record_id,
                "title": row.title,
                "data_type": row.data_type,
                "section_id": row.section_id,
                "section_title": row.section_title,
                "snippet": _make_snippet(row.content_text or "", q),
                "tags": list(row.tags or []),
            }
        )
    for rec in record_rows:
        key = (rec.id, None)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "record_id": rec.id,
                "title": rec.title,
                "data_type": rec.data_type,
                "section_id": None,
                "section_title": None,
                "snippet": _make_snippet(rec.summary or "", q),
                "tags": list(rec.tags or []),
            }
        )

    total = len(items)
    return items[offset : offset + limit], total


def _make_snippet(text: str, q: str, *, length: int = 300) -> str:
    if not text:
        return ""
    lower = text.lower()
    needle = q.lower()
    idx = lower.find(needle)
    if idx < 0:
        return text[:length]
    start = max(0, idx - 60)
    end = min(len(text), start + length)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


# ---------------------------------------------------------------------------
# /api/data — Cline SR core
# ---------------------------------------------------------------------------
async def data_for_agent(
    session: AsyncSession,
    agent: str,
    *,
    query: str | None = None,
    data_types: Sequence[str] | None = None,
    limit: int = 5,
) -> dict:
    """에이전트가 사용할 레코드 후보를 반환한다.

    1) ``agent`` ∈ ``records.agents`` 인 레코드 후보군 결정.
    2) DOC 타입은 ``record_sections.content_text`` 에 ILIKE %q% 매칭.
       나머지는 ``records.summary``/``title`` 매칭.
    3) ``AgentRecord.priority`` + 매칭 카운트로 단순 relevance 산출.
    """
    limit = max(1, min(limit, 20))

    overlap_pred = array_overlap(Record.agents, [agent], session)
    candidate_stmt = select(Record).where(overlap_pred.where_clause)
    if data_types:
        candidate_stmt = candidate_stmt.where(Record.data_type.in_(list(data_types)))

    candidates_raw: list[Record] = (
        (await session.execute(candidate_stmt)).scalars().unique().all()
    )
    if overlap_pred.python_filter is not None:
        candidates: list[Record] = overlap_pred.apply_python(candidates_raw)
    else:
        candidates = list(candidates_raw)

    priority_map: dict[str, int] = {}
    if candidates:
        ids = [r.id for r in candidates]
        prio_rows = (
            await session.execute(
                select(AgentRecord.record_id, AgentRecord.priority).where(
                    (AgentRecord.agent_type == agent)
                    & AgentRecord.record_id.in_(ids)
                )
            )
        ).all()
        priority_map = {row.record_id: int(row.priority) for row in prio_rows}

    results: list[dict] = []
    q = (query or "").strip()
    pattern_present = bool(q)

    for rec in candidates:
        priority = priority_map.get(rec.id, 1)

        if rec.data_type == "DOC" and pattern_present:
            sec_rows = (
                await session.execute(
                    select(RecordSection)
                    .where(RecordSection.record_id == rec.id)
                    .where(summary_ilike(RecordSection.content_text, q))
                )
            ).scalars().all()
            if not sec_rows:
                results.append(
                    {
                        "record_id": rec.id,
                        "title": rec.title,
                        "data_type": rec.data_type,
                        "section_id": None,
                        "section_title": None,
                        "snippet": _make_snippet(rec.summary or "", q),
                        "relevance": _score(priority, 0),
                        "tags": list(rec.tags or []),
                    }
                )
                continue
            for sec in sec_rows:
                hits = (sec.content_text or "").lower().count(q.lower()) if q else 0
                results.append(
                    {
                        "record_id": rec.id,
                        "title": rec.title,
                        "data_type": rec.data_type,
                        "section_id": sec.section_id,
                        "section_title": sec.title,
                        "snippet": _make_snippet(sec.content_text or "", q),
                        "relevance": _score(priority, hits),
                        "tags": list(rec.tags or []),
                    }
                )
        else:
            haystack = " ".join([rec.title or "", rec.summary or ""])
            if q and q.lower() not in haystack.lower():
                hits = 0
            else:
                hits = haystack.lower().count(q.lower()) if q else 0
            results.append(
                {
                    "record_id": rec.id,
                    "title": rec.title,
                    "data_type": rec.data_type,
                    "section_id": None,
                    "section_title": None,
                    "snippet": _make_snippet(rec.summary or rec.title or "", q),
                    "relevance": _score(priority, hits),
                    "tags": list(rec.tags or []),
                }
            )

    results.sort(key=lambda r: r["relevance"], reverse=True)
    total = len(results)
    return {
        "agent": agent,
        "query": query,
        "results": results[:limit],
        "total_matched": total,
    }


def _score(priority: int, hits: int) -> float:
    """단순 relevance = priority 가중 + 매칭 횟수.

    priority 범위 1-5 가정. 매칭 0 회 → 0.1 floor.
    """
    base = priority / 5.0
    boost = min(hits * 0.1, 0.5)
    score = base * 0.7 + boost + 0.05
    return round(min(score, 1.0), 3)


__all__ = [
    "array_contains_all",
    "array_overlap_compat",
    "data_for_agent",
    "fts_search",
    "tag_search",
]
