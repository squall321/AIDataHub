"""attachments: record_attachments table + records.has_attachments/attachment_count

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-07

이 마이그레이션은 기존 ``figures`` 모델을 8 종류 (figure / document /
spreadsheet / media / archive / cad / drawing / data / other) 의
첨부 (attachment) 로 일반화하기 위해 ``record_attachments`` 테이블을
새로 생성하고, 부모 ``records`` 테이블에 두 개의 보조 컬럼
(``has_attachments`` / ``attachment_count``) 을 추가한다.

스토리지 레이아웃은 기존 ``figures/{record_id}/F001.png`` 와 동일하지만
파일명이 ``A{nnn}.{ext}`` (kind 무관) 로 일반화된다. 정적 마운트는
``/attachments`` 가 새로 생기고 ``/figures`` 도 (호환을 위해) 같은 폴더
또는 별도 폴더로 유지될 수 있다 — 본 마이그레이션은 DB 만 다룬다.

PostgreSQL 전용 GIN(jsonb_path_ops) 인덱스를 사용한다 — SQLite 등 타
DB 와 호환되지 않으나, 기존 0001 도 동일한 제약을 갖는다.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ records
    # 보조 컬럼: 첨부 존재 여부 + 카운트 (애플리케이션이 INSERT 시 갱신).
    op.add_column(
        "records",
        sa.Column(
            "has_attachments",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
    )
    op.add_column(
        "records",
        sa.Column(
            "attachment_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # ----------------------------------------------------- record_attachments
    op.create_table(
        "record_attachments",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("record_id", sa.String(length=80), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("hash_sha256", sa.String(length=64), nullable=True),
        sa.Column("section_ref", sa.String(length=20), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["records.id"],
            ondelete="CASCADE",
            name="fk_record_attachments_record_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_record_attachments"),
    )
    op.create_index(
        "idx_attachments_record",
        "record_attachments",
        ["record_id"],
    )
    op.create_index(
        "idx_attachments_kind",
        "record_attachments",
        ["kind"],
    )
    op.create_index(
        "idx_attachments_extra",
        "record_attachments",
        ["extra"],
        postgresql_using="gin",
        postgresql_ops={"extra": "jsonb_path_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "idx_attachments_extra",
        table_name="record_attachments",
        postgresql_using="gin",
    )
    op.drop_index("idx_attachments_kind", table_name="record_attachments")
    op.drop_index("idx_attachments_record", table_name="record_attachments")
    op.drop_table("record_attachments")

    op.drop_column("records", "attachment_count")
    op.drop_column("records", "has_attachments")
