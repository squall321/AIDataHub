"""mcp_uploads.description_embedding — Wave-7 P1 도구 description 임베딩

Revision ID: 0024
Revises: 0023
Create Date: 2026-05-26

목적:
    wave-5 도구 (mcp_uploads) 의 description 을 e5-base 768d 로 임베딩하여
    ``recommend_agents(q)`` 응답에 ``relevant_tools`` 를 동봉.

추가 컬럼:
    - description_text     : 합성 텍스트 (description + when_to_use + example_calls
                             natural_language join). embedding 의 원본.
    - description_embedding: vector(EMBEDDING_DIM) — sections 와 동일 모델.

인덱스:
    - HNSW (m=16, ef_construction=64) — pgvector cosine ops.
"""
from __future__ import annotations

import os
from collections.abc import Sequence

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "768"))


def upgrade() -> None:
    from alembic import op

    op.execute(
        "ALTER TABLE mcp_uploads "
        "ADD COLUMN IF NOT EXISTS description_text TEXT"
    )
    op.execute(
        f"ALTER TABLE mcp_uploads "
        f"ADD COLUMN IF NOT EXISTS description_embedding vector({_EMBEDDING_DIM})"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_mcp_uploads_desc_embedding_hnsw "
        "ON mcp_uploads USING hnsw "
        "(description_embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS idx_mcp_uploads_desc_embedding_hnsw")
    op.execute("ALTER TABLE mcp_uploads DROP COLUMN IF EXISTS description_embedding")
    op.execute("ALTER TABLE mcp_uploads DROP COLUMN IF EXISTS description_text")
