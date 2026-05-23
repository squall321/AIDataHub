"""record_sections.section_path — 인용 맥락 (부모 섹션 제목 체인).

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-23

배경:
    LLM 이 인용할 때 ``§4.2`` 만 보이면 어느 장의 4.2 인지 모호하다. ingest
    시점에 부모 섹션 제목 체인을 평탄화 단계에서 계산해 ``section_path`` 컬럼
    (예: ``1. 개요 > 1.2 범위``) 에 저장하면 search 응답에서 그대로 노출 가능.

호환성:
    기존 행은 NULL — 재적재 시에만 채워진다. 응답 측에서 NULL 은 표시 안 함.
    인덱스는 만들지 않음 (검색 키 아님, 표시 용).
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        "ALTER TABLE record_sections "
        "ADD COLUMN IF NOT EXISTS section_path TEXT"
    )


def downgrade() -> None:
    from alembic import op

    op.execute(
        "ALTER TABLE record_sections DROP COLUMN IF EXISTS section_path"
    )
