"""표준 에이전트 정의.

사업부 문서 AI 데이터 허브에 등록되는 기본 에이전트 5종.
각 dict 는 ``agents`` 테이블의 한 행 (``agent_type`` PK) 에 대응한다.
"""
from __future__ import annotations

from typing import TypedDict


class AgentSeed(TypedDict):
    agent_type: str
    name: str
    description: str
    common_tags: list[str]
    data_types: list[str]


STANDARD_AGENTS: list[AgentSeed] = [
    {
        "agent_type": "iga-analyst",
        "name": "IGA 해석 분석가",
        "description": "IGA(등기하해석) 설정·검토. NURBS·LS-DYNA·KooRemapper 도메인.",
        "common_tags": ["IGA", "NURBS", "LS-DYNA", "KooRemapper"],
        "data_types": ["DOC", "SIM", "DATA"],
    },
    {
        "agent_type": "cae-reporter",
        "name": "CAE 보고서 작성자",
        "description": "해석 결과 보고서 작성. 결과 기준·서식·그래프 포함.",
        "common_tags": ["보고서", "해석", "결과", "기준"],
        "data_types": ["DOC", "SIM", "DATA"],
    },
    {
        "agent_type": "material-reviewer",
        "name": "재료 물성 검토자",
        "description": "재료 물성·시험 데이터·인증 기준 검토.",
        "common_tags": ["재료", "물성", "시험", "기준"],
        "data_types": ["DOC", "DATA"],
    },
    {
        "agent_type": "process-checker",
        "name": "공정 절차 검증자",
        "description": "공정 절차·체크리스트·품질 기준 검증.",
        "common_tags": ["공정", "절차", "품질", "체크리스트"],
        "data_types": ["DOC", "FORM"],
    },
    {
        "agent_type": "code-assistant",
        "name": "코드 어시스턴트",
        "description": "KooRemapper 등 사내 도구 코드 작업·API 참조.",
        "common_tags": ["코드", "API", "KooRemapper", "변환기"],
        "data_types": ["DOC"],
    },
]


__all__ = ["AgentSeed", "STANDARD_AGENTS"]
