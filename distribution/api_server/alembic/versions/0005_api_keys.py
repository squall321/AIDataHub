"""api_keys: API 키 인증 테이블

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-08

`api_keys` 테이블을 추가한다. SHA-256 해시된 키만 저장하고,
revoked / expires_at 기반 라이프사이클을 지원한다. 부분 인덱스
``WHERE NOT revoked`` 로 활성 키 조회를 최적화한다.

PostgreSQL 전용:
    - ``TEXT[]`` (agent_scopes), ``TIMESTAMPTZ``, partial index.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "agent_scopes",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("department", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "expires_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "revoked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column(
            "last_used_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_api_keys"),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )
    op.create_index(
        "idx_api_keys_hash",
        "api_keys",
        ["key_hash"],
        postgresql_where=sa.text("NOT revoked"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_api_keys_hash",
        table_name="api_keys",
        postgresql_where=sa.text("NOT revoked"),
    )
    op.drop_table("api_keys")
