"""``/api/records/{id}/attachments`` 와 ``/api/attachments`` — 첨부 조회 라우터.

엔드포인트:

- ``GET  /api/records/{record_id}/attachments``
    레코드에 속한 모든 첨부를 ``number`` 오름차순으로 반환.
- ``GET  /api/records/{record_id}/attachments/{att_id}``
    특정 첨부 단건 조회 (404 시 not found).
- ``GET  /api/attachments``
    ``kind`` / ``record_id`` 필터로 첨부 목록 조회 (전역 검색용).

응답 모델은 :class:`api.schemas.AttachmentOut` 를 사용한다.
바이너리 자체는 ``/attachments/{record_id}/A{nnn}.{ext}`` 정적 마운트로
서빙된다 (이 라우터에서는 메타만 다룬다).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import get_session
from api.db.models import Record, RecordAttachment
from api.schemas.attachment import ATTACHMENT_KINDS, AttachmentOut

log = logging.getLogger(__name__)

router = APIRouter(tags=["attachments"])


# ---------------------------------------------------------------------------
# /api/records/{record_id}/attachments
# ---------------------------------------------------------------------------
@router.get(
    "/api/records/{record_id}/attachments",
    response_model=list[AttachmentOut],
    response_model_exclude_none=True,
)
async def list_record_attachments(
    record_id: str,
    kind: str | None = Query(None, description="필터: 첨부 종류 (figure/document/...)"),
    session: AsyncSession = Depends(get_session),
) -> list[AttachmentOut]:
    """레코드의 첨부 목록 (number 오름차순)."""
    # record 존재 확인 — 빈 첨부 리스트와 404 를 구분하기 위함.
    rec = (
        await session.execute(select(Record).where(Record.id == record_id))
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(
            status_code=404, detail=f"record not found: {record_id}"
        )

    stmt = select(RecordAttachment).where(
        RecordAttachment.record_id == record_id
    )
    if kind:
        if kind not in ATTACHMENT_KINDS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"invalid kind {kind!r}; expected one of {ATTACHMENT_KINDS}"
                ),
            )
        stmt = stmt.where(RecordAttachment.kind == kind)
    stmt = stmt.order_by(RecordAttachment.number.asc())

    rows = (await session.execute(stmt)).scalars().all()
    return [AttachmentOut.model_validate(r) for r in rows]


@router.get(
    "/api/records/{record_id}/attachments/{att_id}",
    response_model=AttachmentOut,
    response_model_exclude_none=True,
)
async def get_record_attachment(
    record_id: str,
    att_id: str,
    session: AsyncSession = Depends(get_session),
) -> AttachmentOut:
    """특정 첨부 단건 조회."""
    row = (
        await session.execute(
            select(RecordAttachment).where(
                RecordAttachment.record_id == record_id,
                RecordAttachment.id == att_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"attachment not found: record={record_id} id={att_id}",
        )
    return AttachmentOut.model_validate(row)


# ---------------------------------------------------------------------------
# /api/attachments  — 전역 검색 (kind / record_id 필터)
# ---------------------------------------------------------------------------
@router.get(
    "/api/attachments",
    response_model=list[AttachmentOut],
    response_model_exclude_none=True,
)
async def list_attachments(
    kind: str | None = Query(None, description="필터: 첨부 종류"),
    record_id: str | None = Query(None, description="필터: 레코드 ID"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[AttachmentOut]:
    """첨부 전역 목록. ``kind`` 또는 ``record_id`` 로 필터 가능."""
    if kind and kind not in ATTACHMENT_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid kind {kind!r}; expected one of {ATTACHMENT_KINDS}",
        )

    stmt = select(RecordAttachment)
    if kind:
        stmt = stmt.where(RecordAttachment.kind == kind)
    if record_id:
        stmt = stmt.where(RecordAttachment.record_id == record_id)
    stmt = stmt.order_by(
        RecordAttachment.record_id.asc(),
        RecordAttachment.number.asc(),
    ).limit(limit).offset(offset)

    rows = (await session.execute(stmt)).scalars().all()
    return [AttachmentOut.model_validate(r) for r in rows]


__all__ = ["router"]
