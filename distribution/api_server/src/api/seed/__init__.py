"""표준 에이전트 시드 패키지.

``python -m api.seed`` 로 실행해 ``agents`` 테이블에 표준 에이전트
정의(``STANDARD_AGENTS``)를 멱등(idempotent)하게 적재한다.
"""
from __future__ import annotations

from .agents_data import STANDARD_AGENTS
from .cli import main, seed_agents

__all__ = ["STANDARD_AGENTS", "main", "seed_agents"]
