"""라우터에서 사용하는 Pydantic 모델.

Agent 2 의 `api.schemas.common` 모듈이 준비되면 거기서 재익스포트한다.
준비되지 않은 시점에도 라우터가 부팅되도록 폴백 정의를 제공한다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DataType = Literal["DOC", "DATA", "SIM", "CAD", "LOG", "FORM", "OTHER"]


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RecordSectionOut(_Base):
    section_id: str
    level: int
    title: str
    content_text: str = ""
    figure_refs: list[str] = Field(default_factory=list)
    table_refs: list[str] = Field(default_factory=list)


class RecordOut(_Base):
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
    schema_version: str = "1.0"
    content: dict[str, Any] = Field(default_factory=dict)
    content_hash: str | None = None
    source_file: str | None = None
    author: str = ""
    department: str = ""
    project: str | None = None
    version: str = "1.0"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RecordIn(_Base):
    """레코드 생성 요청."""

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
    schema_version: str = "1.0"
    content: dict[str, Any] = Field(default_factory=dict)
    content_hash: str | None = None
    source_file: str | None = None
    author: str = ""
    department: str = ""
    project: str | None = None
    version: str = "1.0"


class RecordPatch(_Base):
    summary: str | None = None
    tags: list[str] | None = None
    agents: list[str] | None = None
    project: str | None = None
    version: str | None = None


class RecordListResponse(_Base):
    items: list[RecordOut]
    total: int
    limit: int
    offset: int


class AgentOut(_Base):
    agent_type: str
    name: str
    description: str = ""
    common_tags: list[str] = Field(default_factory=list)
    data_types: list[str] = Field(default_factory=list)
    created_at: datetime | None = None


class AgentIn(_Base):
    agent_type: str
    name: str
    description: str = ""
    common_tags: list[str] = Field(default_factory=list)
    data_types: list[str] = Field(default_factory=list)


class AgentPatch(_Base):
    name: str | None = None
    description: str | None = None
    common_tags: list[str] | None = None
    data_types: list[str] | None = None


# Agent 2 의 정식 스키마와 호환을 유지한다.
#
# 주의: ``api.schemas.common.RecordIn`` 은 인제스트(ingest) 입력용으로
# ``team/group/year/seq`` 를 ``id`` 에서 파싱해 ORM 으로 변환한다.
# 라우터 ``POST /api/records`` 는 명시적으로 분해된 컬럼을 받는 것이
# 더 정확하므로, 라우터에서는 *이 모듈의 로컬 ``RecordIn``* 을 사용한다.
# 응답 모델(``RecordOut``)은 ORM-mode (``from_attributes=True``) 가 켜진
# 정식 스키마를 우선 사용한다.
try:  # pragma: no cover - import-time fallback
    from api.schemas.common import (  # type: ignore  # noqa: F401
        DataType as _DataType,
    )
    from api.schemas.common import RecordOut as _RecordOut  # type: ignore

    DataType = _DataType  # type: ignore[assignment]
    RecordOut = _RecordOut  # type: ignore[assignment,misc]
except ImportError:
    pass


__all__ = [
    "AgentIn",
    "AgentOut",
    "AgentPatch",
    "DataType",
    "RecordIn",
    "RecordListResponse",
    "RecordOut",
    "RecordPatch",
    "RecordSectionOut",
]
