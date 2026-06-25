"""record-level 시그니처 임베딩 — 유사도 자동분류의 대량(ANN) 확장.

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-25

목적:
    find_similar_data(제안형 자동분류)가 후보를 매 호출 임베딩하던 O(N) 방식을
    pgvector ANN(hnsw)으로 교체하기 위한 토대. records 에 시그니처 임베딩 컬럼을
    두고, 데이터가 수만 건+ 로 늘어도 ``signature_embedding <=> qvec`` 으로 O(log N)
    근사 최근접 검색이 되게 한다.

추가 컬럼:
    - signature_embedding: vector(EMBEDDING_DIM) — 시그니처(제목/caption + 헤더)를
      e5-base 768d 로 임베딩. record_sections.embedding 과 동일 모델.

인덱스:
    - HNSW (m=16, ef_construction=64) cosine — record_sections 와 동일 패턴.
      partial(WHERE NOT NULL) — 백필 전 NULL 행은 인덱스에서 제외.
"""
from __future__ import annotations

import os
from collections.abc import Sequence

revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "768"))


def upgrade() -> None:
    from alembic import op

    op.execute(
        f"ALTER TABLE records "
        f"ADD COLUMN IF NOT EXISTS signature_embedding vector({_EMBEDDING_DIM})"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_records_signature_embedding_hnsw "
        "ON records USING hnsw "
        "(signature_embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64) "
        "WHERE signature_embedding IS NOT NULL"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS idx_records_signature_embedding_hnsw")
    op.execute("ALTER TABLE records DROP COLUMN IF EXISTS signature_embedding")
