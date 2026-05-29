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
    # 사후 부모 연결 (specimen → campaign). suggest-parent 확인 후 사용.
    parent_record_id: str | None = None


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
    # ---- Expected-schema validation (Migration 0011) ----
    required_doc_type: str | None = None
    required_tags: list[str] = Field(default_factory=list)
    excluded_tags: list[str] = Field(default_factory=list)
    # ---- RAG recipe (Migration 0014) ----
    retrieval_config: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str | None = None
    response_config: dict[str, Any] = Field(default_factory=dict)
    sample_queries: list[str] = Field(default_factory=list)
    # ---- Sample-embedding routing index (Migration 0016) ----
    # samples_indexed_count: 실제 agent_sample_embeddings 행 수.
    # samples_stale: sample_queries 길이 ≠ indexed 면 True — UI 가 경고 배지.
    samples_indexed_count: int = 0
    samples_stale: bool = False
    created_at: datetime | None = None


class AgentIn(_Base):
    agent_type: str
    name: str
    description: str = ""
    common_tags: list[str] = Field(default_factory=list)
    data_types: list[str] = Field(default_factory=list)
    # ---- Expected-schema validation (Migration 0011) ----
    required_doc_type: str | None = None
    required_tags: list[str] = Field(default_factory=list)
    excluded_tags: list[str] = Field(default_factory=list)
    # ---- RAG recipe (Migration 0014) ----
    retrieval_config: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str | None = None
    response_config: dict[str, Any] = Field(default_factory=dict)
    sample_queries: list[str] = Field(default_factory=list)


class AgentDraftIn(_Base):
    """``POST /api/agents/draft`` — LLM/휴리스틱 agent 초안 생성 요청.

    데이터 군 한정 (선택): record_ids 직접 지정, 또는 filter_tags /
    filter_data_types 로 표본을 좁힌다. 셋 다 비우면 최근 레코드 전체 표본.
    """

    record_ids: list[str] = Field(default_factory=list)
    filter_tags: list[str] = Field(default_factory=list)
    filter_data_types: list[str] = Field(default_factory=list)
    hint: str | None = None


class AgentBindMatchingIn(_Base):
    """``POST /api/agents/{agent_type}/bind-matching`` — 저장 후 매칭
    레코드 자동 바인딩 요청 (비우면 agent 의 현재 기대 스키마 사용)."""

    limit: int = Field(500, ge=1, le=5000)


class AgentPatch(_Base):
    name: str | None = None
    description: str | None = None
    common_tags: list[str] | None = None
    data_types: list[str] | None = None
    required_doc_type: str | None = None
    required_tags: list[str] | None = None
    excluded_tags: list[str] | None = None
    # ---- RAG recipe (Migration 0014) ----
    retrieval_config: dict[str, Any] | None = None
    system_prompt: str | None = None
    response_config: dict[str, Any] | None = None
    sample_queries: list[str] | None = None


# ---------------------------------------------------------------------------
# Agent history (Migration 0015) — append-only audit log
# ---------------------------------------------------------------------------
class AgentHistoryOut(_Base):
    id: int
    agent_type: str
    operation: Literal["create", "update", "delete"]
    snapshot: dict[str, Any] = Field(default_factory=dict)
    changed_by: str | None = None
    changed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Agent preview (RAG recipe dry-run) — POST /api/agents/preview
# ---------------------------------------------------------------------------
class AgentPreviewIn(_Base):
    """저장 전 RAG 레시피 미리보기 입력.

    agent_type 은 선택 — 주어지면 그 agent 의 record 만 검색 범위로 좁힌다.
    주어지지 않으면 retrieval_config.data_type_filter 만 적용.
    """

    query: str
    agent_type: str | None = None
    retrieval_config: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str | None = None
    response_config: dict[str, Any] = Field(default_factory=dict)


class AgentPreviewHit(_Base):
    record_id: str
    section_id: str
    section_title: str
    snippet: str = ""
    score: float


class AgentPreviewOut(_Base):
    query: str
    hits: list[AgentPreviewHit] = Field(default_factory=list)
    hits_above_threshold: int = 0
    threshold: float | None = None
    refused: bool = False
    refusal_message: str | None = None
    answer: str | None = None
    llm_used: bool = False
    llm_note: str | None = None


# ---------------------------------------------------------------------------
# Agent sample embeddings resync (Migration 0016)
# ---------------------------------------------------------------------------
class AgentSamplesResyncOut(_Base):
    agent_type: str
    indexed_count: int
    sample_queries: list[str] = Field(default_factory=list)


class AgentSamplesResyncAllOut(_Base):
    agents_total: int
    successes: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class AgentHistoryPruneOut(_Base):
    deleted: int
    agent_types_touched: int
    keep_last: int | None = None
    older_than_days: int | None = None


# ---------------------------------------------------------------------------
# DocType (Migration 0011) — taxonomy entries
# ---------------------------------------------------------------------------
class DocTypeOut(_Base):
    code: str
    name: str
    description: str = ""
    expected_sections: list[str] = Field(default_factory=list)
    mode: str = "llm_context"   # alembic 0026 — llm_context | data_extract | hybrid
    created_at: datetime | None = None


class DocTypeIn(_Base):
    code: str
    name: str
    description: str = ""
    expected_sections: list[str] = Field(default_factory=list)
    mode: str = "llm_context"   # 신규 등록 시 모드 명시 (생략 시 llm_context)


class DocTypePatch(_Base):
    name: str | None = None
    description: str | None = None
    expected_sections: list[str] | None = None
    mode: str | None = None     # 모드 변경 가능


# ---------------------------------------------------------------------------
# OrgTeam / OrgGroup (Migration 0012) — 조직 마스터
# ---------------------------------------------------------------------------
class OrgTeamOut(_Base):
    code: str
    name: str
    description: str = ""
    is_active: bool = True
    group_count: int = 0
    record_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OrgTeamIn(_Base):
    code: str = Field(..., pattern=r"^[A-Z][A-Z0-9]{1,9}$")
    name: str = Field(..., min_length=1, max_length=80)
    description: str = ""
    is_active: bool = True


class OrgTeamPatch(_Base):
    name: str | None = Field(None, min_length=1, max_length=80)
    description: str | None = None
    is_active: bool | None = None


class OrgGroupOut(_Base):
    team_code: str
    code: str
    name: str
    description: str = ""
    is_active: bool = True
    record_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class OrgGroupIn(_Base):
    team_code: str = Field(..., pattern=r"^[A-Z][A-Z0-9]{1,9}$")
    code: str = Field(..., pattern=r"^[A-Z][A-Z0-9]{1,19}$")
    name: str = Field(..., min_length=1, max_length=80)
    description: str = ""
    is_active: bool = True


class OrgGroupPatch(_Base):
    name: str | None = Field(None, min_length=1, max_length=80)
    description: str | None = None
    is_active: bool | None = None


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
    "AgentHistoryOut",
    "AgentHistoryPruneOut",
    "AgentIn",
    "AgentOut",
    "AgentPatch",
    "AgentPreviewHit",
    "AgentPreviewIn",
    "AgentPreviewOut",
    "AgentSamplesResyncAllOut",
    "AgentSamplesResyncOut",
    "DataType",
    "DocTypeIn",
    "DocTypeOut",
    "DocTypePatch",
    "OrgGroupIn",
    "OrgGroupOut",
    "OrgGroupPatch",
    "OrgTeamIn",
    "OrgTeamOut",
    "OrgTeamPatch",
    "RecordIn",
    "RecordListResponse",
    "RecordOut",
    "RecordPatch",
    "RecordSectionOut",
]
