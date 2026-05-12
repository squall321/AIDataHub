"""embedding column dim 384 → 768 + reset

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-11

``record_sections.embedding`` 컬럼 차원을 384 → 768 로 확장한다.
``intfloat/multilingual-e5-base`` (dim=768) 등 더 큰 임베더로 전환하기 위함.

작업:
    1. 기존 임베딩 값을 모두 NULL 로 리셋 (차원 mismatch 방지).
    2. ``ALTER COLUMN embedding TYPE vector(768)``.
    3. 운영 측에서 ``EMBEDDING_DIM=768`` + ``EMBEDDING_PROVIDER=e5_base`` 설정
       후 ``POST /api/jobs/embed`` 로 backfill.

downgrade 는 역순으로 768→384 복원 (NULL reset → vector(384)).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # 1) 기존 임베딩 NULL 리셋 — 차원 변경 ALTER 가 데이터 손상 없이 진행.
    bind.execute(sa.text("UPDATE record_sections SET embedding = NULL"))
    # 2) 컬럼 타입 변경 (vector(384) → vector(768)).
    op.execute(
        "ALTER TABLE record_sections ALTER COLUMN embedding TYPE vector(768)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE record_sections SET embedding = NULL"))
    op.execute(
        "ALTER TABLE record_sections ALTER COLUMN embedding TYPE vector(384)"
    )
