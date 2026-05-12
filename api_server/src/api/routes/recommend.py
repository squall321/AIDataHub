"""``/api/recommend`` — agent 추천 라우터 (의미검색 집계)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import get_session
from api.services import recommend_svc

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recommend", tags=["recommend"])


class RecommendRequest(BaseModel):
    q: str = Field(..., min_length=1, description="자연어 쿼리 (한/영).")
    top_k: int = Field(5, ge=1, le=20)
    candidate_sections: int = Field(50, ge=5, le=200)


@router.post("/agents", summary="자연어 → 추천 agents (의미검색 집계)")
async def recommend_agents_route(
    payload: RecommendRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    ranked = await recommend_svc.recommend_agents(
        session,
        query=payload.q,
        top_k=payload.top_k,
        candidate_sections=payload.candidate_sections,
    )
    return {
        "query": payload.q,
        "candidate_sections": payload.candidate_sections,
        "agents": ranked,
    }
