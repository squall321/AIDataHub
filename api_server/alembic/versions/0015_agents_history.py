"""agents_history — append-only audit log of agent CRUD operations

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-11

운영자가 system_prompt / retrieval_config 등 RAG 레시피를 잘못 수정해서
챗봇 품질이 떨어졌을 때 즉시 이전 버전을 확인·복원할 수 있도록 변경 이력을
보존한다.

테이블 ``agents_history``:

- ``id``                BIGSERIAL PK
- ``agent_type``        TEXT (FK 없음 — 삭제된 agent 의 이력도 보존)
- ``operation``         VARCHAR(10) — 'create' / 'update' / 'delete'
- ``snapshot``          JSONB — 변경 직후 agent 행의 전체 스냅샷 (delete 의
                        경우 삭제 직전 스냅샷)
- ``changed_by``        TEXT NULL — 향후 auth 연동 시 채워짐 (지금은 NULL)
- ``changed_at``        TIMESTAMPTZ default now()
- ``idx_agents_history_type``     (agent_type, changed_at DESC)
- ``idx_agents_history_changed_at`` (changed_at DESC)

설계 노트:
    - append-only — UPDATE/DELETE 금지 (서비스 레이어 컨벤션)
    - snapshot 은 JSONB 로 전체 스키마를 보관해 컬럼 추가/제거에 강건
    - agent_type FK 미설정 — 삭제 이력이 cascade 되지 않게
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agents_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("agent_type", sa.Text(), nullable=False),
        sa.Column("operation", sa.String(length=10), nullable=False),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("changed_by", sa.Text(), nullable=True),
        sa.Column(
            "changed_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agents_history"),
        sa.CheckConstraint(
            "operation IN ('create', 'update', 'delete')",
            name="ck_agents_history_operation",
        ),
    )
    op.create_index(
        "idx_agents_history_type",
        "agents_history",
        ["agent_type", sa.text("changed_at DESC")],
    )
    op.create_index(
        "idx_agents_history_changed_at",
        "agents_history",
        [sa.text("changed_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_agents_history_changed_at", table_name="agents_history")
    op.drop_index("idx_agents_history_type", table_name="agents_history")
    op.drop_table("agents_history")
