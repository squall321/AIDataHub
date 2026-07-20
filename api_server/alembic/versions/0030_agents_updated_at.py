"""agents updated_at + on-update trigger

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-20

배경: cae00(운영) merge-from-drive.sh 의 merge_table 이 ``agents`` 테이블에
"운영 쪽이 더 최근에 수정됐으면 dev 값으로 덮어쓰지 않는다"는 보호를 걸기 위해
``agents.updated_at`` 컬럼을 참조하지만(merge-from-drive.sh:94), 이 컬럼이
존재하지 않아 매 배포마다 SQL 에러로 병합 스크립트 전체가 그 자리에서 중단되고
있었다(``set -euo pipefail``). agents 이후 순번의 records/agent_records 등도
덩달아 병합되지 않는 부작용이 있었다.

Record(0002)와 동일한 패턴 — ORM 측 ``onupdate=func.now()`` 는 SQLAlchemy 경유
UPDATE 만 잡으므로, psql/시드 스크립트 등 ORM 외부 UPDATE 에 대비해 PostgreSQL
BEFORE UPDATE 트리거로 이중 보장한다. ``set_updated_at()`` 함수는 0002 에서
이미 생성되어 있어 재사용만 한다(재정의하지 않음).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.execute(
        """
        CREATE TRIGGER trg_agents_updated_at
          BEFORE UPDATE ON agents
          FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_agents_updated_at ON agents;")
    op.drop_column("agents", "updated_at")
