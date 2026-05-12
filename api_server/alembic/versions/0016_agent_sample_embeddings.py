"""agent_sample_embeddings — routing-signal embeddings for agents.sample_queries

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-11

agents.sample_queries (Migration 0014) 를 ``recommend_agents`` 라우팅에
실제로 활용하기 위한 벡터 인덱스 테이블.

운영 흐름:
    1. 운영자가 admin UI 에서 agent.sample_queries 를 수정.
    2. ``agent_svc.create/update_agent`` 가 ``sample_embedding_svc.sync_agent_samples``
       을 호출 → 이 테이블의 해당 agent rows 를 전체 교체.
    3. ``recommend_agents`` 가 record-section 의미검색 결과 + 이 테이블
       의미검색 결과를 ``SAMPLE_WEIGHT`` 로 가중 합산.

차원은 ``EMBEDDING_DIM`` (0013 이후 768) 와 정합. 운영 PG 에는
``pgvector`` 확장이 이미 설치되어 있다고 가정 (Migration 0004).

기존 agent 들의 backfill: ``POST /api/agents/{type}/resync-samples`` 또는
다음 update 시 자동 동기화.
"""
from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "768"))


def upgrade() -> None:
    op.create_table(
        "agent_sample_embeddings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("agent_type", sa.Text(), nullable=False),
        sa.Column("sample_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_sample_embeddings"),
        sa.ForeignKeyConstraint(
            ["agent_type"],
            ["agents.agent_type"],
            name="fk_agent_sample_emb_agent",
            ondelete="CASCADE",
        ),
    )
    # pgvector 컬럼은 raw SQL 로 (alembic 이 vector 타입을 모름).
    op.execute(
        "ALTER TABLE agent_sample_embeddings "
        f"ADD COLUMN embedding vector({_EMBEDDING_DIM})"
    )
    op.create_index(
        "idx_agent_sample_emb_agent",
        "agent_sample_embeddings",
        ["agent_type"],
    )
    # ivfflat 인덱스는 데이터 양이 너무 적어 (보통 < 수백 행) 효과 없음.
    # 필요 시 추후 추가. seq scan 도 빠르게 동작.


def downgrade() -> None:
    op.drop_index("idx_agent_sample_emb_agent", table_name="agent_sample_embeddings")
    op.drop_table("agent_sample_embeddings")
