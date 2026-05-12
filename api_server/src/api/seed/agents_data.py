"""[DEPRECATED] 표준 에이전트 정의.

Migration 0012/ask-keywords-from-db 이후 agents 는 DB 마스터 (``/api/agents``
CRUD + 대시보드) 에서 운영자가 직접 관리한다. 자동 시드는 빈 리스트로
유지하여 ``python -m api.seed`` 호출이 멱등 no-op 이 되게 한다.

코드 상수로 박힌 표준 agent 집합은 운영 단계에서 의도치 않게 삭제된 행을
재생성하는 부작용이 있었다 (team-group-mgmt 사이클의 정신과 충돌).
"""
from __future__ import annotations

from typing import TypedDict


class AgentSeed(TypedDict):
    agent_type: str
    name: str
    description: str
    common_tags: list[str]
    data_types: list[str]


# 빈 리스트 — agent 는 운영자가 REST API / 대시보드로 관리.
STANDARD_AGENTS: list[AgentSeed] = []


__all__ = ["AgentSeed", "STANDARD_AGENTS"]
