"""doc_types taxonomy + agent expected-schema + record.doc_type

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-11

다음을 추가한다:

1. ``doc_types`` 테이블 (소프트 taxonomy, ``records.doc_type`` 의 권장 어휘):
   - ``code``  PK VARCHAR(40)
   - ``name``  VARCHAR(120)
   - ``description``  TEXT default ''
   - ``expected_sections``  TEXT[]  default '{}'
   - ``created_at``  TIMESTAMPTZ default now()
   - ``idx_doc_types_code`` index (PK 외 추가 — 작은 테이블이라 큰 부담 없음)

2. ``agents`` 테이블 확장 (에이전트가 기대하는 스키마 명세):
   - ``required_doc_type``  VARCHAR(40)  nullable
   - ``required_tags``      TEXT[] default '{}'
   - ``excluded_tags``      TEXT[] default '{}'

3. ``records`` 테이블 확장:
   - ``doc_type``  VARCHAR(40)  nullable  — 어느 doc_type 에 속하는지 (자유)

4. 초기 4개 doc_type seed:
   - manual / report / checklist / training

검증은 인제스트 시 warn-only — 본 migration 은 단순 schema 확장만 수행한다.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Initial seed rows (id-stable; 추후 변경은 별도 마이그레이션 또는 admin API 로)
# ---------------------------------------------------------------------------
_SEED_DOC_TYPES: list[dict[str, str]] = [
    {
        "code": "manual",
        "name": "Manual",
        "description": "사용·운영 매뉴얼 / 가이드.",
    },
    {
        "code": "report",
        "name": "Report",
        "description": "분석·시험 결과 보고서.",
    },
    {
        "code": "checklist",
        "name": "Checklist",
        "description": "점검표 / 절차 체크리스트.",
    },
    {
        "code": "training",
        "name": "Training",
        "description": "교육·훈련 자료.",
    },
]


def upgrade() -> None:
    # ------------------------------------------------------------------ doc_types
    op.create_table(
        "doc_types",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "expected_sections",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("code", name="pk_doc_types"),
    )
    op.create_index("idx_doc_types_code", "doc_types", ["code"])

    # ------------------------------------------------------------------ agents 확장
    op.add_column(
        "agents",
        sa.Column("required_doc_type", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "agents",
        sa.Column(
            "required_tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "agents",
        sa.Column(
            "excluded_tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )

    # ------------------------------------------------------------------ records.doc_type
    op.add_column(
        "records",
        sa.Column("doc_type", sa.String(length=40), nullable=True),
    )

    # ------------------------------------------------------------------ seed 4 rows
    doc_types = sa.table(
        "doc_types",
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
    )
    op.bulk_insert(doc_types, _SEED_DOC_TYPES)


def downgrade() -> None:
    op.drop_column("records", "doc_type")

    op.drop_column("agents", "excluded_tags")
    op.drop_column("agents", "required_tags")
    op.drop_column("agents", "required_doc_type")

    op.drop_index("idx_doc_types_code", table_name="doc_types")
    op.drop_table("doc_types")
