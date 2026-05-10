"""``/api/doc-types`` — doc_type taxonomy CRUD (Migration 0011)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import get_session
from api.services import doc_type_svc

from ._schemas import DocTypeIn, DocTypeOut, DocTypePatch

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/doc-types", tags=["doc-types"])


@router.get(
    "",
    response_model=list[DocTypeOut],
    response_model_exclude_none=True,
)
@router.get(
    "/",
    response_model=list[DocTypeOut],
    response_model_exclude_none=True,
    include_in_schema=False,
)
async def list_doc_types(
    session: AsyncSession = Depends(get_session),
) -> list[DocTypeOut]:
    rows = await doc_type_svc.list_doc_types(session)
    return [DocTypeOut.model_validate(r) for r in rows]


@router.get(
    "/{code}",
    response_model=DocTypeOut,
    response_model_exclude_none=True,
)
async def get_doc_type(
    code: str,
    session: AsyncSession = Depends(get_session),
) -> DocTypeOut:
    dt = await doc_type_svc.get_doc_type(session, code)
    if dt is None:
        raise HTTPException(status_code=404, detail=f"doc_type not found: {code}")
    return DocTypeOut.model_validate(dt)


@router.post(
    "",
    response_model=DocTypeOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/",
    response_model=DocTypeOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def create_doc_type(
    payload: DocTypeIn,
    session: AsyncSession = Depends(get_session),
) -> DocTypeOut:
    try:
        dt = await doc_type_svc.create_doc_type(session, payload.model_dump())
    except ValueError as exc:
        log.info("create_doc_type conflict: %s", payload.code)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DocTypeOut.model_validate(dt)


@router.patch(
    "/{code}", response_model=DocTypeOut, response_model_exclude_none=True
)
async def patch_doc_type(
    code: str,
    patch: DocTypePatch,
    session: AsyncSession = Depends(get_session),
) -> DocTypeOut:
    dt = await doc_type_svc.update_doc_type(
        session, code, patch.model_dump(exclude_unset=True)
    )
    if dt is None:
        raise HTTPException(status_code=404, detail=f"doc_type not found: {code}")
    return DocTypeOut.model_validate(dt)


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_doc_type(
    code: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    ok = await doc_type_svc.delete_doc_type(session, code)
    if not ok:
        raise HTTPException(status_code=404, detail=f"doc_type not found: {code}")


__all__ = ["router"]
