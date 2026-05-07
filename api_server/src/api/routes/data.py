"""``/api/data`` — Cline SR 코어 엔드포인트.

에이전트 식별자(``agent``) 를 키로, 해당 에이전트가 사용 가능한 레코드 후보 중
주어진 ``query`` 와 가장 관련성 높은 결과를 반환한다.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import get_session
from api.services.search_svc import data_for_agent

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data"])


@router.get("")
@router.get("/", include_in_schema=False)
async def get_data(
    agent: str = Query(..., description="에이전트 식별자 (예: iga-analyst)"),
    query: str | None = Query(None, description="자연어 검색어"),
    data_types: list[str] | None = Query(
        None, description="필터: data_type 화이트리스트"
    ),
    limit: int = Query(5, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Cline SR 가 호출할 단일 데이터 검색 엔드포인트."""
    log.info(
        "get_data: agent=%s query=%s data_types=%s limit=%s",
        agent, query, data_types, limit,
    )
    return await data_for_agent(
        session,
        agent=agent,
        query=query,
        data_types=data_types,
        limit=limit,
    )


__all__ = ["router"]
