"""record metadata extension: classification/status/domain/derivation/capabilities

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-08

확장 메타데이터 컬럼들을 ``records`` 에 추가하여 ``일반화 데이터(generalized
data)`` 입력을 받아들일 때 슬라이스 가능한 분류 키를 강화한다.

추가되는 컬럼:
    - classification     : public/internal/confidential/restricted (기본 'internal')
    - status             : draft/review/approved/deprecated (기본 'draft')
    - domain             : 상위 도메인 라벨
    - subject_keywords   : tags 보다 더 풍부한 키워드 배열
    - source_system      : 데이터 출처 시스템
    - language           : ko/en/mixed (기본 'ko')
    - parent_record_id   : 파생/자식 문서를 위한 self-FK
    - derivation         : original/extracted/aggregated/translated
    - capabilities       : 구조 형태 라벨 (sections/blocks/tables/figures/...)
    - quality_score      : 0..100
    - valid_from         : 유효 시작
    - valid_until        : 유효 종료

PostgreSQL 전용:
    - ``TEXT[]`` (subject_keywords/capabilities) GIN 인덱스
    - 자기참조 FK (``parent_record_id`` → ``records.id``, ON DELETE SET NULL)

Agent 10 의 ``has_attachments``/``attachment_count`` (0003) 와는
컬럼 수준에서 충돌하지 않는다 — 둘 다 ``records`` 에 컬럼만 append.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "records",
        sa.Column(
            "classification",
            sa.String(length=20),
            nullable=False,
            server_default="internal",
        ),
    )
    op.add_column(
        "records",
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="draft",
        ),
    )
    op.add_column(
        "records",
        sa.Column("domain", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "records",
        sa.Column(
            "subject_keywords",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "records",
        sa.Column("source_system", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "records",
        sa.Column(
            "language",
            sa.String(length=10),
            nullable=False,
            server_default="ko",
        ),
    )
    op.add_column(
        "records",
        sa.Column("parent_record_id", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "records",
        sa.Column(
            "derivation",
            sa.String(length=20),
            nullable=False,
            server_default="original",
        ),
    )
    op.add_column(
        "records",
        sa.Column(
            "capabilities",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "records",
        sa.Column("quality_score", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "records",
        sa.Column("valid_from", sa.Date(), nullable=True),
    )
    op.add_column(
        "records",
        sa.Column("valid_until", sa.Date(), nullable=True),
    )

    op.create_foreign_key(
        "fk_records_parent",
        "records",
        "records",
        ["parent_record_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_records_classification", "records", ["classification"]
    )
    op.create_index("idx_records_status", "records", ["status"])
    op.create_index("idx_records_domain", "records", ["domain"])
    op.create_index(
        "idx_records_capabilities",
        "records",
        ["capabilities"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_records_subject",
        "records",
        ["subject_keywords"],
        postgresql_using="gin",
    )
    op.create_index("idx_records_parent", "records", ["parent_record_id"])


def downgrade() -> None:
    op.drop_index("idx_records_parent", table_name="records")
    op.drop_index(
        "idx_records_subject", table_name="records", postgresql_using="gin"
    )
    op.drop_index(
        "idx_records_capabilities",
        table_name="records",
        postgresql_using="gin",
    )
    op.drop_index("idx_records_domain", table_name="records")
    op.drop_index("idx_records_status", table_name="records")
    op.drop_index("idx_records_classification", table_name="records")
    op.drop_constraint("fk_records_parent", "records", type_="foreignkey")

    op.drop_column("records", "valid_until")
    op.drop_column("records", "valid_from")
    op.drop_column("records", "quality_score")
    op.drop_column("records", "capabilities")
    op.drop_column("records", "derivation")
    op.drop_column("records", "parent_record_id")
    op.drop_column("records", "language")
    op.drop_column("records", "source_system")
    op.drop_column("records", "subject_keywords")
    op.drop_column("records", "domain")
    op.drop_column("records", "status")
    op.drop_column("records", "classification")
