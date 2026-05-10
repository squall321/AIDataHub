"""SQLite 호환 미러 ORM 모델.

Agent 1 의 ``api.db.models`` 와 동일 컬럼 구조이지만 PostgreSQL 전용 타입을
SQLite 호환 타입으로 치환했다.

치환:
    JSONB              → JSON
    ARRAY(TEXT)        → JSON  (list[str] 직렬화)
    TIMESTAMP(tz=True) → DateTime(tz=True)
    BIGSERIAL          → BigInteger autoincrement

테스트 픽스처에서 ``sys.modules['api.db.models']`` 를 본 모듈로 교체한 뒤
``api.ingest.db_writer`` 를 reload 해 미러 모델을 바인딩한다.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)
from sqlalchemy.types import JSON


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint(
            "data_type", "team", "group", "year", "seq",
            name="uq_records_natural_key",
        ),
    )
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    data_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Migration 0011: soft taxonomy 컬럼.
    doc_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    team: Mapped[str] = mapped_column(String(10), nullable=False)
    group: Mapped[str] = mapped_column(String(20), nullable=False)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    agents: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    schema_version: Mapped[str] = mapped_column(
        String(10), nullable=False, default="1.0"
    )
    content: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    department: Mapped[str] = mapped_column(
        String(100), nullable=False, default=""
    )
    project: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")

    # ---- Attachment summary (Migration 0003) -----------------------------
    has_attachments: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    attachment_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # ---- Extended classification metadata (Migration 0006) ---------------
    classification: Mapped[str] = mapped_column(
        String(20), nullable=False, default="internal", server_default="internal"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    domain: Mapped[str | None] = mapped_column(String(100), nullable=True)
    subject_keywords: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_system: Mapped[str | None] = mapped_column(String(50), nullable=True)
    language: Mapped[str] = mapped_column(
        String(10), nullable=False, default="ko", server_default="ko"
    )
    parent_record_id: Mapped[str | None] = mapped_column(
        String(80),
        ForeignKey("records.id", ondelete="SET NULL", name="fk_records_parent"),
        nullable=True,
    )
    derivation: Mapped[str] = mapped_column(
        String(20), nullable=False, default="original", server_default="original"
    )
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    quality_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)

    # ---- Agent discovery hints (Migration 0007) -------------------------
    agent_hints: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_record_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    query_examples: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    access_pattern: Mapped[str] = mapped_column(
        String(20), nullable=False, default="occasional", server_default="occasional"
    )

    # ---- Soft delete + usage stats (Migration 0008) ----------------------
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    read_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    sections: Mapped[list["RecordSection"]] = relationship(
        back_populates="record",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
    agent_links: Mapped[list["AgentRecord"]] = relationship(
        back_populates="record",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    attachments: Mapped[list["RecordAttachment"]] = relationship(
        back_populates="record",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
    parent: Mapped["Record | None"] = relationship(
        "Record",
        remote_side="Record.id",
        back_populates="children",
        foreign_keys="Record.parent_record_id",
    )
    children: Mapped[list["Record"]] = relationship(
        "Record",
        back_populates="parent",
        foreign_keys="Record.parent_record_id",
    )


class RecordSection(Base):
    __tablename__ = "record_sections"
    __table_args__ = (
        UniqueConstraint(
            "record_id", "section_id", name="uq_sections_record_section"
        ),
    )
    # SQLite 의 INTEGER PRIMARY KEY 만 자동증가하므로 BigInteger 의 SQLite 변종을
    # Integer 로 강제한다. 본 미러 모델은 PG 모델 컬럼명/타입 호환성 검증 목적이며
    # ID 자체의 폭이 중요하지 않다.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    record_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("records.id", ondelete="CASCADE"),
        nullable=False,
    )
    section_id: Mapped[str] = mapped_column(String(20), nullable=False)
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    figure_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    table_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    record: Mapped[Record] = relationship(back_populates="sections")


class Agent(Base):
    __tablename__ = "agents"
    agent_type: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    common_tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    data_types: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Migration 0011: agent expected-schema 컬럼.
    required_doc_type: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    required_tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    excluded_tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    record_links: Mapped[list["AgentRecord"]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DocType(Base):
    __tablename__ = "doc_types"
    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expected_sections: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentRecord(Base):
    __tablename__ = "agent_records"
    agent_type: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("agents.agent_type", ondelete="CASCADE"),
        primary_key=True,
    )
    record_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("records.id", ondelete="CASCADE"),
        primary_key=True,
    )
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    agent: Mapped[Agent] = relationship(back_populates="record_links")
    record: Mapped[Record] = relationship(back_populates="agent_links")


class RecordAttachment(Base):
    __tablename__ = "record_attachments"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    record_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("records.id", ondelete="CASCADE"),
        nullable=False,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hash_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    section_ref: Mapped[str | None] = mapped_column(String(20), nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    record: Mapped[Record] = relationship(back_populates="attachments")


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    record_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    field_changes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "Agent",
    "AgentRecord",
    "ApiKey",
    "AuditLog",
    "Base",
    "DocType",
    "Record",
    "RecordAttachment",
    "RecordSection",
]
