"""doc_types taxonomy CRUD (Migration 0011).

``data_type`` (구조 분류 7-enum) 위에 얹는 soft taxonomy. 등록되지 않은
``records.doc_type`` 값은 인제스트 시 warn-only.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import DocType


async def list_doc_types(session: AsyncSession) -> list[DocType]:
    rows = (
        await session.execute(select(DocType).order_by(DocType.code))
    ).scalars().all()
    return list(rows)


async def get_doc_type(session: AsyncSession, code: str) -> DocType | None:
    return (
        await session.execute(select(DocType).where(DocType.code == code))
    ).scalar_one_or_none()


async def create_doc_type(session: AsyncSession, payload: dict) -> DocType:
    dt = DocType(
        code=payload["code"],
        name=payload.get("name", payload["code"]),
        description=payload.get("description", ""),
        expected_sections=list(payload.get("expected_sections", []) or []),
    )
    session.add(dt)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ValueError(f"doc_type already exists: {dt.code}") from exc
    await session.commit()
    await session.refresh(dt)
    return dt


async def update_doc_type(
    session: AsyncSession, code: str, patch: dict
) -> DocType | None:
    dt = await get_doc_type(session, code)
    if dt is None:
        return None
    for key in ("name", "description", "expected_sections"):
        if key in patch and patch[key] is not None:
            setattr(dt, key, patch[key])
    await session.commit()
    await session.refresh(dt)
    return dt


async def delete_doc_type(session: AsyncSession, code: str) -> bool:
    dt = await get_doc_type(session, code)
    if dt is None:
        return False
    await session.delete(dt)
    await session.commit()
    return True


__all__ = [
    "create_doc_type",
    "delete_doc_type",
    "get_doc_type",
    "list_doc_types",
    "update_doc_type",
]
