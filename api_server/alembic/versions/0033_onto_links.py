# 카드 ↔ 온톨로지 객체 연결의 파생 표 넷(카드 낱말 · 객체 연결 · 이어진 객체의 이름 · 상태)을 만드는 이행
"""카드 ↔ 온톨로지 객체 연결 표 — record_mentions · record_entities · onto_terms · onto_state

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-21

배경(HWAXPortal docs/expert-ontology 6절 — 사용자 결정 11-21):
    지식카드를 정본 허브의 객체 키에 잇는다. 카드 쪽 낱말(``record_mentions``)은 카드가 스스로 가진 구조에서
    뽑아 허브 없이 어느 박스에서나 짓고, 연결(``record_entities``)은 허브에 닿는 박스에서 허브가 exact 로 확인한
    이름으로 잇는다. 네 표 모두 레코드와 규칙 · 사전에서 다시 지을 수 있는 **파생 표**다 — 조각 동기화 · 병합은
    이 표를 모르고(박스마다 ``deploy/apptainer/onto.sh`` 가 짓는다), 레코드가 지워지면 외래키로 함께 지워진다.

    새 표만 만든다 — 지금 있는 표 · 색인은 건드리지 않아 잠금이 없다.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "record_mentions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("record_id", sa.String(80), sa.ForeignKey("records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("type_slug", sa.String(60), nullable=False, server_default=""),
        sa.Column("norm", sa.Text(), nullable=False, server_default=""),
        sa.Column("term", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("record_id", "source", "type_slug", "norm", name="uq_record_mentions"),
    )
    op.create_index("idx_record_mentions_record", "record_mentions", ["record_id"])
    op.create_index("idx_record_mentions_norm", "record_mentions", ["norm"])

    op.create_table(
        "record_entities",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("record_id", sa.String(80), sa.ForeignKey("records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hub", sa.String(80), nullable=False),
        sa.Column("type_slug", sa.String(60), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("found_in", sa.String(12), nullable=False),
        sa.Column("strong", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("record_id", "hub", "type_slug", "key", name="uq_record_entities"),
    )
    op.create_index("idx_record_entities_record", "record_entities", ["record_id"])
    op.create_index("idx_record_entities_object", "record_entities", ["hub", "type_slug", "key"])

    op.create_table(
        "onto_terms",
        sa.Column("hub", sa.String(80), primary_key=True),
        sa.Column("type_slug", sa.String(60), primary_key=True),
        sa.Column("norm", sa.Text(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
    )
    op.create_index("idx_onto_terms_object", "onto_terms", ["hub", "type_slug", "key"])

    op.create_table(
        "onto_state",
        sa.Column("name", sa.String(100), primary_key=True),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("onto_state")
    op.drop_index("idx_onto_terms_object", table_name="onto_terms")
    op.drop_table("onto_terms")
    op.drop_index("idx_record_entities_object", table_name="record_entities")
    op.drop_index("idx_record_entities_record", table_name="record_entities")
    op.drop_table("record_entities")
    op.drop_index("idx_record_mentions_norm", table_name="record_mentions")
    op.drop_index("idx_record_mentions_record", table_name="record_mentions")
    op.drop_table("record_mentions")
