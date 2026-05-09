"""/api/schema — JSON Schema 응답 검증.

Schema 자체의 유효성 + 핵심 필드/enum 가 노출되었는지 확인.
"""
from __future__ import annotations

import pytest

from api.schemas.common import (
    CAPABILITY_LABELS,
    CLASSIFICATIONS,
    DERIVATIONS,
    STATUSES,
)
from api.schemas.id_format import DATA_TYPES


@pytest.mark.asyncio
async def test_schema_basic_shape(db_client) -> None:
    resp = await db_client.get("/api/schema")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # draft-2020-12
    assert body["$schema"].endswith("/draft/2020-12/schema")
    assert body["type"] == "object"
    assert "properties" in body
    assert "oneOf" in body
    assert "examples" in body


@pytest.mark.asyncio
async def test_schema_required_fields(db_client) -> None:
    resp = await db_client.get("/api/schema")
    body = resp.json()
    assert set(body["required"]) >= {"id", "data_type", "title"}


@pytest.mark.asyncio
async def test_schema_enums_match_constants(db_client) -> None:
    """schema 의 enum 들이 코드의 단일 정의와 일치해야 한다 (drift 방지)."""
    resp = await db_client.get("/api/schema")
    body = resp.json()
    props = body["properties"]
    assert set(props["data_type"]["enum"]) == set(DATA_TYPES)
    assert set(props["classification"]["enum"]) == set(CLASSIFICATIONS)
    assert set(props["status"]["enum"]) == set(STATUSES)
    assert set(props["derivation"]["enum"]) == set(DERIVATIONS)
    # capabilities items also expose the canonical labels
    cap_enum = props["capabilities"]["items"]["enum"]
    assert set(cap_enum) == set(CAPABILITY_LABELS)


@pytest.mark.asyncio
async def test_schema_includes_agent_hints_fields(db_client) -> None:
    """Migration 0007 의 신규 필드가 schema 에 노출됐는지."""
    resp = await db_client.get("/api/schema")
    props = resp.json()["properties"]
    assert "agent_hints" in props
    assert "related_record_ids" in props
    assert "query_examples" in props
    assert "access_pattern" in props
    assert set(props["access_pattern"]["enum"]) == {"frequent", "occasional", "rare"}


@pytest.mark.asyncio
async def test_schema_oneof_per_data_type(db_client) -> None:
    """data_type 별 content 페이로드 모양이 oneOf 로 분기되어 있어야 한다."""
    resp = await db_client.get("/api/schema")
    body = resp.json()
    one_of = body["oneOf"]
    titles = {variant["title"] for variant in one_of}
    assert any("DOC" in t for t in titles)
    assert any("DATA" in t for t in titles)
    assert any("SIM" in t for t in titles)
    assert any("CAD" in t for t in titles)


@pytest.mark.asyncio
async def test_schema_examples_present(db_client) -> None:
    resp = await db_client.get("/api/schema")
    examples = resp.json()["examples"]
    assert len(examples) >= 2
    # 첫 예시는 DOC, 두 번째는 DATA (build_json_schema 의 약속)
    types = {ex["data_type"] for ex in examples}
    assert {"DOC", "DATA"}.issubset(types)
