"""[DEPRECATED] 팀(team) ↔ 그룹(group) 정적 매핑.

Migration 0012 이후 ``org_teams`` / ``org_groups`` 마스터 테이블이 권위 소스다.
``/api/meta/options`` 는 DB 조회로 전환됐고, 신규 팀/그룹 추가는 대시보드의
"조직 관리" 탭 또는 ``/api/org/teams`` ``/api/org/groups`` 라우터를 사용한다.

이 모듈의 상수는 0012 마이그레이션의 초기 시드값 참조용으로만 남겨둔다.
런타임 코드는 더 이상 본 모듈을 import 하지 않는다 (호환성 유지를 위해
파일은 보존).
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
