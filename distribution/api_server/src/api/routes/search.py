"""``/api/search`` — 태그/FTS/시맨틱 검색 + 다층(faceted) 필터링.

엔드포인트:
    - ``GET /api/search``           : 단일 모드 검색 (tag/fts/semantic).
    - ``GET /api/search/faceted``   : 다축 필터 + facets 응답 (작은 AI 가
      어떤 축으로 좁힐지 안내).
    - ``GET /api/search/by-tags``   : 태그 매칭 (any/all 모드).
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import get_session
from api.db.models import Record
from api.services.search_svc import (
    fts_search,
    semantic_search,
    tag_search,
)
from api.services.sql_compat import array_overlap

from ._schemas import RecordOut

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("")
@router.get("/", include_in_schema=False)
async def search(
    mode: Literal["tag", "fts", "semantic"] = Query("fts"),
    q: str | None = Query(None),
    tags: list[str] | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    log.info(
        "search: mode=%s q=%s tags=%s limit=%s offset=%s",
        mode, q, tags, limit, offset,
    )
    if mode == "tag":
        if not tags:
            raise HTTPException(
                status_code=400,
                detail="mode=tag requires at least one 'tags' query parameter",
            )
        rows, total = await tag_search(session, tags, limit=limit, offset=offset)
        return {
            "mode": "tag",
            "tags": tags,
            "items": [RecordOut.model_validate(r).model_dump() for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    if mode == "fts":
        if not q:
            raise HTTPException(status_code=400, detail="mode=fts requires 'q'")
        items, total = await fts_search(session, q, limit=limit, offset=offset)
        return {
            "mode": "fts",
            "q": q,
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    if mode == "semantic":
        if not q:
            raise HTTPException(status_code=400, detail="mode=semantic requires 'q'")
        try:
            items = await semantic_search(session, q, top_k=limit)
        except RuntimeError as exc:
            log.warning("semantic_search embedder error: %s", exc)
            raise HTTPException(
                status_code=503,
                detail=f"semantic search unavailable: {exc}",
            ) from exc
        return {
            "mode": "semantic",
            "q": q,
            "items": items,
            "total": len(items),
            "limit": limit,
            "offset": 0,
        }

    raise HTTPException(status_code=400, detail=f"unknown mode: {mode}")


# ---------------------------------------------------------------------------
# /api/search/faceted — 다축 필터 + facet 카운트
# ---------------------------------------------------------------------------
def _record_to_item(rec: Record) -> dict[str, Any]:
    """Faceted 응답용 record 요약 (RecordOut 보다 가벼움)."""
    return {
        "id": rec.id,
        "data_type": rec.data_type,
        "title": rec.title,
        "summary": (rec.summary or "")[:200],
        "tags": list(rec.tags or []),
        "agents": list(rec.agents or []),
        "domain": rec.domain,
        "classification": rec.classification,
        "status": rec.status,
        "year": rec.year,
        "quality_score": rec.quality_score,
        "updated_at": rec.updated_at.isoformat() if rec.updated_at else None,
    }


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [s.strip() for s in value.split(",") if s.strip()]


def _build_facets(
    rows: list[Record],
    *,
    top_n: int = 10,
) -> dict[str, dict[str, int]]:
    """결과 record 집합에서 facet 카운트 산출."""
    dt_c: Counter[str] = Counter()
    tag_c: Counter[str] = Counter()
    domain_c: Counter[str] = Counter()
    agent_c: Counter[str] = Counter()
    status_c: Counter[str] = Counter()
    cls_c: Counter[str] = Counter()
    year_c: Counter[str] = Counter()

    for r in rows:
        if r.data_type:
            dt_c[r.data_type] += 1
        for t in r.tags or []:
            tag_c[t] += 1
        if r.domain:
            domain_c[r.domain] += 1
        for ag in r.agents or []:
            agent_c[ag] += 1
        if r.status:
            status_c[r.status] += 1
        if r.classification:
            cls_c[r.classification] += 1
        if r.year is not None:
            year_c[str(r.year)] += 1

    def _top(counter: Counter[str]) -> dict[str, int]:
        return dict(counter.most_common(top_n))

    return {
        "data_type": _top(dt_c),
        "tags": _top(tag_c),
        "domain": _top(domain_c),
        "agent": _top(agent_c),
        "status": _top(status_c),
        "classification": _top(cls_c),
        "year": _top(year_c),
    }


@router.get("/faceted")
async def search_faceted(
    q: str | None = Query(None, description="키워드 (semantic 또는 fts)"),
    mode: Literal["semantic", "fts"] = Query(
        "semantic", description="키워드 매칭 모드"
    ),
    data_type: str | None = Query(
        None, description="DOC,DATA 등 콤마 구분"
    ),
    tags: str | None = Query(
        None, description="콤마 구분 태그 (AND)"
    ),
    agent: str | None = Query(None, description="agent_type"),
    domain: str | None = Query(None),
    classification: str | None = Query(None),
    status: str | None = Query(None),
    year_from: int | None = Query(None, ge=2000, le=2099),
    year_to: int | None = Query(None, ge=2000, le=2099),
    min_quality: int | None = Query(None, ge=0, le=100),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """다축 필터 (AND) + facet 카운트.

    facet 응답이 *작은 AI 가 다음 query 를 어떻게 좁힐지* 안내하는 신호다.
    """
    log.info(
        "search/faceted: q=%s mode=%s data_type=%s tags=%s agent=%s domain=%s "
        "year=[%s,%s] min_quality=%s",
        q, mode, data_type, tags, agent, domain, year_from, year_to, min_quality,
    )

    # ---- 후보군 결정 -----------------------------------------------------
    # q 가 있으면 search_svc 의 fts/semantic 으로 후보 record_id 풀을 좁히고,
    # 없으면 records 테이블에서 시작.
    candidate_ids: set[str] | None = None
    semantic_score_map: dict[str, float] = {}
    if q and q.strip():
        if mode == "semantic":
            try:
                # 더 큰 풀을 잡아둔다 (다축 필터가 추가로 줄이므로).
                sem_items = await semantic_search(
                    session, q, top_k=max(limit * 5, 50)
                )
            except RuntimeError as exc:
                log.warning("faceted semantic search unavailable: %s", exc)
                # semantic 실패 → fts 폴백.
                fts_items, _ = await fts_search(session, q, limit=200, offset=0)
                candidate_ids = {it["record_id"] for it in fts_items}
            else:
                candidate_ids = {it["record_id"] for it in sem_items}
                for it in sem_items:
                    semantic_score_map[it["record_id"]] = float(it.get("score", 0))
        else:  # fts
            fts_items, _ = await fts_search(session, q, limit=200, offset=0)
            candidate_ids = {it["record_id"] for it in fts_items}

    stmt = select(Record).where(Record.deleted_at.is_(None))
    if candidate_ids is not None:
        if not candidate_ids:
            return _empty_faceted(q, limit, offset)
        stmt = stmt.where(Record.id.in_(list(candidate_ids)))

    # 단일 컬럼 동등 필터
    dt_list = _split_csv(data_type)
    if dt_list:
        stmt = stmt.where(Record.data_type.in_(dt_list))
    if domain:
        stmt = stmt.where(Record.domain == domain)
    if classification:
        stmt = stmt.where(Record.classification == classification)
    if status:
        stmt = stmt.where(Record.status == status)
    if year_from is not None:
        stmt = stmt.where(Record.year >= year_from)
    if year_to is not None:
        stmt = stmt.where(Record.year <= year_to)
    if min_quality is not None:
        stmt = stmt.where(Record.quality_score >= min_quality)

    # ARRAY 필터 — agent / tags. PG 면 SQL, SQLite 면 파이썬 후필터.
    pyfilters = []
    if agent:
        pred = array_overlap(Record.agents, [agent], session)
        stmt = stmt.where(pred.where_clause)
        if pred.python_filter is not None:
            pyfilters.append(pred)

    tag_list = _split_csv(tags)
    if tag_list:
        from api.services.sql_compat import array_contains
        pred = array_contains(Record.tags, tag_list, session)
        stmt = stmt.where(pred.where_clause)
        if pred.python_filter is not None:
            pyfilters.append(pred)

    # ---- 실행 + 후필터 ---------------------------------------------------
    all_rows = (await session.execute(stmt)).scalars().unique().all()
    rows_list = list(all_rows)
    for pred in pyfilters:
        rows_list = pred.apply_python(rows_list)

    # 정렬 — semantic score 가 있으면 그것, 없으면 updated_at desc.
    if semantic_score_map:
        rows_list.sort(
            key=lambda r: (
                -semantic_score_map.get(r.id, 0.0),
                -(r.updated_at.timestamp() if r.updated_at else 0),
            )
        )
    else:
        rows_list.sort(
            key=lambda r: (
                r.updated_at or datetime(1970, 1, 1, tzinfo=timezone.utc),
                r.id,
            ),
            reverse=True,
        )

    total = len(rows_list)
    page = rows_list[offset : offset + limit]

    # ---- facet 산출 -----------------------------------------------------
    # facet 은 **필터가 적용된 결과 집합** 위에서 계산 — 다음 좁힘 후보를 안내.
    facets = _build_facets(rows_list)

    items = [_record_to_item(r) for r in page]
    if semantic_score_map:
        for it in items:
            sc = semantic_score_map.get(it["id"])
            if sc is not None:
                it["score"] = round(sc, 4)

    return {
        "q": q,
        "mode": mode if q else None,
        "filters": {
            "data_type": dt_list or None,
            "tags": tag_list or None,
            "agent": agent,
            "domain": domain,
            "classification": classification,
            "status": status,
            "year_from": year_from,
            "year_to": year_to,
            "min_quality": min_quality,
        },
        "total": total,
        "items": items,
        "facets": facets,
        "limit": limit,
        "offset": offset,
    }


def _empty_faceted(q: str | None, limit: int, offset: int) -> dict:
    return {
        "q": q,
        "mode": None,
        "filters": {},
        "total": 0,
        "items": [],
        "facets": {
            "data_type": {},
            "tags": {},
            "domain": {},
            "agent": {},
            "status": {},
            "classification": {},
            "year": {},
        },
        "limit": limit,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# /api/search/by-tags — any/all 매칭
# ---------------------------------------------------------------------------
@router.get("/by-tags")
async def search_by_tags(
    tags: str = Query(..., description="콤마 구분 태그 (예: IGA,NURBS)"),
    match: Literal["any", "all"] = Query(
        "all", description="any (OR) / all (AND, default)"
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """태그 매칭 — any/all 모드."""
    tag_list = _split_csv(tags)
    if not tag_list:
        raise HTTPException(
            status_code=400, detail="'tags' must contain at least one value"
        )

    if match == "all":
        rows, total = await tag_search(
            session, tag_list, limit=limit, offset=offset
        )
        items = [_record_to_item(r) for r in rows]
    else:  # any
        # array_overlap 사용 — PG 면 SQL, SQLite 면 파이썬 후필터.
        pred = array_overlap(Record.tags, tag_list, session)
        stmt = (
            select(Record)
            .where(Record.deleted_at.is_(None))
            .where(pred.where_clause)
            .order_by(Record.updated_at.desc(), Record.id.desc())
        )
        if pred.python_filter is not None:
            all_rows = (await session.execute(stmt)).scalars().unique().all()
            rows_list = pred.apply_python(list(all_rows))
            total = len(rows_list)
            page = rows_list[offset : offset + limit]
        else:
            page_q = stmt.limit(limit).offset(offset)
            page = (await session.execute(page_q)).scalars().unique().all()
            # total 카운트 — 단일 쿼리.
            from sqlalchemy import func
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = int((await session.execute(count_stmt)).scalar_one())
        items = [_record_to_item(r) for r in page]

    return {
        "tags": tag_list,
        "match": match,
        "total": total,
        "items": items,
        "limit": limit,
        "offset": offset,
    }


__all__ = ["router"]
