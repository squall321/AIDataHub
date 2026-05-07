"""``/api/search`` — 태그/FTS/시맨틱 검색."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import get_session
from api.services.search_svc import fts_search, tag_search

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
        # TODO(pgvector): record_sections.embedding(vector(1536)) 도입 후 활성화.
        return {
            "mode": "semantic",
            "error": "semantic search not yet implemented",
            "suggested": "use mode=fts",
        }

    raise HTTPException(status_code=400, detail=f"unknown mode: {mode}")


__all__ = ["router"]
