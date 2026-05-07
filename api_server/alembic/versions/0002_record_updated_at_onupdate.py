"""record updated_at on-update trigger

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-07

ORM 측에서는 :func:`Record.updated_at` 컬럼에 ``onupdate=func.now()`` 가
붙어있어 SQLAlchemy 가 UPDATE 발생 시 자동으로 ``NOW()`` 를 바인드한다.
다만 ORM 외부 (psql, pg_admin, 외부 ETL) 에서 직접 UPDATE 가 들어오는
케이스에 대비해, PostgreSQL 측에 BEFORE UPDATE 트리거를 추가하여
``updated_at`` 을 항상 현재 시각으로 강제 갱신한다.

SQLite/오프라인 SQL 렌더 시에는 plpgsql DDL 이 그대로 출력되며 SQLite 에는
적용되지 않는다 (테스트는 ORM-side onupdate 만 검증).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_records_updated_at
          BEFORE UPDATE ON records
          FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_records_updated_at ON records;")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at();")
