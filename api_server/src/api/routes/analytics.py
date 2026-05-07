"""``/api/analytics`` — 크로스 레코드 통계."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import get_session
from api.services import analytics_svc

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/distribution")
async def distribution(
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await analytics_svc.distribution(session)


@router.get("/common-tags")
async def common_tags(
    agent: str = Query(..., description="에이전트 식별자"),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    log.info("common_tags: agent=%s limit=%s", agent, limit)
    return await analytics_svc.common_tags(session, agent, limit=limit)


@router.get("/cross-agent")
async def cross_agent(
    agents: list[str] = Query(..., description="에이전트 식별자 목록"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not agents:
        raise HTTPException(status_code=400, detail="agents query parameter required")
    log.info("cross_agent: agents=%s", agents)
    return await analytics_svc.cross_agent(session, agents)


@router.get("/timeline")
async def timeline(
    year: int = Query(..., ge=1900, le=3000),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await analytics_svc.timeline(session, year)


__all__ = ["router"]
