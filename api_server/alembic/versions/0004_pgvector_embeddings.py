"""pgvector embeddings: record_sections.embedding + ivfflat index

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-08

이 마이그레이션은 ``record_sections`` 테이블에 시맨틱 검색을 위한
pgvector 컬럼을 추가한다. PostgreSQL ``vector`` 확장이 사전 설치된
이미지(``pgvector/pgvector:pg16``)를 사용한다고 가정한다.

추가되는 컬럼/인덱스:
    - ``record_sections.embedding``         vector(384)   NULL
    - ``record_sections.embedded_at``       TIMESTAMPTZ   NULL
    - ``record_sections.embedding_model``   VARCHAR(100)  NULL
    - ``idx_sections_embedding``  IVFFLAT (cosine, lists=100, partial WHERE NOT NULL)

차원 384 는 기본 dummy/sentence-transformers 모델 (mini-LM 계열) 과
호환되며, ``EMBEDDING_DIM`` 환경변수로 애플리케이션 측에서 변경 가능하다.
컬럼 차원은 마이그레이션 시점에 고정되므로 차원 변경 시에는 새로운
revision 이 필요하다.

PostgreSQL 전용 — SQLite 등 타 DB 에서는 동작하지 않는다 (테스트는
``conftest.py`` 가 ``vector`` 컬럼을 ``JSON`` 으로 컴파일한다).
"""
from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from alembic import op

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        "ALTER TABLE record_sections "
        "ADD COLUMN IF NOT EXISTS embedding vector(384)"
    )
    op.execute(
        "ALTER TABLE record_sections "
        "ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE record_sections "
        "ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(100)"
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sections_embedding
            ON record_sections USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
            WHERE embedding IS NOT NULL
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS idx_sections_embedding")
    op.execute(
        "ALTER TABLE record_sections DROP COLUMN IF EXISTS embedding_model"
    )
    op.execute(
        "ALTER TABLE record_sections DROP COLUMN IF EXISTS embedded_at"
    )
    op.execute(
        "ALTER TABLE record_sections DROP COLUMN IF EXISTS embedding"
    )
