"""record_sections.parent_section_id + chunk_index — 큰 섹션 슬라이딩 윈도우 분할.

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-23

배경:
    DOC 의 섹션이 길면 (>2000자) 단일 임베딩으로는 의미 희석. 슬라이딩 윈도우
    (기본 ~1000자 window + ~256자 overlap) 로 sub-chunk 분할 시 recall 개선.

스키마:
    - ``parent_section_id`` : 원본 (분할 전) section_id. NULL = 분할되지 않은 원본.
    - ``chunk_index``       : 0-based 분할 인덱스. NULL = 분할되지 않음.
    - sub-chunk 의 ``section_id`` 는 ``{parent}#{idx:02d}`` (예: 4.2#00, 4.2#01)
      형식으로 unique 제약을 만족하면서 부모를 식별 가능하게 한다.

활성화:
    인제스트 단계에서 env ``AIDH_CHUNK_WINDOW=on`` 일 때만 분할. 기본 off →
    기존 동작 유지 (회귀 0).
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        "ALTER TABLE record_sections "
        "ADD COLUMN IF NOT EXISTS parent_section_id VARCHAR(40)"
    )
    op.execute(
        "ALTER TABLE record_sections "
        "ADD COLUMN IF NOT EXISTS chunk_index SMALLINT"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sections_parent "
        "ON record_sections (parent_section_id) "
        "WHERE parent_section_id IS NOT NULL"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS idx_sections_parent")
    op.execute(
        "ALTER TABLE record_sections DROP COLUMN IF EXISTS chunk_index"
    )
    op.execute(
        "ALTER TABLE record_sections DROP COLUMN IF EXISTS parent_section_id"
    )
