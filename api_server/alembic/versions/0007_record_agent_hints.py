"""record agent hints / related records / query examples / access pattern

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-08

AI 에이전트가 외부 source 코드를 읽지 않아도 record 자기 자신에게
어떤 추가 힌트를 받아 사용할 수 있게 4개 컬럼을 추가한다 (Agent 30 — Discovery
/ RAG-friendly API).

추가 컬럼:
    - agent_hints        : 에이전트가 이 record 를 어떻게 사용해야 하는지 사람이
                           작성한 자유 텍스트 힌트 (markdown 가능)
    - related_record_ids : 다른 레코드 ID 의 배열 (수동 큐레이션 관계 그래프)
    - query_examples     : 이 record 를 다루기 위한 자연어 쿼리 예시 배열
    - access_pattern     : 'frequent' | 'occasional' | 'rare' (UI/캐싱 전략 힌트)

PostgreSQL 전용:
    - ``TEXT[]`` 컬럼은 GIN 인덱스를 따로 두진 않는다 (저빈도 read).
    - ``access_pattern`` 컬럼은 단순 b-tree 인덱스 (분포 통계용).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "records",
        sa.Column("agent_hints", sa.Text(), nullable=True),
    )
    op.add_column(
        "records",
        sa.Column(
            "related_record_ids",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "records",
        sa.Column(
            "query_examples",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "records",
        sa.Column(
            "access_pattern",
            sa.String(length=20),
            nullable=False,
            server_default="occasional",
        ),
    )

    op.create_index(
        "idx_records_access_pattern", "records", ["access_pattern"]
    )
    op.create_index(
        "idx_records_related",
        "records",
        ["related_record_ids"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_records_related",
        table_name="records",
        postgresql_using="gin",
    )
    op.drop_index("idx_records_access_pattern", table_name="records")

    op.drop_column("records", "access_pattern")
    op.drop_column("records", "query_examples")
    op.drop_column("records", "related_record_ids")
    op.drop_column("records", "agent_hints")
