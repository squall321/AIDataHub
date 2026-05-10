"""공통 Pydantic 모델.

모든 데이터 타입(DOC/DATA/SIM/CAD/LOG/FORM/OTHER) 입력에서 공유하는 ``RecordIn`` /
``RecordOut`` 을 정의한다. 변종(variant) 별 콘텐츠 스키마는 ``content`` dict 에
자유롭게 들어가며, 하위 모듈(``document.py`` 등)에서 추가 검증한다.

Migration 0006 은 ``classification``/``status``/``domain``/``subject_keywords``/
``source_system``/``language``/``parent_record_id``/``derivation``/``capabilities``/
``quality_score``/``valid_from``/``valid_until`` 컬럼을 추가하여 일반화 데이터의
슬라이스 가능성을 강화한다 — 본 모듈은 그에 대응하는 입출력 모델을 정의한다.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .id_format import DATA_TYPES, DataType, parse_id

# ---------------------------------------------------------------------------
# Literal types (분류 키)
# ---------------------------------------------------------------------------
Classification = Literal["public", "internal", "confidential", "restricted"]
Status = Literal["draft", "review", "approved", "deprecated"]
Derivation = Literal["original", "extracted", "aggregated", "translated"]
AccessPattern = Literal["frequent", "occasional", "rare"]

CLASSIFICATIONS: tuple[str, ...] = (
    "public",
    "internal",
    "confidential",
    "restricted",
)
STATUSES: tuple[str, ...] = ("draft", "review", "approved", "deprecated")
DERIVATIONS: tuple[str, ...] = (
    "original",
    "extracted",
    "aggregated",
    "translated",
)
ACCESS_PATTERNS: tuple[str, ...] = ("frequent", "occasional", "rare")
# 표준 capabilities 라벨 (compute_capabilities 와 일치)
CAPABILITY_LABELS: tuple[str, ...] = (
    "sections",
    "blocks",
    "tables",
    "figures",
    "attachments",
    "embeddings",
    "rows",
    "headers",
    "samples",
    "files",
    "components",
    "inputs",
    "outputs",
)


class RecordIn(BaseModel):
    """수집(ingest) 단계 입력 모델 — 모든 변종 공통.

    ``content`` 필드에 변종 고유 페이로드를 dict 로 보관한다.
    Pydantic v2 strict-ish 사용; 알 수 없는 필드는 무시한다.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: str
    data_type: DataType
    title: str
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    schema_version: str = "1.0"
    content: dict[str, Any] = Field(default_factory=dict)

    source_file: str | None = None
    author: str = ""
    department: str = ""
    project: str | None = None
    version: str = "1.0"

    # ---- Extended classification metadata (Migration 0006) ---------------
    classification: Classification = "internal"
    status: Status = "draft"
    domain: str | None = None
    subject_keywords: list[str] = Field(default_factory=list)
    source_system: str | None = None
    language: str = "ko"
    parent_record_id: str | None = None
    derivation: Derivation = "original"
    capabilities: list[str] = Field(default_factory=list)
    quality_score: int | None = None
    valid_from: date | None = None
    valid_until: date | None = None

    # ---- Agent discovery hints (Migration 0007) ---------------------------
    agent_hints: str | None = None
    related_record_ids: list[str] = Field(default_factory=list)
    query_examples: list[str] = Field(default_factory=list)
    access_pattern: AccessPattern = "occasional"

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        # parse_id 는 정식·레거시 모두 허용. 단순 검증 목적이므로 결과는 버린다.
        parse_id(v)
        return v

    @field_validator("data_type")
    @classmethod
    def validate_data_type(cls, v: str) -> str:
        if v not in DATA_TYPES:
            raise ValueError(f"data_type must be one of {DATA_TYPES}, got {v!r}")
        return v

    @field_validator(
        "tags",
        "agents",
        "subject_keywords",
        "capabilities",
        "related_record_ids",
        "query_examples",
    )
    @classmethod
    def _strings_only(cls, v: list[Any]) -> list[str]:
        if not isinstance(v, list):
            raise ValueError("must be a list of strings")
        out: list[str] = []
        for item in v:
            if not isinstance(item, str):
                raise ValueError(f"items must be str, got {type(item).__name__}")
            out.append(item)
        return out

    @field_validator("quality_score")
    @classmethod
    def _quality_in_range(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if not (0 <= v <= 100):
            raise ValueError("quality_score must be in [0, 100]")
        return v


class RecordOut(RecordIn):
    """DB 에서 읽어 반환되는 형태 — 분해된 ID 컴포넌트와 메타를 포함.

    SQLAlchemy ORM 객체를 직접 ``model_validate`` 로 직렬화하기 위해
    ``from_attributes=True`` 를 켠다 (Pydantic v2 ORM mode).
    """

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
        from_attributes=True,
    )

    team: str
    group: str
    year: int
    seq: int
    content_hash: str | None = None
    has_attachments: bool = False
    attachment_count: int = 0
    created_at: datetime
    updated_at: datetime


class RecordSlim(BaseModel):
    """일반화(generalized) 슬라이스용 슬림 모델 — content 미포함.

    ``GET /api/views/generalized`` 응답 등에 사용.
    """

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    id: str
    data_type: str
    team: str
    group: str
    year: int
    seq: int
    title: str
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    classification: str = "internal"
    status: str = "draft"
    domain: str | None = None
    subject_keywords: list[str] = Field(default_factory=list)
    source_system: str | None = None
    language: str = "ko"
    parent_record_id: str | None = None
    derivation: str = "original"
    capabilities: list[str] = Field(default_factory=list)
    quality_score: int | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    agent_hints: str | None = None
    related_record_ids: list[str] = Field(default_factory=list)
    query_examples: list[str] = Field(default_factory=list)
    access_pattern: str = "occasional"
    has_attachments: bool = False
    attachment_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


__all__ = [
    "ACCESS_PATTERNS",
    "AccessPattern",
    "CAPABILITY_LABELS",
    "CLASSIFICATIONS",
    "Classification",
    "DATA_TYPES",
    "DERIVATIONS",
    "DataType",
    "Derivation",
    "RecordIn",
    "RecordOut",
    "RecordSlim",
    "STATUSES",
    "Status",
]
