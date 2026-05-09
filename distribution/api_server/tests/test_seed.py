"""``api.seed`` 패키지 테스트 — 표준 에이전트 시드 멱등성 검증.

- SQLite 인메모리 (conftest fixture) 에서 ``seed_agents`` 직접 호출.
- 1회차: 5건 inserted.
- 2회차: 5건 unchanged (동일 페이로드) → 멱등성.
- ``--dry-run`` 모드는 DB 변경 없이 카운터만 반환.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_seed_agents_inserts_all_five(test_session_maker):
    from api.db.models import Agent
    from api.seed import STANDARD_AGENTS
    from api.seed.cli import seed_agents

    async with test_session_maker() as session:
        counters = await seed_agents(session)
        assert counters["inserted"] == len(STANDARD_AGENTS) == 5
        assert counters["updated"] == 0
        assert counters["unchanged"] == 0

    # DB 상태 확인 (다른 세션으로 reload)
    async with test_session_maker() as session:
        rows = (await session.execute(select(Agent))).scalars().all()
        agent_types = {r.agent_type for r in rows}
        expected = {a["agent_type"] for a in STANDARD_AGENTS}
        assert agent_types == expected
        # 필드 한 건 샘플 검증
        iga = next(r for r in rows if r.agent_type == "iga-analyst")
        assert iga.name == "IGA 해석 분석가"
        assert "IGA" in iga.common_tags
        assert "DOC" in iga.data_types


@pytest.mark.asyncio
async def test_seed_agents_idempotent(test_session_maker):
    from api.seed import STANDARD_AGENTS
    from api.seed.cli import seed_agents

    async with test_session_maker() as session:
        first = await seed_agents(session)
        assert first["inserted"] == 5

    # 두 번째 실행 — 모두 unchanged
    async with test_session_maker() as session:
        second = await seed_agents(session)
        assert second["inserted"] == 0
        assert second["updated"] == 0
        assert second["unchanged"] == len(STANDARD_AGENTS) == 5


@pytest.mark.asyncio
async def test_seed_agents_dry_run_does_not_write(test_session_maker):
    from api.db.models import Agent
    from api.seed.cli import seed_agents

    async with test_session_maker() as session:
        counters = await seed_agents(session, dry_run=True)
        assert counters["inserted"] == 5

    # 다른 세션으로 검사 — 실제 데이터는 없어야 함.
    async with test_session_maker() as session:
        rows = (await session.execute(select(Agent))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_seed_agents_updates_changed_fields(test_session_maker):
    """기존 행이 다르면 updated 카운트가 올라가고 필드가 갱신된다."""
    from api.db.models import Agent
    from api.seed.cli import seed_agents

    # 사전 등록 — 동일 PK 이지만 다른 메타데이터.
    async with test_session_maker() as session:
        session.add(
            Agent(
                agent_type="iga-analyst",
                name="OLD NAME",
                description="OLD DESC",
                common_tags=[],
                data_types=[],
            )
        )
        await session.commit()

    async with test_session_maker() as session:
        counters = await seed_agents(session)
        # iga-analyst 1건 update + 나머지 4건 insert
        assert counters["inserted"] == 4
        assert counters["updated"] == 1
        assert counters["unchanged"] == 0

    async with test_session_maker() as session:
        agent = (
            await session.execute(
                select(Agent).where(Agent.agent_type == "iga-analyst")
            )
        ).scalar_one()
        assert agent.name == "IGA 해석 분석가"
        assert "IGA" in agent.common_tags
