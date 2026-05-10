"""rename records.division → team, records.team → group

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-11

조직 계층 명칭을 실조직에 맞춘다 (HE 팀 → CAE 그룹).

변경 매핑:
    - ``records.team``      → ``records.group``  (Pass 1)
    - ``records.division``  → ``records.team``   (Pass 2)

순서 중요: 먼저 기존 ``team`` 컬럼을 ``group`` 으로 옮긴 뒤,
``division`` 컬럼을 ``team`` 으로 옮겨야 충돌이 없다.

또한 다음 객체들을 함께 갱신한다:
    - ``uq_records_natural_key`` 유니크 제약 (drop → recreate with new names)
    - ``idx_records_div_team`` → ``idx_records_team_group`` 인덱스 rename

기존 데이터(`HE`, `CAE` 등 문자열 값) 은 그대로 보존된다 — 컬럼 이름만 바뀐다.

reverse migration 은 컬럼 이름을 원복하며, 데이터는 동일하게 보존된다.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """team→group, division→team 순으로 안전하게 rename."""
    # ------------------------------------------------------------------
    # 1) 기존 인덱스/유니크 제약 제거 (rename 충돌 방지)
    # ------------------------------------------------------------------
    op.drop_index("idx_records_div_team", table_name="records")
    op.drop_constraint("uq_records_natural_key", "records", type_="unique")

    # ------------------------------------------------------------------
    # 2) Pass 1: team → group
    #    (반드시 division→team 보다 먼저 — 그렇지 않으면 새 team 이 즉시
    #    division 의 자리에 덮어쓰여 데이터 충돌이 발생한다.)
    # ------------------------------------------------------------------
    op.alter_column("records", "team", new_column_name="group")

    # ------------------------------------------------------------------
    # 3) Pass 2: division → team
    # ------------------------------------------------------------------
    op.alter_column("records", "division", new_column_name="team")

    # ------------------------------------------------------------------
    # 4) 인덱스 / 유니크 제약 재생성 — 새 컬럼명 기준
    # ------------------------------------------------------------------
    op.create_unique_constraint(
        "uq_records_natural_key",
        "records",
        ["data_type", "team", "group", "year", "seq"],
    )
    op.create_index(
        "idx_records_team_group",
        "records",
        ["team", "group"],
    )


def downgrade() -> None:
    """역순으로 원복: team→division, group→team."""
    op.drop_index("idx_records_team_group", table_name="records")
    op.drop_constraint("uq_records_natural_key", "records", type_="unique")

    # 역순도 마찬가지로 순서 중요:
    #   1) team → division 먼저 (team 자리를 비워야 group 이 들어올 수 있다)
    op.alter_column("records", "team", new_column_name="division")
    #   2) group → team
    op.alter_column("records", "group", new_column_name="team")

    op.create_unique_constraint(
        "uq_records_natural_key",
        "records",
        ["data_type", "division", "team", "year", "seq"],
    )
    op.create_index(
        "idx_records_div_team",
        "records",
        ["division", "team"],
    )
