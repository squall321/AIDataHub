"""에이전트 관리 로직 (CRUD + 매핑 조회 + 변경 이력 + sample 임베딩 동기화)."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Agent, AgentHistory, AgentRecord, Record

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _snapshot(agent: Agent) -> dict:
    """Agent 행 → JSONB 스냅샷 (agents_history.snapshot)."""
    return {
        "agent_type": agent.agent_type,
        "name": agent.name,
        "description": agent.description,
        "common_tags": list(agent.common_tags or []),
        "data_types": list(agent.data_types or []),
        "required_doc_type": agent.required_doc_type,
        "required_tags": list(agent.required_tags or []),
        "excluded_tags": list(agent.excluded_tags or []),
        "retrieval_config": dict(agent.retrieval_config or {}),
        "system_prompt": agent.system_prompt,
        "response_config": dict(agent.response_config or {}),
        "sample_queries": list(agent.sample_queries or []),
    }


def _log_history(
    session: AsyncSession,
    *,
    agent: Agent,
    operation: str,
    changed_by: str | None = None,
) -> None:
    """append-only 이력 1행 추가 (commit 은 호출자가)."""
    session.add(
        AgentHistory(
            agent_type=agent.agent_type,
            operation=operation,
            snapshot=_snapshot(agent),
            changed_by=changed_by,
        )
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
async def list_agents(session: AsyncSession) -> list[Agent]:
    rows = (await session.execute(select(Agent).order_by(Agent.agent_type))).scalars().all()
    return list(rows)


async def get_agent(session: AsyncSession, agent_type: str) -> Agent | None:
    return (
        await session.execute(select(Agent).where(Agent.agent_type == agent_type))
    ).scalar_one_or_none()


async def create_agent(
    session: AsyncSession,
    payload: dict,
    *,
    changed_by: str | None = None,
) -> Agent:
    agent = Agent(
        agent_type=payload["agent_type"],
        name=payload.get("name", payload["agent_type"]),
        description=payload.get("description", ""),
        common_tags=list(payload.get("common_tags", []) or []),
        data_types=list(payload.get("data_types", []) or []),
        required_doc_type=payload.get("required_doc_type"),
        required_tags=list(payload.get("required_tags", []) or []),
        excluded_tags=list(payload.get("excluded_tags", []) or []),
        retrieval_config=dict(payload.get("retrieval_config") or {}),
        system_prompt=payload.get("system_prompt"),
        response_config=dict(payload.get("response_config") or {}),
        sample_queries=list(payload.get("sample_queries", []) or []),
    )
    session.add(agent)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ValueError(f"agent already exists: {agent.agent_type}") from exc
    _log_history(session, agent=agent, operation="create", changed_by=changed_by)
    await session.commit()
    await session.refresh(agent)
    # v0.13.0 — sample_queries 가 있으면 임베딩 동기화 (best-effort).
    if agent.sample_queries:
        await _sync_samples_safely(session, agent_type=agent.agent_type, samples=list(agent.sample_queries))
    return agent


async def update_agent(
    session: AsyncSession,
    agent_type: str,
    patch: dict,
    *,
    changed_by: str | None = None,
) -> Agent | None:
    agent = await get_agent(session, agent_type)
    if agent is None:
        return None
    # sample_queries 변경 여부를 미리 파악 (변경 없으면 임베딩 재계산 skip).
    prev_samples = list(agent.sample_queries or [])
    for key in (
        "name",
        "description",
        "common_tags",
        "data_types",
        "required_doc_type",
        "required_tags",
        "excluded_tags",
        "retrieval_config",
        "system_prompt",
        "response_config",
        "sample_queries",
    ):
        if key in patch and patch[key] is not None:
            setattr(agent, key, patch[key])
    new_samples = list(agent.sample_queries or [])
    samples_changed = prev_samples != new_samples
    await session.flush()
    _log_history(session, agent=agent, operation="update", changed_by=changed_by)
    await session.commit()
    await session.refresh(agent)
    if samples_changed:
        await _sync_samples_safely(session, agent_type=agent.agent_type, samples=new_samples)
    return agent


async def _sync_samples_safely(
    session: AsyncSession,
    *,
    agent_type: str,
    samples: list[str],
) -> None:
    """sample_embedding_svc.sync_agent_samples 호출. 임베딩 실패가 agent
    save 흐름을 깨지 않게 try/except. 1회 자동 retry — 첫 시도가 transient
    실패(rate limit / 일시 네트워크) 일 경우 자동 복구. 두 번 다 실패 시 UI 가
    'Resync samples' 버튼으로 수동 retry 가능.
    """
    import asyncio

    from . import sample_embedding_svc

    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            await sample_embedding_svc.sync_agent_samples(
                session, agent_type=agent_type, sample_queries=samples
            )
            if attempt > 1:
                log.info(
                    "sample embedding sync recovered for agent=%s on attempt %d",
                    agent_type,
                    attempt,
                )
            return
        except Exception as exc:
            last_exc = exc
            if attempt == 1:
                await asyncio.sleep(0.5)
            continue
    log.warning(
        "sample embedding sync failed twice for agent=%s (%s) — UI Resync button can retry",
        agent_type,
        last_exc,
    )


async def fetch_samples_indexed_counts(
    session: AsyncSession,
    agent_types: list[str] | None = None,
) -> dict[str, int]:
    """agent_type → indexed sample 행 수 매핑. UI 의 'samples_stale' 판정용.

    agent_types 가 주어지면 그 agent 들로 한정 (성능). None 이면 전체.
    """
    from sqlalchemy import func as _func

    from api.db.models import AgentSampleEmbedding

    stmt = select(AgentSampleEmbedding.agent_type, _func.count(AgentSampleEmbedding.id))
    if agent_types is not None:
        stmt = stmt.where(AgentSampleEmbedding.agent_type.in_(agent_types))
    stmt = stmt.group_by(AgentSampleEmbedding.agent_type)
    rows = (await session.execute(stmt)).all()
    return {r[0]: int(r[1] or 0) for r in rows}


async def delete_agent(
    session: AsyncSession,
    agent_type: str,
    *,
    changed_by: str | None = None,
) -> bool:
    agent = await get_agent(session, agent_type)
    if agent is None:
        return False
    # delete 전 스냅샷 — 삭제된 agent 도 이력으로 조회 가능해야 함.
    _log_history(session, agent=agent, operation="delete", changed_by=changed_by)
    await session.delete(agent)
    await session.commit()
    return True


async def list_agent_history(
    session: AsyncSession,
    agent_type: str,
    *,
    limit: int = 50,
) -> list[AgentHistory]:
    """agent_type 의 변경 이력 (최신순). agent 가 삭제되어도 이력은 남는다."""
    stmt = (
        select(AgentHistory)
        .where(AgentHistory.agent_type == agent_type)
        .order_by(AgentHistory.changed_at.desc(), AgentHistory.id.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def prune_agent_history(
    session: AsyncSession,
    *,
    keep_last: int = 50,
    older_than_days: int | None = None,
) -> dict:
    """``agents_history`` 청소 — 운영 누적 방지.

    - ``keep_last`` (default 50): 각 agent_type 별 최신 N행 유지, 나머지 삭제.
    - ``older_than_days``: 지정 시 N일 이전 행 모두 삭제 (keep_last 와 OR).

    반환: ``{deleted: N, agent_types_touched: int}``.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import delete as _sa_delete
    from sqlalchemy import func as _sa_func

    deleted_total = 0
    touched = 0

    # 1) per-agent keep_last — row_number window 로 N+1 번째 이후 삭제.
    if keep_last is not None and keep_last >= 0:
        # PG 와 SQLite 모두에서 동작하는 단순 접근: agent_type 마다 N+1번째 id 알아내고 그 이전 id 삭제.
        agent_types = (
            await session.execute(
                select(AgentHistory.agent_type).distinct()
            )
        ).scalars().all()
        for at in agent_types:
            stmt = (
                select(AgentHistory.id)
                .where(AgentHistory.agent_type == at)
                .order_by(AgentHistory.changed_at.desc(), AgentHistory.id.desc())
                .offset(keep_last)
            )
            old_ids = (await session.execute(stmt)).scalars().all()
            if not old_ids:
                continue
            res = await session.execute(
                _sa_delete(AgentHistory).where(AgentHistory.id.in_(list(old_ids)))
            )
            deleted_total += int(res.rowcount or 0)
            touched += 1

    # 2) absolute age threshold.
    if older_than_days is not None and older_than_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        res = await session.execute(
            _sa_delete(AgentHistory).where(AgentHistory.changed_at < cutoff)
        )
        deleted_total += int(res.rowcount or 0)

    await session.commit()
    return {
        "deleted": deleted_total,
        "agent_types_touched": touched,
        "keep_last": keep_last,
        "older_than_days": older_than_days,
    }


async def resync_all_agent_samples(session: AsyncSession) -> dict:
    """모든 agent 에 대해 sample_queries 임베딩 재계산. EMBEDDING_DIM 변경 후
    백필 등에 사용. 실패한 agent 는 errors 에 누적."""
    from . import sample_embedding_svc

    rows = (
        await session.execute(select(Agent).order_by(Agent.agent_type))
    ).scalars().all()
    successes: list[dict] = []
    errors: list[dict] = []
    for ag in rows:
        try:
            summary = await sample_embedding_svc.sync_agent_samples(
                session,
                agent_type=ag.agent_type,
                sample_queries=list(ag.sample_queries or []),
            )
            successes.append(summary)
        except Exception as exc:  # pragma: no cover — env-specific
            log.warning("resync_all failed for agent=%s: %s", ag.agent_type, exc)
            errors.append({"agent_type": ag.agent_type, "error": str(exc)})
    return {
        "agents_total": len(rows),
        "successes": successes,
        "errors": errors,
    }


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
