"""Wave-7 P3 — GET /api/agents/{agent_type}/tools 라우트 단위 테스트.

이 라우트는 매니페스트 정책 (restrict/require/exclude) 을 평가하여
이 agent context 에서 호출 가능한 wave-5 도구만 반환.
"""
from __future__ import annotations

import pytest

try:
    import aiosqlite  # noqa: F401
    aiosqlite_available = True
except ImportError:  # pragma: no cover
    aiosqlite_available = False


@pytest.mark.asyncio
@pytest.mark.skipif(not aiosqlite_available, reason="aiosqlite 미설치")
async def test_get_agent_tools_filters_by_policy(test_session_maker, db_client) -> None:
    """3 도구 등록 (open / require / restrict) → cae_engineer agent 가 2개만 봄."""
    from api.db.models import Agent, MCPUpload

    async with test_session_maker() as s:
        s.add(Agent(
            agent_type="cae_engineer",
            name="CAE Engineer",
            common_tags=["structural", "metal"],
        ))
        s.add(MCPUpload(
            name="open_tool", current_sha="a" * 64,
            manifest={"name": "open_tool", "title": "Open", "description": "no policy"},
        ))
        s.add(MCPUpload(
            name="metal_tool", current_sha="b" * 64,
            manifest={
                "name": "metal_tool", "title": "Metal",
                "description": "metal-only",
                "require_agent_tag": ["metal"],
            },
        ))
        s.add(MCPUpload(
            name="qa_only", current_sha="c" * 64,
            manifest={
                "name": "qa_only", "title": "QA",
                "description": "qa engineer only",
                "restrict_agents": ["qa_engineer"],
            },
        ))
        await s.commit()

    resp = await db_client.get("/api/agents/cae_engineer/tools")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = sorted(t["name"] for t in body["tools"])

    assert body["agent_type"] == "cae_engineer"
    assert body["agent_common_tags"] == ["structural", "metal"]
    assert "open_tool" in names         # no policy
    assert "metal_tool" in names         # require=[metal] ⊆ common_tags
    assert "qa_only" not in names        # restrict=[qa_engineer]
    assert body["tool_count"] == len(names)


@pytest.mark.asyncio
@pytest.mark.skipif(not aiosqlite_available, reason="aiosqlite 미설치")
async def test_get_agent_tools_404_for_unknown_agent(db_client) -> None:
    resp = await db_client.get("/api/agents/nonexistent_agent/tools")
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.skipif(not aiosqlite_available, reason="aiosqlite 미설치")
async def test_get_agent_tools_excludes_deprecated(test_session_maker, db_client) -> None:
    """deprecated_at IS NOT NULL 도구는 제외."""
    from datetime import datetime, timezone

    from api.db.models import Agent, MCPUpload

    async with test_session_maker() as s:
        s.add(Agent(agent_type="a1", name="A1", common_tags=[]))
        s.add(MCPUpload(
            name="active_tool", current_sha="a" * 64,
            manifest={"name": "active_tool", "description": "active"},
        ))
        s.add(MCPUpload(
            name="retired", current_sha="r" * 64,
            manifest={"name": "retired", "description": "old"},
            deprecated_at=datetime.now(timezone.utc),
        ))
        await s.commit()

    resp = await db_client.get("/api/agents/a1/tools")
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()["tools"]]
    assert "active_tool" in names
    assert "retired" not in names
