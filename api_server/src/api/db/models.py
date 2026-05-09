"""SQLAlchemy 2.0 ORM 모델 (PostgreSQL 전용).

테이블:
    - records           : 최상위 레코드 (DOC/DATA/SIM/CAD/LOG/FORM/OTHER)
    - record_sections   : 레코드 본문 섹션 (RAG 청크 단위)
    - agents            : 에이전트 메타데이터 (Cline SR 등)
    - agent_records     : agent ↔ record N:M 매핑 (priority 포함)

설계 노트:
    - PostgreSQL 전용 타입 사용: ARRAY(TEXT), JSONB, TIMESTAMPTZ, BIGSERIAL.
    - PK `records.id`는 사람이 읽는 의미있는 코드 (예: 'DOC-HE-CAE-2026-000001').
    - `record_sections.id`는 BigInteger BIGSERIAL.
    - 향후 마이그레이션에서 `record_sections.embedding`(pgvector) 컬럼이 추가될 수 있다.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

# ---------------------------------------------------------------------------
# Vector type — pgvector 가 설치되어 있으면 ``vector(384)`` 컬럼으로,
# 없으면 SQLite 환경(테스트) 에서 ``TEXT`` 폴백으로 사용한다.
#
# 운영(PostgreSQL): ``pgvector.sqlalchemy.Vector(384)`` — 마이그레이션
# 0004_pgvector_embeddings 가 도입한 ``vector(384)`` 컬럼과 정확히 정합.
# SQLite(test): conftest 에서 ``@compiles(Vector, "sqlite")`` override 로
# ``TEXT`` DDL 을 발급하고, bind/result processor 가 ``"[v1, v2, ...]"``
# 형태로 직렬화한다.
# ---------------------------------------------------------------------------
try:
    from pgvector.sqlalchemy import Vector as _Vector  # type: ignore[import-not-found]

    _VECTOR_AVAILABLE = True
except ImportError:  # pragma: no cover — pgvector 패키지 없음
    from sqlalchemy import JSON as _Vector  # type: ignore[assignment]

    _VECTOR_AVAILABLE = False


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------
class Record(Base):
    """단일 데이터 레코드 (Word→JSON 변환 산출물의 정규화 저장 형태)."""

    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint(
            "data_type",
            "division",
            "team",
            "year",
            "seq",
            name="uq_records_natural_key",
        ),
        Index("idx_records_type", "data_type"),
        Index("idx_records_div_team", "division", "team"),
        Index("idx_records_year", "year"),
        Index("idx_records_agents", "agents", postgresql_using="gin"),
        Index("idx_records_tags", "tags", postgresql_using="gin"),
        Index(
            "idx_records_content",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "jsonb_path_ops"},
        ),
        Index("idx_records_classification", "classification"),
        Index("idx_records_status", "status"),
        Index("idx_records_domain", "domain"),
        Index(
            "idx_records_capabilities",
            "capabilities",
            postgresql_using="gin",
        ),
        Index(
            "idx_records_subject",
            "subject_keywords",
            postgresql_using="gin",
        ),
        Index("idx_records_parent", "parent_record_id"),
        Index("idx_records_access_pattern", "access_pattern"),
        Index(
            "idx_records_related",
            "related_record_ids",
            postgresql_using="gin",
        ),
    )

    # ---- Identity ---------------------------------------------------------
    id: Mapped[str] = mapped_column(String(80), primary_key=True)

    # ---- Classification keys ---------------------------------------------
    data_type: Mapped[str] = mapped_column(String(20), nullable=False)
    division: Mapped[str] = mapped_column(String(10), nullable=False)
    team: Mapped[str] = mapped_column(String(20), nullable=False)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    # ---- Body / metadata --------------------------------------------------
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    agents: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )

    schema_version: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="1.0"
    )
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_file: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- Attachment summary (유지: app 측 INSERT 로 갱신) -----------------
    has_attachments: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    attachment_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    # ---- Provenance ------------------------------------------------------
    author: Mapped[str] = mapped_column(String(100), nullable=False, server_default="")
    department: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=""
    )
    project: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False, server_default="1.0")

    # ---- Extended classification metadata (Migration 0006) ---------------
    classification: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="internal"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="draft"
    )
    domain: Mapped[str | None] = mapped_column(String(100), nullable=True)
    subject_keywords: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    source_system: Mapped[str | None] = mapped_column(String(50), nullable=True)
    language: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="ko"
    )
    parent_record_id: Mapped[str | None] = mapped_column(
        String(80),
        ForeignKey("records.id", ondelete="SET NULL", name="fk_records_parent"),
        nullable=True,
    )
    derivation: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="original"
    )
    capabilities: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    quality_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)

    # ---- Agent discovery hints (Migration 0007) -------------------------
    agent_hints: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_record_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    query_examples: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    access_pattern: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="occasional"
    )

    # ---- Soft delete + usage stats (Migration 0008) ---------------------
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    read_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # ---- Timestamps ------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # ---- Relationships ---------------------------------------------------
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

    # Self-referential parent/children (derived/translated/extracted docs)
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

    def __repr__(self) -> str:  # pragma: no cover - 진단용
        return f"<Record id={self.id!r} type={self.data_type!r} title={self.title[:40]!r}>"


# ---------------------------------------------------------------------------
# RecordSection
# ---------------------------------------------------------------------------
class RecordSection(Base):
    """레코드 본문 섹션 (RAG 청크 단위).

    ``embedding`` 컬럼은 마이그레이션 0004_pgvector_embeddings 에서 도입한
    ``vector(384)`` 와 정합한다. pgvector 패키지가 없으면 ``JSON`` 으로
    폴백 (SQLite 테스트). ``embedded_at`` / ``embedding_model`` 은 백필
    추적용 — 마이그레이션 0004 가 동시에 추가한다.
    """

    __tablename__ = "record_sections"
    __table_args__ = (
        UniqueConstraint("record_id", "section_id", name="uq_sections_record_section"),
        Index("idx_sections_record", "record_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("records.id", ondelete="CASCADE"),
        nullable=False,
    )
    section_id: Mapped[str] = mapped_column(String(20), nullable=False)
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    figure_refs: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    table_refs: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )

    # ---- Embedding (Migration 0004) -------------------------------------
    # PG: vector(384). SQLite (test): TEXT (conftest 가 compile 오버라이드).
    # pgvector 미설치 환경: JSON 컬럼 폴백 (list[float] 직렬화).
    if _VECTOR_AVAILABLE:
        embedding: Mapped[list[float] | None] = mapped_column(
            _Vector(384), nullable=True
        )
    else:  # pragma: no cover — pgvector 패키지 없음
        embedding: Mapped[list[float] | None] = mapped_column(
            _Vector, nullable=True
        )
    embedded_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    record: Mapped["Record"] = relationship(back_populates="sections")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RecordSection id={self.id} record={self.record_id!r} "
            f"section={self.section_id!r} level={self.level}>"
        )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
class Agent(Base):
    """에이전트 메타데이터 (Cline SR 등 외부 LLM 에이전트)."""

    __tablename__ = "agents"

    agent_type: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    common_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    data_types: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    record_links: Mapped[list["AgentRecord"]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Agent type={self.agent_type!r} name={self.name!r}>"


# ---------------------------------------------------------------------------
# AgentRecord (junction)
# ---------------------------------------------------------------------------
class AgentRecord(Base):
    """agents ↔ records N:M 매핑. priority로 정렬 가능."""

    __tablename__ = "agent_records"
    __table_args__ = (
        Index("idx_agent_records_agent", "agent_type"),
    )

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
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )

    agent: Mapped["Agent"] = relationship(back_populates="record_links")
    record: Mapped["Record"] = relationship(back_populates="agent_links")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AgentRecord agent={self.agent_type!r} record={self.record_id!r} "
            f"priority={self.priority}>"
        )


# ---------------------------------------------------------------------------
# RecordAttachment
# ---------------------------------------------------------------------------
class RecordAttachment(Base):
    """Record 에 딸린 첨부 (figure / document / spreadsheet / media / archive
    / cad / drawing / data / other).

    캡션은 필수 — 변환·인제스트 단계에서 누락된 첨부는 placeholder caption
    (``"(캡션 누락 — 검수 필요)"``) 를 채워 넣고 경고를 남긴다.

    파일 바이너리는 DB 가 아닌 ``settings.attachments_dir`` 아래 파일시스템에
    저장하며, ``file_path`` 는 그 디렉터리 기준 상대 경로다 (예:
    ``"DOC-HE-CAE-2026-000001/A001.pdf"``).
    """

    __tablename__ = "record_attachments"
    __table_args__ = (
        Index("idx_attachments_record", "record_id"),
        Index("idx_attachments_kind", "kind"),
        Index(
            "idx_attachments_extra",
            "extra",
            postgresql_using="gin",
            postgresql_ops={"extra": "jsonb_path_ops"},
        ),
    )

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
    extra: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    record: Mapped["Record"] = relationship(back_populates="attachments")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RecordAttachment id={self.id!r} kind={self.kind!r} "
            f"record={self.record_id!r}>"
        )


# ---------------------------------------------------------------------------
# ApiKey
# ---------------------------------------------------------------------------
class ApiKey(Base):
    """API 키 (SHA-256 해시 저장).

    plaintext 키는 발급 직후 한 번만 호출자에게 반환되며 DB 에는 저장되지
    않는다. 부분 인덱스 ``idx_api_keys_hash WHERE NOT revoked`` 로 활성 키
    조회를 빠르게 한다 (마이그레이션 0005).
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        Index(
            "idx_api_keys_hash",
            "key_hash",
            postgresql_where=text("NOT revoked"),
        ),
        UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    revoked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ApiKey id={self.id} name={self.name!r} revoked={self.revoked}>"


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------
class AuditLog(Base):
    """거버넌스용 감사 로그 (Migration 0008).

    각 INSERT/UPDATE/DELETE/RESTORE/ACCESS/VIEW 이벤트마다 한 행을 추가한다.
    ``field_changes`` 는 ``{field: [old, new]}`` 형태이며 INSERT 의 경우 비워두고,
    UPDATE 의 경우 변경 필드만 기록한다.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("idx_audit_record", "record_id"),
        Index("idx_audit_actor", "actor"),
        Index("idx_audit_action", "action"),
        Index("idx_audit_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    record_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    field_changes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AuditLog id={self.id} record={self.record_id!r} "
            f"action={self.action!r} actor={self.actor!r}>"
        )


__all__ = [
    "Agent",
    "AgentRecord",
    "ApiKey",
    "AuditLog",
    "Record",
    "RecordAttachment",
    "RecordSection",
]
