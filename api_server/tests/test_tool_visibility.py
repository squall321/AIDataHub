"""Wave-7 P2 — tool_visibility_svc 단위 테스트.

매니페스트 정책 3 종 (restrict_agents / require_agent_tag / exclude_agent_tag)
이 agent context 와 함께 올바르게 평가되는지 검증.
"""
from __future__ import annotations

import pytest

try:
    import aiosqlite  # noqa: F401
    aiosqlite_available = True
except ImportError:  # pragma: no cover
    aiosqlite_available = False


# ---------------------------------------------------------------------------
# 1. extract_policy
# ---------------------------------------------------------------------------
def test_extract_policy_all_keys() -> None:
    from api.services.tool_visibility_svc import extract_policy

    p = extract_policy({
        "restrict_agents": ["cae_engineer", "materials_engineer"],
        "require_agent_tag": ["structural", "metal"],
        "exclude_agent_tag": ["legacy"],
    })
    assert p["restrict_agents"] == ["cae_engineer", "materials_engineer"]
    assert p["require_agent_tag"] == ["structural", "metal"]
    assert p["exclude_agent_tag"] == ["legacy"]


def test_extract_policy_empty_or_none() -> None:
    from api.services.tool_visibility_svc import extract_policy

    assert extract_policy(None) == {
        "restrict_agents": [], "require_agent_tag": [], "exclude_agent_tag": [],
    }
    assert extract_policy({})["restrict_agents"] == []


def test_extract_policy_filters_non_strings() -> None:
    from api.services.tool_visibility_svc import extract_policy

    # 비 string / 빈 string 은 무시
    p = extract_policy({
        "restrict_agents": ["ok", 1, None, ""],
        "require_agent_tag": ["", "a", 42],
    })
    assert p["restrict_agents"] == ["ok"]
    assert p["require_agent_tag"] == ["a"]


# ---------------------------------------------------------------------------
# 2. is_compatible — 핵심 정책 평가
# ---------------------------------------------------------------------------
def test_is_compatible_no_policy_passes() -> None:
    """정책 모두 빈 → 항상 노출 (기본 동작 유지)."""
    from api.services.tool_visibility_svc import is_compatible

    assert is_compatible({}, agent_type=None, agent_tags=None)
    assert is_compatible({}, agent_type="any_agent", agent_tags=["x"])
    assert is_compatible(None, agent_type="any_agent", agent_tags=["x"])


def test_is_compatible_restrict_whitelist() -> None:
    """restrict_agents — agent_type 매치만 통과."""
    from api.services.tool_visibility_svc import is_compatible

    m = {"restrict_agents": ["cae_engineer", "materials_engineer"]}
    assert is_compatible(m, agent_type="cae_engineer", agent_tags=[])
    assert is_compatible(m, agent_type="materials_engineer", agent_tags=[])
    assert not is_compatible(m, agent_type="random_agent", agent_tags=[])


def test_is_compatible_restrict_none_agent_passes() -> None:
    """agent_type 가 None 이면 restrict 평가 skip → 노출."""
    from api.services.tool_visibility_svc import is_compatible

    m = {"restrict_agents": ["cae_engineer"]}
    assert is_compatible(m, agent_type=None, agent_tags=None)


def test_is_compatible_require_all_tags() -> None:
    """require_agent_tag — agent.common_tags 가 모든 태그 포함 시만 통과 (AND)."""
    from api.services.tool_visibility_svc import is_compatible

    m = {"require_agent_tag": ["structural", "metal"]}
    assert is_compatible(m, agent_type="x", agent_tags=["structural", "metal", "iso"])
    assert not is_compatible(m, agent_type="x", agent_tags=["structural"])  # metal 부재
    assert not is_compatible(m, agent_type="x", agent_tags=[])


def test_is_compatible_exclude_any_tag() -> None:
    """exclude_agent_tag — agent.common_tags 가 어떤 태그라도 포함하면 숨김."""
    from api.services.tool_visibility_svc import is_compatible

    m = {"exclude_agent_tag": ["legacy", "deprecated"]}
    assert is_compatible(m, agent_type="x", agent_tags=["modern", "v2"])
    assert not is_compatible(m, agent_type="x", agent_tags=["legacy"])
    assert not is_compatible(m, agent_type="x", agent_tags=["modern", "deprecated"])


def test_is_compatible_all_three_policies_and() -> None:
    """3 정책 동시 — AND 평가 (모두 통과해야 노출)."""
    from api.services.tool_visibility_svc import is_compatible

    m = {
        "restrict_agents": ["cae_engineer"],
        "require_agent_tag": ["structural"],
        "exclude_agent_tag": ["legacy"],
    }
    # 셋 다 통과
    assert is_compatible(
        m, agent_type="cae_engineer", agent_tags=["structural", "metal"]
    )
    # restrict fail
    assert not is_compatible(
        m, agent_type="random_agent", agent_tags=["structural"]
    )
    # require fail
    assert not is_compatible(
        m, agent_type="cae_engineer", agent_tags=["metal"]
    )
    # exclude fail
    assert not is_compatible(
        m, agent_type="cae_engineer", agent_tags=["structural", "legacy"]
    )


# ---------------------------------------------------------------------------
# 3. filter_tools_for_agent — 통합 필터
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.skipif(not aiosqlite_available, reason="aiosqlite 미설치")
async def test_filter_tools_for_agent_e2e(test_session_maker) -> None:
    """DB 의 agent.common_tags 와 함께 도구 가시성 평가."""
    from api.db.models import Agent
    from api.services.tool_visibility_svc import filter_tools_for_agent

    async with test_session_maker() as s:
        s.add(Agent(
            agent_type="cae_engineer",
            name="CAE Engineer",
            common_tags=["structural", "metal"],
        ))
        await s.commit()

    tools = [
        # 통과 — 정책 없음
        {"name": "free_tool", "manifest": {}},
        # 통과 — agent.common_tags 가 structural 포함
        {"name": "structural_tool", "manifest": {"require_agent_tag": ["structural"]}},
        # 차단 — require ceramic 부재
        {"name": "ceramic_tool", "manifest": {"require_agent_tag": ["ceramic"]}},
        # 차단 — restrict 에 cae_engineer 미포함
        {"name": "qa_only", "manifest": {"restrict_agents": ["qa_engineer"]}},
        # 차단 — exclude metal
        {"name": "no_metal", "manifest": {"exclude_agent_tag": ["metal"]}},
    ]

    async with test_session_maker() as s:
        out = await filter_tools_for_agent(s, tools, agent_type="cae_engineer")
        names = [t["name"] for t in out]

    assert "free_tool" in names
    assert "structural_tool" in names
    assert "ceramic_tool" not in names
    assert "qa_only" not in names
    assert "no_metal" not in names


@pytest.mark.asyncio
@pytest.mark.skipif(not aiosqlite_available, reason="aiosqlite 미설치")
async def test_filter_tools_for_agent_none_passes_all(test_session_maker) -> None:
    """agent_type=None 이면 restrict 만 평가 (require/exclude skip), 모든 도구 통과."""
    from api.services.tool_visibility_svc import filter_tools_for_agent

    tools = [
        {"name": "a", "manifest": {"restrict_agents": ["x"]}},
        {"name": "b", "manifest": {"require_agent_tag": ["x"]}},
        {"name": "c", "manifest": {"exclude_agent_tag": ["x"]}},
    ]

    async with test_session_maker() as s:
        out = await filter_tools_for_agent(s, tools, agent_type=None)

    # restrict_agents 는 agent_type=None 시 skip → 모두 통과
    assert len(out) == 3


# ---------------------------------------------------------------------------
# 4. validate_manifest 가 새 정책 키 round-trip OK
# ---------------------------------------------------------------------------
def test_validate_manifest_preserves_new_policy_keys() -> None:
    """validate_manifest + _manifest_to_dict + _from_dict round-trip."""
    from api.services.mcp_upload_svc import (
        _manifest_from_dict, _manifest_to_dict, validate_manifest,
    )

    raw = {
        "name": "policy_tool",
        "description": "test",
        "script": "tool.py",
        "runtime": "python",
        "restrict_agents": ["cae_engineer"],
        "require_agent_tag": ["structural", "metal"],
        "exclude_agent_tag": ["legacy"],
    }
    m = validate_manifest(raw)
    assert m.restrict_agents == ["cae_engineer"]
    assert m.require_agent_tag == ["structural", "metal"]
    assert m.exclude_agent_tag == ["legacy"]

    d = _manifest_to_dict(m)
    assert d["restrict_agents"] == ["cae_engineer"]
    assert d["require_agent_tag"] == ["structural", "metal"]
    assert d["exclude_agent_tag"] == ["legacy"]

    m2 = _manifest_from_dict(d)
    assert m2.restrict_agents == m.restrict_agents
    assert m2.require_agent_tag == m.require_agent_tag
    assert m2.exclude_agent_tag == m.exclude_agent_tag
