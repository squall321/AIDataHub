"""팀(team) ↔ 그룹(group) 정적 매핑.

VSCode 확장 폼의 셀렉트박스 옵션 소스. 운영 단계에서 ``records.team/group``
distinct 결과와 머지하는 향상이 가능하지만, 1차에서는 정적 매핑으로 충분하다.

설계 노트:
    - team 코드는 2~4자 대문자 ASCII (id_format 검증 규약).
    - group 코드는 2~5자 대문자 ASCII.
    - 신규 팀/그룹 추가는 본 파일을 직접 편집하면 된다.
"""
from __future__ import annotations

# 팀 코드 → 표시 순서 (확장 UI 의 select 옵션 순서를 결정)
TEAMS: list[str] = ["HE", "EV", "PT", "DA", "MX", "VD"]

# 팀별 그룹 코드 매핑.
GROUPS: dict[str, list[str]] = {
    "HE": ["CAE", "Test", "Design"],
    "EV": ["BMS", "Battery", "Motor"],
    "PT": ["Material", "Process"],
    "DA": ["AI", "Data"],
    "MX": ["MFG", "QA"],
    "VD": ["DEV", "PLM"],
}


__all__ = ["TEAMS", "GROUPS"]
