# list_agents MCP 도구의 compact·domain·tag 옵션(additive) 검증
from __future__ import annotations

import pytest

from api import mcp_runtime
from api.db.models import Agent


async def _seed(maker) -> None:
    async with maker() as s:
        s.add_all(
            [
                Agent(
                    agent_type="cam-ois-control",
                    name="OIS",
                    description="D1",
                    common_tags=["servo", "손떨림"],
                    data_types=["DOC"],
                ),
                Agent(
                    agent_type="cam-vcm-actuator",
                    name="VCM",
                    description="D2",
                    common_tags=["actuator", "vcm"],
                    data_types=["DOC"],
                ),
                Agent(
                    agent_type="pwr-dcdc-regulator",
                    name="DCDC",
                    description="D3",
                    common_tags=["buck"],
                    data_types=["DOC"],
                ),
            ]
        )
        await s.commit()


@pytest.mark.asyncio
async def test_list_agents_default_is_backward_compatible(monkeypatch, test_session_maker):
    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    await _seed(test_session_maker)
    rows = await mcp_runtime.list_agents()
    assert len(rows) == 3
    # 인자 없는 기존 호출은 전체 메타(description·data_types 포함) 그대로.
    assert "description" in rows[0] and "data_types" in rows[0]


@pytest.mark.asyncio
async def test_list_agents_compact_omits_description(monkeypatch, test_session_maker):
    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    await _seed(test_session_maker)
    rows = await mcp_runtime.list_agents(compact=True)
    assert len(rows) == 3
    assert all(set(a.keys()) == {"agent_type", "name", "common_tags"} for a in rows)


@pytest.mark.asyncio
async def test_list_agents_domain_filter(monkeypatch, test_session_maker):
    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    await _seed(test_session_maker)
    cam = await mcp_runtime.list_agents(domain="cam")
    assert {a["agent_type"] for a in cam} == {"cam-ois-control", "cam-vcm-actuator"}


@pytest.mark.asyncio
async def test_list_agents_tag_filter(monkeypatch, test_session_maker):
    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    await _seed(test_session_maker)
    act = await mcp_runtime.list_agents(tag="actuator")
    assert [a["agent_type"] for a in act] == ["cam-vcm-actuator"]
