"""Wave-7 P2 — wave-5 도구의 agent-context 별 가시성 필터.

매니페스트 정책 (3 종, AND 결합):
    - ``restrict_agents``    : 명시된 agent_type 만 호출 가능. (whitelist)
    - ``require_agent_tag``  : agent.common_tags 가 모든 태그 포함 시만 노출. (AND)
    - ``exclude_agent_tag``  : agent.common_tags 가 어떤 태그라도 포함하면 숨김.

활용:
    - REST ``/api/recommend/agents`` — top_k_tools 결과를 agent context 로 필터
    - MCP ``list_tools`` — clientInfo.agent_type 기반 필터 (옵션)

설계 노트:
    - 매니페스트 정책이 모두 비어있으면 (default) — 무제한 노출 (기존 동작)
    - 정책 충돌 (예: restrict + require) 시 모두 AND 평가 (다 통과해야 노출)
    - agent_type 미지정 (None) 시 — ``restrict_agents`` 만 적용 (require/exclude 는 agent 정보 필요해서 skip)
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Agent


# ---------------------------------------------------------------------------
# 1) 매니페스트 추출
# ---------------------------------------------------------------------------
def extract_policy(manifest: dict[str, Any] | None) -> dict[str, list[str]]:
    """매니페스트 dict → 3 정책 키만 정규화한 dict (모두 list[str])."""
    m = manifest or {}
    return {
        "restrict_agents": [
            str(a) for a in (m.get("restrict_agents") or []) if isinstance(a, str) and a
        ],
        "require_agent_tag": [
            str(t) for t in (m.get("require_agent_tag") or []) if isinstance(t, str) and t
        ],
        "exclude_agent_tag": [
            str(t) for t in (m.get("exclude_agent_tag") or []) if isinstance(t, str) and t
        ],
    }


# ---------------------------------------------------------------------------
# 2) 한 도구 + 한 agent 의 호환성 평가
# ---------------------------------------------------------------------------
def is_compatible(
    manifest: dict[str, Any] | None,
    *,
    agent_type: str | None,
    agent_tags: list[str] | None,
) -> bool:
    """매니페스트 정책 vs agent context 호환성 (True = 노출 가능).

    - agent_type 가 None 이고 정책이 비어있으면 True (default 노출).
    - agent_type 가 None 인데 restrict_agents 비어있지 않으면 — agent context
      미식별로 정책 적용 불가 → True 로 통과 (LLM 이 도구 자체는 볼 수 있게).
      운영자가 강제 필터하려면 클라이언트가 agent_type 을 전달해야 함.
    - require/exclude 는 agent_tags 가 None 일 때 skip (정책 자체가 무의미).
    """
    policy = extract_policy(manifest)
    has_any_policy = any(policy.values())
    if not has_any_policy:
        return True

    # restrict_agents — whitelist (agent_type 명시되어 있을 때만 평가)
    restrict = policy["restrict_agents"]
    if restrict and agent_type is not None:
        if agent_type not in restrict:
            return False

    tags = set(agent_tags or [])
    # require_agent_tag — agent.common_tags 가 모든 태그 포함 (AND)
    require = policy["require_agent_tag"]
    if require and agent_tags is not None:
        if not all(t in tags for t in require):
            return False

    # exclude_agent_tag — agent.common_tags 가 어떤 태그라도 포함하면 숨김
    exclude = policy["exclude_agent_tag"]
    if exclude and agent_tags is not None:
        if any(t in tags for t in exclude):
            return False

    return True


# ---------------------------------------------------------------------------
# 3) tools list 필터
# ---------------------------------------------------------------------------
async def filter_tools_for_agent(
    session: AsyncSession,
    tools: list[dict[str, Any]],
    *,
    agent_type: str | None,
) -> list[dict[str, Any]]:
    """tool 목록을 agent context 기반으로 필터링 후 반환.

    Args:
        tools: ``[{name, manifest?, ...}]`` 또는 ``[{name, compatible_agents, ...}]``
               (manifest 가 dict 로 주어지면 정책 추출, 없으면 DB 에서 다시 조회 안 함)
        agent_type: 호출자 agent context. ``None`` 이면 require/exclude 정책 skip,
                    restrict 정책은 모두 통과 (필터 효력 없음).
    """
    if not tools:
        return tools

    agent_tags: list[str] | None = None
    if agent_type:
        agent = await session.get(Agent, agent_type)
        if agent is not None:
            agent_tags = list(agent.common_tags or [])

    out: list[dict[str, Any]] = []
    for t in tools:
        manifest = t.get("manifest") if isinstance(t, dict) else None
        # search_tools 의 응답에는 manifest 가 없을 수 있다 — name 으로 DB 재조회
        # 까지는 비용이 커서 skip. 이 경우 정책 적용 불가 → 통과 (default 노출).
        if manifest is None and t.get("compatible_agents") is not None:
            # search_tools 응답의 compatible_agents = restrict_agents
            restrict = list(t.get("compatible_agents") or [])
            if restrict and agent_type and agent_type not in restrict:
                continue
            out.append(t)
            continue

        if is_compatible(manifest, agent_type=agent_type, agent_tags=agent_tags):
            out.append(t)

    return out


__all__ = ["extract_policy", "is_compatible", "filter_tools_for_agent"]
