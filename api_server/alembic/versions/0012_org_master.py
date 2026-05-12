"""org_teams + org_groups master tables + seed transfer

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-11

조직 마스터 테이블을 도입한다. 기존 ``api.seed.teams`` 파이썬 상수에 박혀
있던 ``TEAMS`` / ``GROUPS`` 값을 DB 로 이전해, 운영자가 코드 수정 없이
대시보드/REST API 에서 추가/수정/삭제할 수 있게 한다.

추가 객체:

1. ``org_teams`` (code PK, name, description, is_active, created_at, updated_at)
2. ``org_groups`` (team_code FK→org_teams.code, code, ... composite PK (team_code, code))
3. ``idx_org_groups_team`` 인덱스

데이터 이전:
    - ``api.seed.teams.TEAMS`` 6개 → ``org_teams``
    - ``api.seed.teams.GROUPS`` 16개 → ``org_groups``
    - ``SELECT DISTINCT team, group FROM records`` 결과 중 마스터에 없는 값은
      WARNING 로그만 출력 (자동 추가 X — Strict 정책의 일관성).

records 와의 FK 는 의도적으로 걸지 않는다 (서비스 레이어 검증).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 기존 seed/teams.py 와 정확히 일치해야 한다 (회귀 방지).
_SEED_TEAMS: list[dict[str, str]] = [
    {"code": "HE", "name": "HE 팀"},
    {"code": "EV", "name": "EV 팀"},
    {"code": "PT", "name": "PT 팀"},
    {"code": "DA", "name": "DA 팀"},
    {"code": "MX", "name": "MX 팀"},
    {"code": "VD", "name": "VD 팀"},
]

_SEED_GROUPS: list[tuple[str, str, str]] = [
    ("HE", "CAE", "CAE"),
    ("HE", "Test", "Test"),
    ("HE", "Design", "Design"),
    ("EV", "BMS", "BMS"),
    ("EV", "Battery", "Battery"),
    ("EV", "Motor", "Motor"),
    ("PT", "Material", "Material"),
    ("PT", "Process", "Process"),
    ("DA", "AI", "AI"),
    ("DA", "Data", "Data"),
    ("MX", "MFG", "MFG"),
    ("MX", "QA", "QA"),
    ("VD", "DEV", "DEV"),
    ("VD", "PLM", "PLM"),
]


def upgrade() -> None:
    # ------------------------------------------------------------------ DDL
    op.create_table(
        "org_teams",
        sa.Column("code", sa.String(length=10), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    op.create_table(
        "org_groups",
        sa.Column(
            "team_code",
            sa.String(length=10),
            sa.ForeignKey("org_teams.code", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("code", sa.String(length=20), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_org_groups_team", "org_groups", ["team_code"])

    # -------------------------------------------------------------- seed transfer
    bind = op.get_bind()

    # team seed (멱등 INSERT — 이미 있으면 skip)
    for t in _SEED_TEAMS:
        bind.execute(
            sa.text(
                "INSERT INTO org_teams (code, name) VALUES (:code, :name) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            t,
        )

    # group seed
    for team_code, gcode, gname in _SEED_GROUPS:
        bind.execute(
            sa.text(
                "INSERT INTO org_groups (team_code, code, name) "
                "VALUES (:team_code, :code, :name) "
                "ON CONFLICT (team_code, code) DO NOTHING"
            ),
            {"team_code": team_code, "code": gcode, "name": gname},
        )

    # -------------------------------------------------------- orphan 검사 (warn-only)
    # records 테이블의 (team, group) distinct 가 마스터에 없으면 경고 출력.
    # 자동 추가하지 않는다 (Strict 정책 일관성).
    orphan_rows = bind.execute(
        sa.text(
            'SELECT DISTINCT r.team, r."group" '
            'FROM records r '
            'LEFT JOIN org_teams t  ON t.code = r.team '
            'LEFT JOIN org_groups g ON g.team_code = r.team AND g.code = r."group" '
            'WHERE t.code IS NULL OR g.code IS NULL'
        )
    ).fetchall()
    for team, group in orphan_rows:
        print(
            f"  [WARN 0012] records 에 마스터 미등록 조직값 존재: team='{team}' "
            f"group='{group}' (Strict 정책 도입 후 추가 ingest 가 막힐 수 있음)"
        )


def downgrade() -> None:
    op.drop_index("idx_org_groups_team", table_name="org_groups")
    op.drop_table("org_groups")
    op.drop_table("org_teams")
