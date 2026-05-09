"""governance: audit_log + soft delete + usage stats

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-08

거버넌스 기능을 위한 통합 마이그레이션 (Agent 31).

추가/변경:
    1) ``audit_log`` 테이블 신설
       - INSERT/UPDATE/DELETE/RESTORE/ACCESS/VIEW 이벤트 기록
       - field_changes JSONB 에 ``{field: [old, new]}`` 저장
       - record_id / actor / action / created_at 인덱스
    2) ``records.deleted_at`` 컬럼 + 부분 인덱스 (soft delete)
       - 부분 인덱스는 ``WHERE deleted_at IS NULL`` 인 행만 인덱싱하여
         활성 레코드 조회를 빠르게 한다.
    3) ``records.read_count`` / ``records.last_accessed_at`` 컬럼
       - 사용량 통계: GET /api/records/{id}, /api/data 응답 시 증가.

Agent 30 의 마이그레이션 0007 (mcp_server) 을 의존한다 (down_revision='0007').
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ audit_log
    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("record_id", sa.String(length=80), nullable=True),
        sa.Column("actor", sa.String(length=100), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column(
            "field_changes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_audit_record", "audit_log", ["record_id"])
    op.create_index("idx_audit_actor", "audit_log", ["actor"])
    op.create_index("idx_audit_action", "audit_log", ["action"])
    op.create_index("idx_audit_created_at", "audit_log", ["created_at"])

    # ------------------------------------------------------- records.deleted_at
    op.add_column(
        "records",
        sa.Column(
            "deleted_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_records_deleted_at",
        "records",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # -------------------------------------------- records.read_count / last_accessed_at
    op.add_column(
        "records",
        sa.Column(
            "read_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "records",
        sa.Column(
            "last_accessed_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("records", "last_accessed_at")
    op.drop_column("records", "read_count")

    op.drop_index(
        "idx_records_deleted_at",
        table_name="records",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_column("records", "deleted_at")

    op.drop_index("idx_audit_created_at", table_name="audit_log")
    op.drop_index("idx_audit_action", table_name="audit_log")
    op.drop_index("idx_audit_actor", table_name="audit_log")
    op.drop_index("idx_audit_record", table_name="audit_log")
    op.drop_table("audit_log")
