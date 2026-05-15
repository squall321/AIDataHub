"""records.depth — 계층 깊이 (campaign=0, specimen=1, ...)

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-15

하이브리드 데이터 모델(campaign 부모 + specimen 자식, parent_record_id 연결)
에서 "몇 번째 계층인지"를 ID 에 인코딩하지 않고 별도 컬럼으로 둔다.
ID 는 영구 불변(인용 키) 으로 남기고, 재부모화 시 depth 만 재계산한다.

upgrade:
    1. records.depth SMALLINT NOT NULL DEFAULT 0 추가.
    2. 기존 데이터 backfill — parent_record_id 체인을 따라 depth 계산
       (최대 8단까지 반복; 순환/고아는 0 유지).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "records",
        sa.Column(
            "depth",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    # backfill — parent 체인을 따라 반복적으로 depth = parent.depth + 1.
    # 루트(parent_record_id IS NULL)는 0. 최대 8회 반복이면 현실적 깊이 충분.
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE records SET depth = 0 WHERE parent_record_id IS NULL"))
    for _ in range(8):
        result = conn.execute(
            sa.text(
                """
                UPDATE records AS c
                SET depth = p.depth + 1
                FROM records AS p
                WHERE c.parent_record_id = p.id
                  AND c.parent_record_id IS NOT NULL
                  AND c.depth <> p.depth + 1
                """
            )
        )
        if result.rowcount == 0:
            break


def downgrade() -> None:
    op.drop_column("records", "depth")
