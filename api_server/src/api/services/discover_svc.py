"""Discovery / RAG-friendly API 서비스 로직.

Endpoints (Agent 30 — `/api/discover`, `/api/schema`, `/api/hints`,
`/api/docs/llm.txt`, `/api/docs/agent-guide`, `/api/ask`) 가 공통으로 사용하는
비즈니스 로직.

- ``build_discover_payload``  : `/api/discover` 응답 생성 (60초 TTL 캐시).
- ``build_json_schema``       : `/api/schema` 응답 (Record JSON Schema, 정적).
- ``build_hints``             : `/api/hints` 응답 (정적 카탈로그).
- ``build_llm_doc``           : `/api/docs/llm.txt` 응답 (정적 마크다운).
- ``load_agent_guide``        : `/api/docs/agent-guide?size=...` 응답
  (모델 사이즈별 친화 가이드, 디스크에서 즉시 로드).
- ``interpret_query``         : `/api/ask` 의 자연어 → 필터 변환
  (LLM 옵셔널, 키워드 폴백 항상 동작).
- ``execute_ask``             : 해석된 필터로 ``Record`` 검색 실행.

캐시 전략:
    in-process dict + monotonic TTL. 여러 워커가 떠도 자체 캐시이므로
    데이터 변경 직후 stale 응답이 나올 수 있지만 60초 안엔 일관 — 의도된
    트레이드오프 (B6 요구사항).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Agent, Record
from api.schemas.common import (
    ACCESS_PATTERNS,
    CAPABILITY_LABELS,
    CLASSIFICATIONS,
    DERIVATIONS,
    STATUSES,
)
from api.schemas.id_format import DATA_TYPES

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 데이터 타입 / 분류 메타 — 사람/LLM 이 읽을 수 있는 한글 설명
# ---------------------------------------------------------------------------
DATA_TYPE_DESCRIPTIONS: dict[str, str] = {
    "DOC": (
        "문서·매뉴얼·보고서 — sections + blocks 계층 구조. "
        "지원 source_format: docx · pdf · pptx · md"
    ),
    "DATA": "측정·시험 표 데이터 — headers + rows",
    "SIM": "시뮬레이션 입력·출력 — solver + inputs/outputs",
    "CAD": "CAD 모델 메타 — cad_type + file_format + components",
    "LOG": "로그·시계열",
    "FORM": "양식·체크리스트",
    "OTHER": "기타 / 분류되지 않은 일반 레코드",
}

CONTENT_SHAPE_HINTS: dict[str, dict[str, Any]] = {
    "DOC": {
        "required": ["meta", "sections"],
        "optional": ["toc", "figures", "tables", "sources", "attachments"],
        "shape": "{ meta:{}, toc:[{id,level,title}], "
        "sections:[{id,level,title,blocks:[{type,text}],children:[...]}] }",
    },
    "DATA": {
        "required": ["headers", "rows"],
        "optional": ["caption", "units", "notes"],
        "shape": "{ caption, headers:[str], rows:[[any]], units:{col:unit}, notes }",
    },
    "SIM": {
        "required": ["solver", "inputs"],
        "optional": ["solver_version", "outputs", "runtime"],
        "shape": "{ solver, solver_version, inputs:{}, outputs:{}, runtime:{} }",
    },
    "CAD": {
        "required": ["cad_type", "file_format"],
        "optional": ["file_metadata", "components"],
        "shape": "{ cad_type, file_format, file_metadata:{}, components:[] }",
    },
    "LOG": {"required": [], "optional": [], "shape": "free-form"},
    "FORM": {"required": [], "optional": [], "shape": "free-form"},
    "OTHER": {"required": [], "optional": [], "shape": "free-form"},
}

LANGUAGES: tuple[str, ...] = ("ko", "en", "mixed")


# ---------------------------------------------------------------------------
# In-process TTL cache (단일 워커 가정)
# ---------------------------------------------------------------------------
_DISCOVER_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DISCOVER_TTL_SECONDS: float = 60.0


def _cache_get(key: str) -> dict[str, Any] | None:
    entry = _DISCOVER_CACHE.get(key)
    if entry is None:
        return None
    expires_at, payload = entry
    if time.monotonic() > expires_at:
        _DISCOVER_CACHE.pop(key, None)
        return None
    return payload


def _cache_set(key: str, payload: dict[str, Any]) -> None:
    _DISCOVER_CACHE[key] = (time.monotonic() + _DISCOVER_TTL_SECONDS, payload)


def clear_cache() -> None:
    """테스트용 — 캐시 전체 비우기."""
    _DISCOVER_CACHE.clear()


# ---------------------------------------------------------------------------
# /api/discover
# ---------------------------------------------------------------------------
async def build_discover_payload(
    session: AsyncSession,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """전체 카탈로그 응답.

    카운트 집계가 무거울 수 있으므로 60초 in-memory 캐시.
    """
    if use_cache:
        cached = _cache_get("discover")
        if cached is not None:
            return cached

    total_q = await session.execute(select(func.count()).select_from(Record))
    total_records = int(total_q.scalar_one() or 0)

    by_type: dict[str, int] = {}
    rows = (
        await session.execute(
            select(Record.data_type, func.count()).group_by(Record.data_type)
        )
    ).all()
    for k, v in rows:
        if k:
            by_type[str(k)] = int(v)

    by_division: dict[str, int] = {}
    rows = (
        await session.execute(
            select(Record.division, func.count()).group_by(Record.division)
        )
    ).all()
    for k, v in rows:
        if k:
            by_division[str(k)] = int(v)

    by_classification: dict[str, int] = {}
    rows = (
        await session.execute(
            select(Record.classification, func.count()).group_by(Record.classification)
        )
    ).all()
    for k, v in rows:
        if k:
            by_classification[str(k)] = int(v)

    # ---- agents (with record counts + sample tags) ----------------------
    agents_payload: list[dict[str, Any]] = []
    agent_rows = (await session.execute(select(Agent))).scalars().all()
    for ag in agent_rows:
        # record count for this agent (fast: scan records.agents array)
        # Use python-side count to stay dialect-agnostic.
        rec_count_stmt = select(func.count()).select_from(Record).where(
            Record.agents.isnot(None)
        )
        # We want records whose agents array contains ag.agent_type.
        # Dialect-agnostic: fetch IDs (might be expensive on huge DBs but
        # discover is cached).
        all_records = (
            (await session.execute(select(Record.agents)))
            .scalars()
            .all()
        )
        cnt = sum(1 for arr in all_records if arr and ag.agent_type in list(arr))
        agents_payload.append(
            {
                "agent_type": ag.agent_type,
                "name": ag.name,
                "description": ag.description or "",
                "record_count": cnt,
                "common_tags": list(ag.common_tags or []),
                "data_types": list(ag.data_types or []),
                "sample_query": f"/api/data?agent={ag.agent_type}",
            }
        )

    payload: dict[str, Any] = {
        "version": "1.0",
        "title": "AI Data Hub",
        "description": "사업부 문서·데이터 통합 허브 (DOC/DATA/SIM/CAD/LOG/FORM/OTHER)",
        "total_records": total_records,
        "by_data_type": by_type,
        "by_division": by_division,
        "by_classification": by_classification,
        "agents": agents_payload,
        "data_types_explained": DATA_TYPE_DESCRIPTIONS,
        "starting_points": [
            "GET /api/agents — 등록된 에이전트 목록",
            "GET /api/views/hierarchical — 계층 문서만 (DOC sections+blocks)",
            "GET /api/records?capabilities=tables — 표 가진 record",
            "GET /api/records?capabilities=sections&classification=approved",
            "POST /api/ask — 자연어 쿼리로 검색",
            "POST /api/groups/auto — 자연어 → 의미 그룹 자동 클러스터링",
            "GET /api/records/{id}/cluster — 한 record 의 의미 그룹",
            "POST /api/records/bulk — 여러 record id 한 번에 조회",
            "GET /api/schema — 머신 리더블 JSON Schema",
            "GET /api/hints — 에이전트용 자연어 힌트",
            "GET /api/docs/llm.txt — LLM 한 번에 읽을 통합 문서",
            "GET /api/docs/agent-guide?size={tiny|small|medium|large} — 모델 사이즈별 친화 가이드",
        ],
        "schema_url": "/api/schema",
        "hints_url": "/api/hints",
        "llm_doc_url": "/api/docs/llm.txt",
        "ask_url": "/api/ask",
        "agent_guides": {
            **AGENT_GUIDE_INDEX,
            "default": "/api/docs/agent-guide",
            "description": AGENT_GUIDES_DESCRIPTION,
        },
        "taxonomy_endpoints": {
            "tags": "/api/taxonomy/tags",
            "data_types": "/api/taxonomy/data-types",
            "domains": "/api/taxonomy/domains",
            "agents": "/api/taxonomy/agents",
            "tag_resolver": "/api/taxonomy/tags/resolve?q=...",
            "enums": (
                "/api/taxonomy/classification | "
                "/api/taxonomy/status | "
                "/api/taxonomy/access-pattern"
            ),
        },
        # ----- Semantic Groups (의미 그룹) — 같은 의미의 record 군 묶음 -----
        "semantic_groups": {
            "auto": (
                "/api/groups/auto (POST, body={q, n_groups, "
                "limit_per_group, min_score})"
            ),
            "from_record": "/api/records/{id}/cluster?mode=semantic|tag|hybrid",
            "bulk_fetch": (
                "/api/records/bulk (POST, body={ids:[...], include_sections})"
            ),
            "description": (
                "embedding cosine + tag jaccard 으로 record 군을 묶어 "
                "작은 AI 가 한 번에 가져갈 수 있게 한다."
            ),
        },
        # ----- DATA 타입 전용 엔드포인트 (작은 AI 가 일반화 데이터 분석) -----
        "data_endpoints": {
            "catalog": "/api/data?tags=...&domain=...&min_rows=...",
            "rows": "/api/data/{id}/rows?limit=...&offset=...&where=Region:Yield",
            "columns": "/api/data/{id}/columns",
            "aggregate": "/api/data/{id}/aggregate?op=avg&column=Stress&group_by=Region",
            "description": (
                "DATA 타입 record (Excel→JSON 변환 결과 등) 의 행/컬럼 정의/"
                "집계 통계를 작은 AI 가 직접 평균·최대·최소 계산 안 해도 되게 "
                "노출한다. op ∈ {avg,max,min,sum,count}."
            ),
        },
        # ----- 다층 필터링 (Faceted Search) ----------------------------
        "faceted_search": {
            "url": "/api/search/faceted?q=...&data_type=DOC,DATA&tags=...",
            "by_tags": "/api/search/by-tags?tags=IGA,NURBS&match=all",
            "description": (
                "다축 필터 (data_type/tags/agent/domain/classification/status/"
                "year_from/year_to/min_quality) 가 AND 로 결합되며, 응답의 "
                "facets 가 다음 query 를 어떻게 좁힐지 안내한다."
            ),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    _cache_set("discover", payload)
    return payload


# ---------------------------------------------------------------------------
# /api/schema  — JSON Schema (draft-2020-12)
# ---------------------------------------------------------------------------
def build_json_schema() -> dict[str, Any]:
    """``Record`` 의 JSON Schema 표현.

    - draft-2020-12 사양
    - enum 은 코드의 단일 정의(``DATA_TYPES`` 등) 와 sync
    - data_type 별 ``content`` 오브젝트는 oneOf 로 표현
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://ai-data-hub/api/schema",
        "title": "AI Data Hub — Record",
        "description": (
            "단일 데이터 레코드 (Word/Excel/PDF/Markdown/PowerPoint → JSON → DB). "
            "data_type 별로 content 페이로드 모양이 달라진다."
        ),
        "type": "object",
        "required": ["id", "data_type", "title"],
        "properties": {
            "id": {
                "type": "string",
                "pattern": (
                    "^(DOC|DATA|SIM|CAD|LOG|FORM|OTHER)-"
                    "[A-Z]{2,4}-[A-Z]{2,5}-20[2-9][0-9]-[0-9]{6}$"
                ),
                "description": "레코드 ID (예: DOC-HE-CAE-2026-000001).",
            },
            "data_type": {
                "type": "string",
                "enum": list(DATA_TYPES),
                "description": "콘텐츠 변종.",
            },
            "division": {"type": "string", "description": "사업부 (HE/EV/PT/...)"},
            "team": {"type": "string", "description": "팀 (CAE/MFG/...)"},
            "year": {"type": "integer", "minimum": 2020, "maximum": 2099},
            "seq": {"type": "integer", "minimum": 1, "maximum": 999_999},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "agents": {
                "type": "array",
                "items": {"type": "string"},
                "description": "이 레코드를 사용하는 agent_type 배열.",
            },
            "schema_version": {"type": "string", "default": "1.0"},
            "content": {
                "type": "object",
                "description": "data_type 별 페이로드. 아래 oneOf 로 분기.",
            },
            "source_file": {"type": ["string", "null"]},
            "author": {"type": "string"},
            "department": {"type": "string"},
            "project": {"type": ["string", "null"]},
            "version": {"type": "string", "default": "1.0"},
            "classification": {
                "type": "string",
                "enum": list(CLASSIFICATIONS),
                "default": "internal",
            },
            "status": {
                "type": "string",
                "enum": list(STATUSES),
                "default": "draft",
            },
            "domain": {"type": ["string", "null"]},
            "subject_keywords": {"type": "array", "items": {"type": "string"}},
            "source_system": {"type": ["string", "null"]},
            "language": {
                "type": "string",
                "enum": list(LANGUAGES),
                "default": "ko",
            },
            "parent_record_id": {"type": ["string", "null"]},
            "derivation": {
                "type": "string",
                "enum": list(DERIVATIONS),
                "default": "original",
            },
            "capabilities": {
                "type": "array",
                "items": {"type": "string", "enum": list(CAPABILITY_LABELS)},
                "description": "INSERT 시 content 모양에서 자동 산출.",
            },
            "quality_score": {
                "type": ["integer", "null"],
                "minimum": 0,
                "maximum": 100,
            },
            "valid_from": {"type": ["string", "null"], "format": "date"},
            "valid_until": {"type": ["string", "null"], "format": "date"},
            "agent_hints": {
                "type": ["string", "null"],
                "description": "에이전트가 이 record 를 어떻게 다뤄야 하는지 사람이 작성한 힌트.",
            },
            "related_record_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "수동 큐레이션된 관계 record id 목록.",
            },
            "query_examples": {
                "type": "array",
                "items": {"type": "string"},
                "description": "이 record 를 다루기 위한 자연어 쿼리 예시.",
            },
            "access_pattern": {
                "type": "string",
                "enum": list(ACCESS_PATTERNS),
                "default": "occasional",
            },
            "has_attachments": {"type": "boolean"},
            "attachment_count": {"type": "integer", "minimum": 0},
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
        },
        "oneOf": [
            {
                "title": "DOC content",
                "properties": {
                    "data_type": {"const": "DOC"},
                    "content": {
                        "type": "object",
                        "required": ["sections"],
                        "properties": {
                            "meta": {"type": "object"},
                            "toc": {"type": "array"},
                            "sections": {
                                "type": "array",
                                "description": "{id,level,title,blocks,children} 트리.",
                            },
                            "figures": {"type": "array"},
                            "tables": {"type": "array"},
                            "sources": {"type": "array"},
                            "attachments": {"type": "array"},
                        },
                    },
                },
            },
            {
                "title": "DATA content",
                "properties": {
                    "data_type": {"const": "DATA"},
                    "content": {
                        "type": "object",
                        "required": ["headers", "rows"],
                        "properties": {
                            "caption": {"type": "string"},
                            "headers": {"type": "array", "items": {"type": "string"}},
                            "rows": {"type": "array"},
                            "units": {"type": ["object", "null"]},
                            "notes": {"type": "string"},
                        },
                    },
                },
            },
            {
                "title": "SIM content",
                "properties": {
                    "data_type": {"const": "SIM"},
                    "content": {
                        "type": "object",
                        "required": ["solver", "inputs"],
                        "properties": {
                            "solver": {"type": "string"},
                            "solver_version": {"type": ["string", "null"]},
                            "inputs": {"type": "object"},
                            "outputs": {"type": "object"},
                            "runtime": {"type": ["object", "null"]},
                        },
                    },
                },
            },
            {
                "title": "CAD content",
                "properties": {
                    "data_type": {"const": "CAD"},
                    "content": {
                        "type": "object",
                        "required": ["cad_type", "file_format"],
                        "properties": {
                            "cad_type": {"type": "string"},
                            "file_format": {"type": "string"},
                            "file_metadata": {"type": "object"},
                            "components": {"type": "array"},
                        },
                    },
                },
            },
            {
                "title": "LOG/FORM/OTHER (free-form)",
                "properties": {
                    "data_type": {
                        "type": "string",
                        "enum": ["LOG", "FORM", "OTHER"],
                    },
                    "content": {"type": "object"},
                },
            },
        ],
        "examples": [
            {
                "id": "DOC-HE-CAE-2026-000001",
                "data_type": "DOC",
                "title": "IGA 가이드",
                "agents": ["iga-analyst"],
                "content": {
                    "meta": {"doc_id": "HE-CAE-2026-000001"},
                    "sections": [
                        {"id": "1", "level": 1, "title": "개요", "blocks": []}
                    ],
                },
            },
            {
                "id": "DATA-HE-CAE-2026-000002",
                "data_type": "DATA",
                "title": "배터리 셀 측정 데이터",
                "content": {
                    "headers": ["time", "force"],
                    "rows": [[0.0, 0.0], [0.1, 12.5]],
                },
            },
        ],
        "x-relationships": {
            "parent_record_id": "self-FK to records.id (derived/translated/extracted documents)",
            "agents": "agents[] ↔ agent_records (many-to-many junction with priority)",
            "attachments": "GET /api/records/{id}/attachments",
            "sections": "GET /api/records/{id}/sections (DOC only)",
            "related_record_ids": "manual curation of cross-record relations",
        },
    }


# ---------------------------------------------------------------------------
# /api/hints
# ---------------------------------------------------------------------------
_ALL_HINTS: dict[str, list[dict[str, str]]] = {
    "getting_started": [
        {
            "hint": (
                "AI 에이전트라면 가장 먼저 GET /api/discover 를 호출해 "
                "허브 전체 카탈로그를 받아라."
            ),
            "sample_endpoint": "GET /api/discover",
            "why_useful": (
                "data_type, division, classification 분포 + 에이전트 + "
                "starting_points 가 한 번에 들어온다."
            ),
        },
        {
            "hint": "JSON Schema 가 필요하면 /api/schema 를 받아라.",
            "sample_endpoint": "GET /api/schema",
            "why_useful": "필드명/enum/오브젝트 모양을 코드 없이 알 수 있다.",
        },
        {
            "hint": "한 번에 통합 문서가 필요하면 /api/docs/llm.txt 를 컨텍스트에 넣어라.",
            "sample_endpoint": "GET /api/docs/llm.txt",
            "why_useful": "5-10KB 마크다운으로 API 전체를 압축 설명한다.",
        },
    ],
    "searching": [
        {
            "hint": "키워드로 본문/요약 검색은 /api/search?mode=fts 사용.",
            "sample_endpoint": "GET /api/search?mode=fts&q=offset",
            "why_useful": "section.content_text + record.summary/title 모두 매칭.",
        },
        {
            "hint": "태그 AND 검색은 /api/search?mode=tag&tags=...",
            "sample_endpoint": "GET /api/search?mode=tag&tags=IGA&tags=LS-DYNA",
            "why_useful": "정확한 키 큐레이션이 필요할 때.",
        },
        {
            "hint": "자연어가 편하면 POST /api/ask 로 던져라.",
            "sample_endpoint": "POST /api/ask {\"query\":\"최근 IGA 시뮬\"}",
            "why_useful": "interpreted_query + results + follow-up 까지 한 번에.",
        },
    ],
    "filtering_by_agent": [
        {
            "hint": "agent 가 사용 가능한 레코드만 필터하려면 /api/data?agent=...",
            "sample_endpoint": "GET /api/data?agent=iga-analyst&query=offset&limit=5",
            "why_useful": "agent_records junction + relevance score 정렬.",
        },
        {
            "hint": "agent 메타만 보려면 /api/agents/{agent_type}.",
            "sample_endpoint": "GET /api/agents/iga-analyst",
            "why_useful": "common_tags / data_types 가 함께 온다.",
        },
    ],
    "tabular_data": [
        {
            "hint": "표 가진 record 만 보려면 capabilities=tables 또는 data_type=DATA.",
            "sample_endpoint": "GET /api/records?capabilities=tables",
            "why_useful": "DATA 변종 + DOC 안의 tables 모두 후보.",
        },
        {
            "hint": "특정 record 의 표만 추출은 /api/records/{id}/tables.",
            "sample_endpoint": "GET /api/records/DATA-HE-CAE-2026-000002/tables",
            "why_useful": "headers/rows 만 잘라서 받는다.",
        },
    ],
    "time_bounded": [
        {
            "hint": "year 슬라이스는 ?year=2026.",
            "sample_endpoint": "GET /api/records?year=2026",
            "why_useful": "연도별 reporting 에 가장 빠르다.",
        },
        {
            "hint": "분기/월간 통계는 /api/analytics/timeline?year=2026.",
            "sample_endpoint": "GET /api/analytics/timeline?year=2026",
            "why_useful": "월별 카운트 12행 한 번에.",
        },
    ],
    "attachments": [
        {
            "hint": "첨부 메타 조회는 /api/records/{id}/attachments.",
            "sample_endpoint": "GET /api/records/DOC-HE-CAE-2026-000001/attachments",
            "why_useful": "kind/caption/file_path 메타. 바이너리는 /attachments/{...}.",
        },
        {
            "hint": "첨부 가진 record 만 필터: ?has_attachments=true (또는 capabilities=attachments).",
            "sample_endpoint": "GET /api/records?capabilities=attachments",
            "why_useful": "PDF/CAD 파일이 딸린 record 만 좁힐 때.",
        },
    ],
    "cross_record_relations": [
        {
            "hint": "수동 큐레이션 관계는 record.related_record_ids.",
            "sample_endpoint": "GET /api/records/{id}",
            "why_useful": "큐레이터가 명시한 강한 관계 (graph traversal).",
        },
        {
            "hint": "파생/번역/추출 관계는 parent_record_id (self-FK).",
            "sample_endpoint": "GET /api/records?parent_record_id=DOC-HE-CAE-2026-000001",
            "why_useful": "원본 → 파생 트리.",
        },
        {
            "hint": "동일 태그/agent 교집합은 /api/analytics/cross-agent.",
            "sample_endpoint": "GET /api/analytics/cross-agent?agents=iga-analyst&agents=cae-reporter",
            "why_useful": "두 agent 가 모두 사용하는 record 집합.",
        },
    ],
}


def build_hints(context: str | None = None) -> list[dict[str, str]]:
    """주어진 컨텍스트의 힌트만 또는 전체.

    알 수 없는 컨텍스트면 빈 리스트.
    """
    if context is None or not context.strip():
        flat: list[dict[str, str]] = []
        for ctx, items in _ALL_HINTS.items():
            for item in items:
                flat.append({**item, "context": ctx})
        return flat
    items = _ALL_HINTS.get(context.strip().lower())
    if items is None:
        return []
    return [{**item, "context": context.strip().lower()} for item in items]


def list_hint_contexts() -> list[str]:
    return list(_ALL_HINTS.keys())


# ---------------------------------------------------------------------------
# /api/docs/llm.txt
# ---------------------------------------------------------------------------
_LLM_DOC_TEMPLATE = """# AI Data Hub — LLM Quick Reference

> 이 한 페이지가 허브 전체를 설명한다. 백엔드 source 를 읽지 마라.
> 가장 먼저 `GET /api/discover` 를 호출해라.

## 1. What is this hub
사업부 문서 / 측정 / 시뮬레이션 / CAD / 로그 / 양식을 통합한 데이터 허브.
모든 record 는 단일 PostgreSQL 테이블 (`records`) + 변종별 content JSONB 로
저장된다. REST API 와 MCP 도구 두 가지 채널로 노출.

## 2. Core concepts
- **record**: 최상위 단위. id 가 사람이 읽을 수 있는 코드.
- **section**: DOC 변종에서 본문 청크 (RAG 단위). `record_sections` 테이블.
- **attachment**: figure / pdf / cad 등 파일 첨부 (caption 의무).
- **agent**: LLM 에이전트 메타 (iga-analyst 등). agents[] 배열로 record 와 N:M.
- **capabilities**: content 모양 라벨 (sections/blocks/tables/figures/...).

## 3. ID format
`{DATA_TYPE}-{DIVISION}-{TEAM}-{YEAR}-{SEQ:06d}` — 예: `DOC-HE-CAE-2026-000001`.
- DATA_TYPE ∈ {DOC, DATA, SIM, CAD, LOG, FORM, OTHER}
- DIVISION 2-4자, TEAM 2-5자, YEAR 2020-2099, SEQ 6자리.
- 레거시 (접두사 누락) 도 ingest 단계에서 자동 보강.

## 4. Key endpoints (one-line each)
- `GET /api/discover` — 카탈로그 (count + agents + starting_points + URL 모음).
- `GET /api/schema` — JSON Schema (draft-2020-12).
- `GET /api/hints?context=getting_started` — 자연어 힌트.
- `GET /api/docs/llm.txt` — 이 문서.
- `POST /api/ask` — 자연어 쿼리 → interpreted_query + results.
- `GET /api/records` — 필터 (data_type/division/year/agent/tag/capabilities/q).
- `GET /api/records/{id}` — 단일 record 상세.
- `GET /api/records/{id}/sections` / `/tables` / `/figures` / `/attachments`.
- `GET /api/data?agent=...&query=...` — 에이전트 시점 검색 (Cline SR 코어).
- `GET /api/search?mode=fts|tag|semantic` — 일반 검색.
- `GET /api/agents` / `/api/agents/{type}` / `/api/agents/{type}/records`.
- `GET /api/analytics/distribution|common-tags|cross-agent|timeline`.
- `GET /api/views/hierarchical|tabular|generalized` — 모양별 슬라이스.

## 5. Common query patterns
1. *최근 IGA 시뮬 결과 5건*:
   `GET /api/data?agent=iga-analyst&data_types=SIM&limit=5`
2. *2026년 HE-CAE 표 데이터*:
   `GET /api/records?division=HE&team=CAE&year=2026&capabilities=tables`
3. *quality_score >= 80 인 approved 문서*:
   `GET /api/records?status=approved` 후 quality_score 클라이언트 필터,
   또는 `POST /api/ask {"query":"품질 80 이상 approved"}`.
4. *tag IGA + tag offset 모두*:
   `GET /api/search?mode=tag&tags=IGA&tags=offset`.
5. *iga-analyst 와 cae-reporter 둘 다 사용하는 record*:
   `GET /api/analytics/cross-agent?agents=iga-analyst&agents=cae-reporter`.
6. *DOC 안의 특정 섹션 본문*:
   `GET /api/records/{id}/sections` → 원하는 section_id 의 content_text.
7. *자연어로 한 번에*:
   `POST /api/ask` body `{"query":"최근 일주일 IGA NURBS","limit":5}`.

## 6. data_type → content shape map
- **DOC**: `{ meta:{}, toc:[{id,level,title}], sections:[{id,level,title,blocks,children}], figures, tables, sources, attachments }`
- **DATA**: `{ caption, headers:[str], rows:[[any]], units:{col:unit}, notes }`
- **SIM**: `{ solver, solver_version, inputs:{}, outputs:{}, runtime:{} }`
- **CAD**: `{ cad_type, file_format, file_metadata:{}, components:[] }`
- **LOG / FORM / OTHER**: free-form (raw JSON 그대로 보관).

## 7. How to start (AI agent recipe)
```
1. discover  : GET /api/discover                # 전체 지도
2. narrow    : POST /api/ask {"query":"..."} or GET /api/records?...
3. detail    : GET /api/records/{id}             # 한 건 풀
4. traverse  : record.related_record_ids / parent_record_id 로 그래프 이동
5. sections  : GET /api/records/{id}/sections    # DOC 본문 청크
6. attach    : GET /api/records/{id}/attachments # 첨부 메타
```

## 8. Discovery contract
모든 enum/필드/data_type 모양은 `/api/schema` 한 곳에서 받아라.
변경되면 `/api/discover.version` 도 함께 올린다 (현재 `1.0`).
LLM 컨텍스트에는 `/api/docs/llm.txt` + `/api/schema` 두 개만 넣으면 충분하다.
"""

_LLM_DOC_SIZE_GUIDE_TAIL = """

## Size-specific agent guides (NEW)

For your model size, fetch the matching guide:

- **TINY (1B-3B)**: GET /api/docs/agent-guide?size=tiny  (cheatsheet, ~1500 words)
- **SMALL (3B-7B)**: GET /api/docs/agent-guide?size=small  (tables, ~4000 words)
- **MEDIUM (13B-70B)**: GET /api/docs/agent-guide?size=medium  (decision+fallback, ~6000 words)
- **LARGE (frontier)**: GET /api/docs/agent-guide?size=large  (rationale + trust eval, ~10000 words)

Default (no size param): SMALL.
"""


def build_llm_doc() -> str:
    """LLM 한 번에 읽을 통합 문서 (마크다운).

    응답 끝에 모델 사이즈별 가이드 (``/api/docs/agent-guide?size=...``) 안내
    단락을 함께 노출한다.
    """
    return _LLM_DOC_TEMPLATE + _LLM_DOC_SIZE_GUIDE_TAIL


# ---------------------------------------------------------------------------
# /api/docs/agent-guide  — 모델 사이즈별 친화 가이드
# ---------------------------------------------------------------------------
AGENT_GUIDE_SIZES: tuple[str, ...] = ("tiny", "small", "medium", "large")
DEFAULT_AGENT_GUIDE_SIZE: str = "small"

# 가이드 마크다운 파일이 위치한 디렉터리.
# ``api_server/src/api/services/discover_svc.py`` 기준 → ``api_server/docs``.
_AGENT_GUIDE_DIR: Path = (
    Path(__file__).resolve().parents[3] / "docs"
)

AGENT_GUIDE_INDEX: dict[str, str] = {
    size: f"/api/docs/agent-guide?size={size}" for size in AGENT_GUIDE_SIZES
}

AGENT_GUIDES_DESCRIPTION: str = (
    "Model-size-specific API guides. "
    "Tiny (1B-3B): cheatsheet format. "
    "Small (3B-7B): tables + named flows. "
    "Medium (13B-70B): decision+fallback ladder. "
    "Large (frontier): design rationale + trust eval."
)


def agent_guide_path(size: str) -> Path:
    """``size`` 에 대응하는 마크다운 파일 경로를 반환한다.

    ``size`` 가 4종 (``tiny|small|medium|large``) 이외면 ``ValueError``.
    """
    key = (size or "").strip().lower()
    if key not in AGENT_GUIDE_SIZES:
        raise ValueError(
            f"unsupported size {size!r} (expected one of {AGENT_GUIDE_SIZES})"
        )
    return _AGENT_GUIDE_DIR / f"AGENT_API_GUIDE_{key.upper()}.md"


def load_agent_guide(size: str) -> str:
    """``size`` 가이드 마크다운 본문을 로드한다.

    파일 부재 시 ``FileNotFoundError`` 를 그대로 던진다 — 라우터에서 500 으로
    매핑한다. 디스크 IO 는 캐시하지 않는다 (4 파일, 각 ~수 KB).
    """
    path = agent_guide_path(size)
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# POST /api/ask — 자연어 → 필터
# ---------------------------------------------------------------------------
# 키워드 → agent_type / data_type / 기타 매핑
_AGENT_KEYWORDS: dict[str, list[str]] = {
    "iga-analyst": ["iga", "isogeometric", "nurbs"],
    "cae-reporter": ["cae", "보고서", "report"],
    "material-reviewer": ["material", "재료", "물성"],
    "process-checker": ["process", "공정", "절차"],
    "code-assistant": ["code", "코드", "스크립트"],
}

_DATA_TYPE_KEYWORDS: dict[str, list[str]] = {
    "DOC": ["문서", "보고서", "가이드", "doc", "document", "manual"],
    "DATA": ["데이터", "표", "측정", "data", "table", "raw"],
    "SIM": ["시뮬", "simulation", "sim", "해석", "lsdyna", "ls-dyna", "abaqus"],
    "CAD": ["cad", "step", "stp", "모델", "도면"],
    "LOG": ["log", "로그", "시계열"],
    "FORM": ["양식", "form", "체크리스트"],
}

_CAPABILITY_KEYWORDS: dict[str, list[str]] = {
    "tables": ["표", "테이블", "table"],
    "figures": ["그림", "figure", "이미지"],
    "attachments": ["첨부", "attachment"],
    "sections": ["섹션", "section", "본문"],
}

_RECENT_RE = re.compile(
    r"(?:최근|recent|지난)\s*(\d+)?\s*(일|주|개월|달|month|week|day)?",
    re.IGNORECASE,
)
_QUALITY_RE = re.compile(
    r"(?:quality(?:_score)?|품질)\s*(?:>=|>|이상|over|at\s*least)?\s*(\d{1,3})",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(20[2-9][0-9])\b")


def _detect_recent_window(query: str) -> str | None:
    """``최근 1주일`` 등에서 created_at_gte ISO date 를 산출한다."""
    m = _RECENT_RE.search(query)
    if not m:
        return None
    n = int(m.group(1)) if m.group(1) else 7
    unit = (m.group(2) or "").lower()
    if unit in ("주", "week"):
        days = n * 7
    elif unit in ("개월", "달", "month"):
        days = n * 30
    elif unit in ("일", "day", ""):
        days = n
    else:
        days = n
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.date().isoformat()


def _interpret_keywords(query: str) -> dict[str, Any]:
    """LLM 없이 키워드 룩업으로 필터를 추정한다."""
    q = query.lower()
    filters: dict[str, Any] = {}
    explanation_parts: list[str] = []

    # agent
    detected_agent: str | None = None
    for agent_type, keys in _AGENT_KEYWORDS.items():
        if any(k in q for k in keys):
            detected_agent = agent_type
            break
    if detected_agent:
        filters["agent"] = detected_agent
        explanation_parts.append(f"키워드로 agent={detected_agent} 추정")

    # data_type
    detected_dt: str | None = None
    for dt, keys in _DATA_TYPE_KEYWORDS.items():
        if any(k in q for k in keys):
            detected_dt = dt
            break
    if detected_dt:
        filters["data_type"] = detected_dt
        explanation_parts.append(f"키워드로 data_type={detected_dt}")

    # capabilities
    caps: list[str] = []
    for cap, keys in _CAPABILITY_KEYWORDS.items():
        if any(k in q for k in keys):
            caps.append(cap)
    if caps:
        filters["capabilities"] = caps
        explanation_parts.append(f"capabilities={caps}")

    # recent / created_at
    recent = _detect_recent_window(query)
    if recent:
        filters["created_at_gte"] = recent
        explanation_parts.append(f"최근 {recent} 이후")

    # quality_score
    qm = _QUALITY_RE.search(query)
    if qm:
        try:
            qv = int(qm.group(1))
            if 0 <= qv <= 100:
                filters["quality_score_gte"] = qv
                explanation_parts.append(f"quality_score>={qv}")
        except ValueError:
            pass

    # year
    ym = _YEAR_RE.search(query)
    if ym:
        filters["year"] = int(ym.group(1))
        explanation_parts.append(f"year={ym.group(1)}")

    # status
    for status_val in STATUSES:
        if status_val in q:
            filters["status"] = status_val
            explanation_parts.append(f"status={status_val}")
            break

    # classification
    for cls in CLASSIFICATIONS:
        if cls in q:
            filters["classification"] = cls
            explanation_parts.append(f"classification={cls}")
            break

    return {
        "filters": filters,
        "explanation": (
            "; ".join(explanation_parts) if explanation_parts else "키워드 매칭 없음 — 전체 검색"
        ),
    }


async def _interpret_with_llm(query: str) -> dict[str, Any] | None:
    """OPENAI_API_KEY 가 있으면 LLM 으로 해석. 실패 시 None.

    반환: ``{"filters": {...}, "explanation": "..."}`` 또는 None.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        # openai 패키지가 없을 수도 — 부드럽게 폴백.
        from openai import AsyncOpenAI  # type: ignore
    except ImportError:
        log.info("openai package not installed — LLM ask fallback to keyword")
        return None

    system_prompt = (
        "You translate Korean/English natural-language search queries about an "
        "industrial data hub into a structured JSON filter object.\n"
        "Available filter fields:\n"
        f"  - agent (one of: {', '.join(_AGENT_KEYWORDS.keys())})\n"
        f"  - data_type (one of: {', '.join(DATA_TYPES)})\n"
        f"  - division, team, year (int 2020-2099)\n"
        f"  - status (one of: {', '.join(STATUSES)})\n"
        f"  - classification (one of: {', '.join(CLASSIFICATIONS)})\n"
        f"  - capabilities (subset of: {', '.join(CAPABILITY_LABELS)})\n"
        f"  - language (one of: {', '.join(LANGUAGES)})\n"
        "  - quality_score_gte (int 0-100)\n"
        "  - created_at_gte (ISO date YYYY-MM-DD)\n"
        "  - tags (array of free strings)\n"
        "  - q (free-text remainder for FTS, optional)\n\n"
        "Output ONLY a JSON object with two keys: filters (object), "
        "explanation (short Korean sentence). No prose."
    )
    try:
        client = AsyncOpenAI(api_key=api_key)
        resp = await client.chat.completions.create(
            model=os.environ.get("OPENAI_ASK_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            timeout=20,
        )
        text = resp.choices[0].message.content or "{}"
        parsed = json.loads(text)
    except Exception as exc:  # pragma: no cover — depends on network
        log.warning("LLM ask failed (%s) — falling back to keyword", exc)
        return None

    return _validate_filters(parsed)


def _validate_filters(parsed: dict[str, Any]) -> dict[str, Any]:
    """LLM 응답을 도메인 enum 으로 sanitize."""
    filters_raw = parsed.get("filters") or {}
    if not isinstance(filters_raw, dict):
        return {"filters": {}, "explanation": "LLM 응답이 dict 가 아님 — 무시"}

    filters: dict[str, Any] = {}
    if (v := filters_raw.get("agent")) and isinstance(v, str):
        filters["agent"] = v
    if (v := filters_raw.get("data_type")) and v in DATA_TYPES:
        filters["data_type"] = v
    if (v := filters_raw.get("status")) and v in STATUSES:
        filters["status"] = v
    if (v := filters_raw.get("classification")) and v in CLASSIFICATIONS:
        filters["classification"] = v
    if (v := filters_raw.get("language")) and v in LANGUAGES:
        filters["language"] = v
    if (v := filters_raw.get("year")) is not None:
        try:
            yv = int(v)
            if 2020 <= yv <= 2099:
                filters["year"] = yv
        except (TypeError, ValueError):
            pass
    if (v := filters_raw.get("quality_score_gte")) is not None:
        try:
            qv = int(v)
            if 0 <= qv <= 100:
                filters["quality_score_gte"] = qv
        except (TypeError, ValueError):
            pass
    if (v := filters_raw.get("created_at_gte")) and isinstance(v, str):
        try:
            datetime.fromisoformat(v)
            filters["created_at_gte"] = v
        except ValueError:
            pass
    if (v := filters_raw.get("capabilities")) and isinstance(v, list):
        valid = [c for c in v if c in CAPABILITY_LABELS]
        if valid:
            filters["capabilities"] = valid
    if (v := filters_raw.get("tags")) and isinstance(v, list):
        valid_tags = [t for t in v if isinstance(t, str) and t.strip()]
        if valid_tags:
            filters["tags"] = valid_tags
    if (v := filters_raw.get("division")) and isinstance(v, str):
        filters["division"] = v.upper()
    if (v := filters_raw.get("team")) and isinstance(v, str):
        filters["team"] = v.upper()
    if (v := filters_raw.get("q")) and isinstance(v, str) and v.strip():
        filters["q"] = v.strip()

    explanation = parsed.get("explanation") or ""
    if not isinstance(explanation, str):
        explanation = ""

    return {"filters": filters, "explanation": explanation}


async def interpret_query(query: str) -> dict[str, Any]:
    """``query`` 를 ``{filters, explanation, source}`` 로 해석한다.

    LLM 사용 시 source="llm", 폴백 시 source="keyword".
    """
    if not query or not query.strip():
        return {"filters": {}, "explanation": "빈 쿼리", "source": "noop"}

    llm_result = await _interpret_with_llm(query)
    if llm_result and llm_result.get("filters"):
        return {**llm_result, "source": "llm"}

    kw = _interpret_keywords(query)
    return {**kw, "source": "keyword"}


# ---------------------------------------------------------------------------
# /api/ask — 검색 실행
# ---------------------------------------------------------------------------
async def execute_ask(
    session: AsyncSession,
    *,
    query: str,
    limit: int = 5,
) -> dict[str, Any]:
    """자연어 쿼리 → 해석 → record 검색."""
    interpreted = await interpret_query(query)
    filters = interpreted["filters"]

    stmt = select(Record)
    if (v := filters.get("data_type")):
        stmt = stmt.where(Record.data_type == v)
    if (v := filters.get("division")):
        stmt = stmt.where(Record.division == v)
    if (v := filters.get("team")):
        stmt = stmt.where(Record.team == v)
    if (v := filters.get("year")) is not None:
        stmt = stmt.where(Record.year == v)
    if (v := filters.get("status")):
        stmt = stmt.where(Record.status == v)
    if (v := filters.get("classification")):
        stmt = stmt.where(Record.classification == v)
    if (v := filters.get("language")):
        stmt = stmt.where(Record.language == v)
    if (v := filters.get("quality_score_gte")) is not None:
        stmt = stmt.where(Record.quality_score >= v)
    if (v := filters.get("created_at_gte")):
        try:
            cutoff = datetime.fromisoformat(v).replace(tzinfo=timezone.utc)
            stmt = stmt.where(Record.created_at >= cutoff)
        except (TypeError, ValueError):
            pass

    # agent / capabilities / tags 는 ARRAY 로 — 파이썬 후필터 사용 (dialect-agnostic).
    rows = (await session.execute(stmt.limit(max(limit * 4, 50)))).scalars().unique().all()

    agent_filter = filters.get("agent")
    cap_filter = filters.get("capabilities") or []
    tag_filter = filters.get("tags") or []
    q_text = (filters.get("q") or query).lower().strip()

    matched: list[Record] = []
    for r in rows:
        if agent_filter and agent_filter not in (r.agents or []):
            continue
        if cap_filter and not all(c in (r.capabilities or []) for c in cap_filter):
            continue
        if tag_filter and not all(t in (r.tags or []) for t in tag_filter):
            continue
        matched.append(r)

    total_matched = len(matched)
    matched.sort(
        key=lambda r: (r.updated_at or datetime(1970, 1, 1, tzinfo=timezone.utc)),
        reverse=True,
    )
    matched = matched[:limit]

    results = [
        {
            "id": r.id,
            "data_type": r.data_type,
            "title": r.title,
            "summary": r.summary or "",
            "tags": list(r.tags or []),
            "agents": list(r.agents or []),
            "classification": r.classification,
            "status": r.status,
            "quality_score": r.quality_score,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in matched
    ]

    follow_ups = [
        "GET /api/records/{id} — 위 결과 중 한 건 풀 상세",
        "GET /api/records/{id}/sections — DOC 본문 섹션 정독",
        "GET /api/analytics/distribution — 분포 확인",
        "GET /api/discover — 전체 카탈로그",
    ]
    if agent_filter:
        follow_ups.insert(
            0, f"GET /api/agents/{agent_filter}/records — {agent_filter} 의 모든 record"
        )
    if filters.get("data_type") == "DATA":
        follow_ups.insert(0, "GET /api/views/tabular — 표 가진 record 만 슬라이스")

    return {
        "interpreted_query": {
            **{k: v for k, v in filters.items()},
            "explanation": interpreted.get("explanation", ""),
            "source": interpreted.get("source", "keyword"),
        },
        "results": results,
        "total_matched": total_matched,
        "follow_up_queries": follow_ups,
        "raw_query": query,
    }


__all__ = [
    "AGENT_GUIDES_DESCRIPTION",
    "AGENT_GUIDE_INDEX",
    "AGENT_GUIDE_SIZES",
    "DEFAULT_AGENT_GUIDE_SIZE",
    "agent_guide_path",
    "build_discover_payload",
    "build_hints",
    "build_json_schema",
    "build_llm_doc",
    "clear_cache",
    "execute_ask",
    "interpret_query",
    "list_hint_contexts",
    "load_agent_guide",
]
