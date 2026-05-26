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
    top_k_tools: int = Field(
        3,
        ge=0,
        le=10,
        description="Wave-7 P1: 응답에 동봉할 relevant_tools 개수 (0=비활성).",
    )


@router.post("/agents", summary="자연어 → 추천 agents (의미검색 집계) + relevant_tools")
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
    # Wave-7 P1 — 도구 검색 (별도 실패해도 agent 응답은 유지)
    relevant_tools: list[dict] = []
    if payload.top_k_tools > 0:
        try:
            from api.services import tool_embedding_svc
            relevant_tools = await tool_embedding_svc.search_tools(
                session, payload.q, top_k=payload.top_k_tools
            )
        except Exception as e:
            log.warning("relevant_tools 검색 실패 (agent 응답은 유지): %s", e)

    return {
        "query": payload.q,
        "candidate_sections": payload.candidate_sections,
        "agents": ranked,
        "relevant_tools": relevant_tools,
    }
