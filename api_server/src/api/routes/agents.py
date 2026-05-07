"""``/api/agents`` — 에이전트 메타데이터 CRUD."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import get_session
from api.services import agent_svc

from ._schemas import AgentIn, AgentOut, AgentPatch, RecordOut

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get(
    "",
    response_model=list[AgentOut],
    response_model_exclude_none=True,
)
@router.get(
    "/",
    response_model=list[AgentOut],
    response_model_exclude_none=True,
    include_in_schema=False,
)
async def list_agents(
    session: AsyncSession = Depends(get_session),
) -> list[AgentOut]:
    rows = await agent_svc.list_agents(session)
    return [AgentOut.model_validate(r) for r in rows]


@router.get(
    "/{agent_type}", response_model=AgentOut, response_model_exclude_none=True
)
async def get_agent(
    agent_type: str,
    session: AsyncSession = Depends(get_session),
) -> AgentOut:
    agent = await agent_svc.get_agent(session, agent_type)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_type}")
    return AgentOut.model_validate(agent)


@router.get(
    "/{agent_type}/records",
    response_model=list[RecordOut],
    response_model_exclude_none=True,
)
async def get_agent_records(
    agent_type: str,
    session: AsyncSession = Depends(get_session),
) -> list[RecordOut]:
    agent = await agent_svc.get_agent(session, agent_type)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_type}")
    rows = await agent_svc.records_for_agent(session, agent_type)
    return [RecordOut.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=AgentOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/",
    response_model=AgentOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def create_agent(
    payload: AgentIn,
    session: AsyncSession = Depends(get_session),
) -> AgentOut:
    try:
        agent = await agent_svc.create_agent(session, payload.model_dump())
    except ValueError as exc:
        log.info("create_agent conflict: %s", payload.agent_type)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AgentOut.model_validate(agent)


@router.patch(
    "/{agent_type}", response_model=AgentOut, response_model_exclude_none=True
)
async def patch_agent(
    agent_type: str,
    patch: AgentPatch,
    session: AsyncSession = Depends(get_session),
) -> AgentOut:
    agent = await agent_svc.update_agent(
        session, agent_type, patch.model_dump(exclude_unset=True)
    )
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_type}")
    return AgentOut.model_validate(agent)


@router.delete("/{agent_type}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_type: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    ok = await agent_svc.delete_agent(session, agent_type)
    if not ok:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_type}")


__all__ = ["router"]
