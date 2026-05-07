"""사업부(division) ↔ 팀(team) 정적 매핑.

VSCode 확장 폼의 셀렉트박스 옵션 소스. 운영 단계에서 ``records.division/team``
distinct 결과와 머지하는 향상이 가능하지만, 1차에서는 정적 매핑으로 충분하다.

설계 노트:
    - division 코드는 2~4자 대문자 ASCII (id_format 검증 규약).
    - team 코드는 2~5자 대문자 ASCII.
    - 신규 사업부/팀 추가는 본 파일을 직접 편집하면 된다.
"""
from __future__ import annotations

# 사업부 코드 → 표시 순서 (확장 UI 의 select 옵션 순서를 결정)
DIVISIONS: list[str] = ["HE", "EV", "PT", "DA", "MX", "VD"]

# 사업부별 팀 코드 매핑.
TEAMS: dict[str, list[str]] = {
    "HE": ["CAE", "Test", "Design"],
    "EV": ["BMS", "Battery", "Motor"],
    "PT": ["Material", "Process"],
    "DA": ["AI", "Data"],
    "MX": ["MFG", "QA"],
    "VD": ["DEV", "PLM"],
}


__all__ = ["DIVISIONS", "TEAMS"]
