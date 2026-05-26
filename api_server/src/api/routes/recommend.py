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
    agent_type: str | None = Field(
        None,
        description=(
            "Wave-7 P2: 호출자 agent context. 설정 시 매니페스트 정책 "
            "(restrict/require/exclude) 적용 후 relevant_tools 필터."
        ),
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
            from api.services import tool_embedding_svc, tool_visibility_svc
            # 정책 평가를 위해 over-fetch (top_k_tools * 3) — 필터 후 top_k_tools 슬라이스.
            over_fetch = min(payload.top_k_tools * 3, 20)
            raw = await tool_embedding_svc.search_tools(
                session, payload.q, top_k=over_fetch
            )
            # Wave-7 P2 — agent context 매니페스트 정책 필터
            if payload.agent_type:
                # search_tools 응답은 manifest 없음 → DB 에서 일괄 조회 후 필터
                from api.db.models import MCPUpload
                from sqlalchemy import select
                names = [r["name"] for r in raw]
                if names:
                    rows = (
                        await session.execute(
                            select(MCPUpload).where(MCPUpload.name.in_(names))
                        )
                    ).scalars().all()
                    manifest_by_name = {r.name: r.manifest for r in rows}
                    enriched = [
                        {**r, "manifest": manifest_by_name.get(r["name"], {})}
                        for r in raw
                    ]
                    filtered = await tool_visibility_svc.filter_tools_for_agent(
                        session, enriched, agent_type=payload.agent_type
                    )
                    # manifest 키는 응답에서 제거 (이중 전송 방지)
                    relevant_tools = [
                        {k: v for k, v in r.items() if k != "manifest"}
                        for r in filtered
                    ][:payload.top_k_tools]
                else:
                    relevant_tools = []
            else:
                relevant_tools = raw[:payload.top_k_tools]
        except Exception as e:
            log.warning("relevant_tools 검색 실패 (agent 응답은 유지): %s", e)

    return {
        "query": payload.q,
        "candidate_sections": payload.candidate_sections,
        "agents": ranked,
        "relevant_tools": relevant_tools,
    }
