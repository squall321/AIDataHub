"""HNSW vector index — replace ivfflat on record_sections.embedding

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-23

ivfflat → HNSW 전환.

배경:
    - 0004 가 ``record_sections.embedding`` 에 ivfflat (lists=100) 인덱스를 만들었다.
    - pgvector 0.5+ 의 HNSW 는 동일 recall 에서 3~10× 빠른 query, lists 튜닝 불필요.
    - 데이터가 적은 ``agent_sample_embeddings`` 는 seq scan 으로 충분 (0016 노트)
      → 본 마이그레이션은 record_sections 만 다룬다.

파라미터:
    - m=16, ef_construction=64  → pgvector 권장 default. 인덱스 빌드 시간/메모리와
      recall 의 균형. 더 정확하게 하려면 m=24, ef_construction=200 (느림).
    - 부분 인덱스 ``WHERE embedding IS NOT NULL`` 유지 — null 행 인덱스 제외.

실패 안전:
    - pgvector 가 0.5 미만이면 ``USING hnsw`` 가 syntax error → downgrade 로 ivfflat 복구.
    - CREATE INDEX 가 무거우면 운영 중 ``CONCURRENTLY`` 로 바꿔야 함 (alembic 외부에서).
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from alembic import op

    # 기존 ivfflat 인덱스 제거
    op.execute("DROP INDEX IF EXISTS idx_sections_embedding")

    # HNSW 인덱스 신규 생성. m / ef_construction 은 pgvector 권장 default.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sections_embedding_hnsw
            ON record_sections USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
            WHERE embedding IS NOT NULL
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS idx_sections_embedding_hnsw")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sections_embedding
            ON record_sections USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
            WHERE embedding IS NOT NULL
        """
    )
