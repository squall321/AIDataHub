"""``/api/records`` — 레코드 CRUD.

ARRAY 술어는 :mod:`api.services.sql_compat` 를 통해 방언 호환적으로 처리한다.
직접 ``op('@>')`` / ``op('&&')`` 를 호출하지 않는다.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import get_session
from api.db.models import Record
from api.services.sql_compat import (
    ArrayPredicate,
    array_contains,
    array_overlap,
    paginate_rows,
    summary_ilike,
)

from ._schemas import RecordIn, RecordListResponse, RecordOut, RecordPatch

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/records", tags=["records"])


@router.get(
    "",
    response_model=RecordListResponse,
    response_model_exclude_none=True,
)
@router.get(
    "/",
    response_model=RecordListResponse,
    response_model_exclude_none=True,
    include_in_schema=False,
)
async def list_records(
    data_type: str | None = Query(None),
    division: str | None = Query(None),
    team: str | None = Query(None),
    year: int | None = Query(None),
    agent: list[str] | None = Query(None),
    tag: list[str] | None = Query(None),
    q: str | None = Query(None, description="ILIKE on title + summary"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> RecordListResponse:
    log.info(
        "list_records: data_type=%s division=%s team=%s year=%s "
        "agents=%s tags=%s q=%s limit=%s offset=%s",
        data_type, division, team, year, agent, tag, q, limit, offset,
    )

    stmt = select(Record)
    if data_type:
        stmt = stmt.where(Record.data_type == data_type)
    if division:
        stmt = stmt.where(Record.division == division)
    if team:
        stmt = stmt.where(Record.team == team)
    if year is not None:
        stmt = stmt.where(Record.year == year)

    py_predicates: list[ArrayPredicate] = []
    if agent:
        pred = array_overlap(Record.agents, agent, session)
        stmt = stmt.where(pred.where_clause)
        if pred.python_filter is not None:
            py_predicates.append(pred)
    if tag:
        pred = array_contains(Record.tags, tag, session)
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

    return RecordListResponse(
        items=[RecordOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{record_id}", response_model=RecordOut, response_model_exclude_none=True
)
async def get_record(
    record_id: str,
    session: AsyncSession = Depends(get_session),
) -> RecordOut:
    rec = (
        await session.execute(select(Record).where(Record.id == record_id))
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail=f"record not found: {record_id}")
    return RecordOut.model_validate(rec)


@router.post(
    "",
    response_model=RecordOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/",
    response_model=RecordOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def create_record(
    payload: RecordIn,
    session: AsyncSession = Depends(get_session),
) -> RecordOut:
    data = payload.model_dump()
    rec = Record(**data)
    session.add(rec)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        log.info("create_record duplicate: %s", payload.id)
        raise HTTPException(
            status_code=409,
            detail=f"record already exists or violates constraint: {payload.id}",
        ) from exc
    await session.refresh(rec)
    return RecordOut.model_validate(rec)


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(
    record_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    rec = (
        await session.execute(select(Record).where(Record.id == record_id))
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail=f"record not found: {record_id}")
    await session.delete(rec)
    await session.commit()


@router.patch(
    "/{record_id}", response_model=RecordOut, response_model_exclude_none=True
)
async def patch_record(
    record_id: str,
    patch: RecordPatch,
    session: AsyncSession = Depends(get_session),
) -> RecordOut:
    rec = (
        await session.execute(select(Record).where(Record.id == record_id))
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail=f"record not found: {record_id}")

    data = patch.model_dump(exclude_unset=True)
    for key, value in data.items():
        if value is None and key != "project":
            # ``project`` 만 명시적 None 허용 (nullable 컬럼)
            continue
        setattr(rec, key, value)
    await session.commit()
    await session.refresh(rec)
    return RecordOut.model_validate(rec)


__all__ = ["router"]
