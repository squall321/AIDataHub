"""initial schema: records, record_sections, agents, agent_records

Revision ID: 0001
Revises:
Create Date: 2026-05-07

이 마이그레이션은 AI 데이터 허브의 기초 4개 테이블과 7개 인덱스를 생성한다.
- records           (PK: id VARCHAR(80), 자연키 UNIQUE)
- record_sections   (PK: BIGSERIAL, FK→records.id)
- agents            (PK: agent_type)
- agent_records     (PK: agent_type+record_id, FK→agents/records)

GIN 인덱스 3개 (records.agents, records.tags, records.content[jsonb_path_ops])는
PostgreSQL 전용 기능을 사용하므로 본 마이그레이션은 SQLite 등 타 DB와 호환되지 않는다.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ records
    op.create_table(
        "records",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("data_type", sa.String(length=20), nullable=False),
        sa.Column("division", sa.String(length=10), nullable=False),
        sa.Column("team", sa.String(length=20), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "agents",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "schema_version",
            sa.String(length=10),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("author", sa.String(length=100), nullable=False, server_default=""),
        sa.Column(
            "department",
            sa.String(length=100),
            nullable=False,
            server_default="",
        ),
        sa.Column("project", sa.String(length=100), nullable=True),
        sa.Column("version", sa.String(length=20), nullable=False, server_default="1.0"),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_records"),
        sa.UniqueConstraint(
            "data_type",
            "division",
            "team",
            "year",
            "seq",
            name="uq_records_natural_key",
        ),
    )
    op.create_index("idx_records_type", "records", ["data_type"])
    op.create_index("idx_records_div_team", "records", ["division", "team"])
    op.create_index("idx_records_year", "records", ["year"])
    op.create_index(
        "idx_records_agents",
        "records",
        ["agents"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_records_tags",
        "records",
        ["tags"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_records_content",
        "records",
        ["content"],
        postgresql_using="gin",
        postgresql_ops={"content": "jsonb_path_ops"},
    )

    # ----------------------------------------------------------- record_sections
    op.create_table(
        "record_sections",
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("record_id", sa.String(length=80), nullable=False),
        sa.Column("section_id", sa.String(length=20), nullable=False),
        sa.Column("level", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "figure_refs",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "table_refs",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["records.id"],
            ondelete="CASCADE",
            name="fk_record_sections_record_id",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_record_sections"),
        sa.UniqueConstraint(
            "record_id",
            "section_id",
            name="uq_sections_record_section",
        ),
    )
    op.create_index("idx_sections_record", "record_sections", ["record_id"])

    # ------------------------------------------------------------------- agents
    op.create_table(
        "agents",
        sa.Column("agent_type", sa.String(length=50), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "common_tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "data_types",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("agent_type", name="pk_agents"),
    )

    # ------------------------------------------------------------ agent_records
    op.create_table(
        "agent_records",
        sa.Column("agent_type", sa.String(length=50), nullable=False),
        sa.Column("record_id", sa.String(length=80), nullable=False),
        sa.Column(
            "priority",
            sa.SmallInteger(),
            nullable=False,
            server_default="1",
        ),
        sa.ForeignKeyConstraint(
            ["agent_type"],
            ["agents.agent_type"],
            ondelete="CASCADE",
            name="fk_agent_records_agent_type",
        ),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["records.id"],
            ondelete="CASCADE",
            name="fk_agent_records_record_id",
        ),
        sa.PrimaryKeyConstraint("agent_type", "record_id", name="pk_agent_records"),
    )
    op.create_index("idx_agent_records_agent", "agent_records", ["agent_type"])


def downgrade() -> None:
    op.drop_index("idx_agent_records_agent", table_name="agent_records")
    op.drop_table("agent_records")

    op.drop_table("agents")

    op.drop_index("idx_sections_record", table_name="record_sections")
    op.drop_table("record_sections")

    op.drop_index("idx_records_content", table_name="records", postgresql_using="gin")
    op.drop_index("idx_records_tags", table_name="records", postgresql_using="gin")
    op.drop_index("idx_records_agents", table_name="records", postgresql_using="gin")
    op.drop_index("idx_records_year", table_name="records")
    op.drop_index("idx_records_div_team", table_name="records")
    op.drop_index("idx_records_type", table_name="records")
    op.drop_table("records")
