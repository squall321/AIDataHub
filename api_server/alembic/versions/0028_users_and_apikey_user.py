"""users — HWAX 포털 SSO(JIT) 사용자 테이블.

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-19

배경:
    HWAX 포털 SSO 첫 로그인 시 이메일 기준으로 사용자를 just-in-time 생성한다.
    발급되는 SSO ApiKey 는 ``name='sso:<email>'`` 규칙으로 연결되므로 v1 에서는
    api_keys 에 FK 컬럼을 추가하지 않는다(이름 규칙만으로 충분 — 회전/폐기 가능).

테이블:
    users : id(UUID PK), email(unique-ci), name, sso_subject, created_at

비고:
    - ``gen_random_uuid()`` 사용을 위해 ``pgcrypto`` 확장이 필요하다.
    - 이메일 유일성은 ``lower(email)`` 함수형 unique 인덱스로 대소문자 무시.
    - 기존 0005/0027 의 explicit postgresql 타입 스타일을 따른다.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # gen_random_uuid() 제공.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("sso_subject", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # 대소문자 무시 유일 이메일.
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")
