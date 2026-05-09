"""에이전트 관리 로직 (CRUD + 매핑 조회)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Agent, AgentRecord, Record


async def list_agents(session: AsyncSession) -> list[Agent]:
    rows = (await session.execute(select(Agent).order_by(Agent.agent_type))).scalars().all()
    return list(rows)


async def get_agent(session: AsyncSession, agent_type: str) -> Agent | None:
    return (
        await session.execute(select(Agent).where(Agent.agent_type == agent_type))
    ).scalar_one_or_none()


async def create_agent(session: AsyncSession, payload: dict) -> Agent:
    agent = Agent(
        agent_type=payload["agent_type"],
        name=payload.get("name", payload["agent_type"]),
        description=payload.get("description", ""),
        common_tags=list(payload.get("common_tags", []) or []),
        data_types=list(payload.get("data_types", []) or []),
    )
    session.add(agent)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ValueError(f"agent already exists: {agent.agent_type}") from exc
    await session.commit()
    await session.refresh(agent)
    return agent


async def update_agent(
    session: AsyncSession, agent_type: str, patch: dict
) -> Agent | None:
    agent = await get_agent(session, agent_type)
    if agent is None:
        return None
    for key in ("name", "description", "common_tags", "data_types"):
        if key in patch and patch[key] is not None:
            setattr(agent, key, patch[key])
    await session.commit()
    await session.refresh(agent)
    return agent


async def delete_agent(session: AsyncSession, agent_type: str) -> bool:
    agent = await get_agent(session, agent_type)
    if agent is None:
        return False
    await session.delete(agent)
    await session.commit()
    return True


async def records_for_agent(session: AsyncSession, agent_type: str) -> list[Record]:
    """`agents.agent_type` 매핑된 레코드(우선순위 내림차순)."""
    stmt = (
        select(Record)
        .join(AgentRecord, AgentRecord.record_id == Record.id)
        .where(AgentRecord.agent_type == agent_type)
        .order_by(AgentRecord.priority.desc(), Record.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().unique().all()
    return list(rows)
