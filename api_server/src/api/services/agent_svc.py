"""에이전트 관리 로직 (CRUD + 매핑 조회 + 변경 이력 + sample 임베딩 동기화)."""
from __future__ import annotations

import logging

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload

from api.db.models import Agent, AgentHistory, Record
from api.services.sql_compat import array_overlap

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
        await _sync_samples_safely(session, agent=agent, samples=list(agent.sample_queries))
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
        await _sync_samples_safely(session, agent=agent, samples=new_samples)
    return agent


async def _sync_samples_safely(
    session: AsyncSession,
    *,
    agent: Agent,
    samples: list[str],
) -> None:
    """sample_embedding_svc.sync_agent_samples 호출. 임베딩 실패가 agent
    save 흐름을 깨지 않게 try/except. 1회 자동 retry — 첫 시도가 transient
    실패(rate limit / 일시 네트워크) 일 경우 자동 복구. 두 번 다 실패 시 UI 가
    'Resync samples' 버튼으로 수동 retry 가능.

    실패할 때마다 세션을 롤백하고 agent 를 다시 읽는다. 표본 행 INSERT 가 flush 에서
    실패하면 세션이 '롤백 대기' 로 남고 붙어 있던 행이 모두 만료된다 — 이미 커밋한 agent
    까지. 그대로 두면 다시 시도도 PendingRollbackError 로 끝나고, 돌려받은 agent 를 읽는
    응답(AgentOut.model_validate)이 칸마다 ValidationError 를 내 저장은 끝났는데 500 이
    났다(10-28 cae00 운영자 동기화 실측, 10-30 요청 AD-21). 롤백이 되돌리는 것은 표본 행
    교체뿐이다 — agent 행과 이력은 이미 커밋돼 있다.
    """
    import asyncio

    from . import sample_embedding_svc

    agent_type = agent.agent_type
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
            await session.rollback()
            await session.refresh(agent)
            if attempt == 1:
                await asyncio.sleep(0.5)
            continue
    log.warning(
        "sample embedding sync failed twice for agent=%s (%s) — UI Resync button can retry",
        agent_type,
        last_exc,
    )


async def fetch_sample_index_state(
    session: AsyncSession,
    agents: list[Agent],
) -> dict[str, tuple[int, bool]]:
    """agent_type → (색인된 표본 행 수, samples_stale). UI 의 'stale' 배지 · 저장 응답용.

    낡음은 수가 아니라 글로 판정한다. 표본 글만 바꾸고 수는 그대로인 저장의 재색인이 실패하면
    롤백이 옛 행을 남겨, 수로는 맞아 보이는데 옛 글이 색인돼 있었다(10-30 AD-21 검토). 수가
    다르면 글도 다르므로 예전 판정이 잡던 것은 그대로 잡는다.
    """
    from api.db.models import AgentSampleEmbedding

    if not agents:
        return {}
    stmt = select(AgentSampleEmbedding.agent_type, AgentSampleEmbedding.sample_text).where(
        AgentSampleEmbedding.agent_type.in_([a.agent_type for a in agents])
    )
    indexed: dict[str, list[str]] = {}
    for agent_type, sample in (await session.execute(stmt)).all():
        indexed.setdefault(agent_type, []).append(sample)
    out: dict[str, tuple[int, bool]] = {}
    for a in agents:
        texts = indexed.get(a.agent_type, [])
        out[a.agent_type] = (len(texts), sorted(texts) != sorted(a.sample_queries or []))
    return out


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
    # ⚠ records.agents 는 FK 가 없는 맨 text[] 다(GIN 인덱스만 달려 있다). agent_records·
    # agent_sample_embeddings 는 ON DELETE CASCADE 로 따라 지워지지만 이 배열은 문자열이
    # 그대로 남아 **고아 키**가 된다. 그 키는 recommend_agents 의 점수 집계를 그냥 통과해
    # 이름이 빈 좌석으로 추천되고(실측 2026-09-09: 기본 top_k=5 중 2석), 그걸 집어
    # agent_search 를 부르면 "agent not found" 로 즉사한다. 일회성 잔재가 아니라
    # 삭제할 때마다 재현되므로 삭제 경로가 같은 트랜잭션에서 직접 치운다.
    await session.execute(
        text("UPDATE records SET agents = array_remove(agents, :at) WHERE agents @> ARRAY[:at]"),
        {"at": agent_type},
    )
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
    # 값을 먼저 떼어 둔다. 한 agent 의 표본 행이 flush 에서 거절되면 세션을 롤백해야 다음
    # agent 를 할 수 있는데, 롤백은 읽어 둔 행을 모두 만료시킨다. 롤백 없이 만료된 행을 읽던
    # 예전 코드는 첫 실패에서 PendingRollbackError 로 500 을 내, errors 도 남은 agent 도
    # 잃었다(10-30 AD-21 검토).
    targets = [(ag.agent_type, list(ag.sample_queries or [])) for ag in rows]
    successes: list[dict] = []
    errors: list[dict] = []
    for agent_type, samples in targets:
        try:
            summary = await sample_embedding_svc.sync_agent_samples(
                session,
                agent_type=agent_type,
                sample_queries=samples,
            )
            successes.append(summary)
        except Exception as exc:
            await session.rollback()
            log.warning("resync_all failed for agent=%s: %s", agent_type, exc)
            errors.append({"agent_type": agent_type, "error": str(exc)})
    return {
        "agents_total": len(targets),
        "successes": successes,
        "errors": errors,
    }


async def records_for_agent(session: AsyncSession, agent_type: str) -> list[Record]:
    """좌석에 묶인 레코드(지운 것은 빼고 최근 적재순).

    묶음의 정본은 ``records.agents`` 다(AD-20). 추천의 기록 수 · agent_search 범위 ·
    discover 의 기록 수 · ``list_records``(포털 전문가 보기)가 모두 이 배열을 보고, 적재 ·
    자동 바인딩(bind-matching) · 레코드 PATCH · 전문가 삭제가 모두 이 배열을 고친다.
    예전에는 ``agent_records`` 로 목록을 냈는데, 그 표는 자동 바인딩 · PATCH 가 채우지 않고
    박스 병합이 행을 지우지 않아 추천이 센 기록과 이 목록이 어긋났다. 지운 레코드는
    추천 · discover 가 세지 않으므로 여기서도 뺀다.

    절 · 첨부는 읽지 않는다(raiseload). 부르는 곳(목록 응답 RecordOut · RAG 미리보기의 ID)이
    쓰지 않는데, 기본 selectin 이면 카드마다 절과 그 벡터까지 따라온다. 정본을 바꾸면서 자동
    바인딩 · PATCH 로 묶인 카드도 이 목록에 들어와 좌석에 따라 수천 장이 된다.
    """
    pred = array_overlap(Record.agents, [agent_type], session)
    stmt = (
        select(Record)
        .where(pred.where_clause, Record.deleted_at.is_(None))
        .order_by(Record.created_at.desc())
        .options(raiseload(Record.sections), raiseload(Record.attachments))
    )
    return pred.apply_python((await session.execute(stmt)).scalars().all())
