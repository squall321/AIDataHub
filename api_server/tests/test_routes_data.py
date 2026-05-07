"""/api/data 엔드포인트 (Cline SR 코어) 테스트."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_data_requires_agent_param(db_client, seed_records) -> None:
    resp = await db_client.get("/api/data")
    assert resp.status_code == 422  # missing required `agent`


@pytest.mark.asyncio
async def test_data_returns_doc_section_match(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/data",
        params={"agent": "iga-analyst", "query": "offset", "limit": 5},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent"] == "iga-analyst"
    assert body["query"] == "offset"
    assert body["total_matched"] >= 1
    # rec1 (DOC) 의 섹션이 반환되어야 함
    matched_ids = [r["record_id"] for r in body["results"]]
    assert seed_records["rec1"] in matched_ids
    # 첫 번째 결과는 section_id 가 채워져 있어야 함 (DOC + 매칭)
    section_hits = [r for r in body["results"] if r["data_type"] == "DOC"]
    assert any(r["section_id"] for r in section_hits)


@pytest.mark.asyncio
async def test_data_filters_data_type(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/data",
        params=[
            ("agent", "iga-analyst"),
            ("data_types", "DATA"),
            ("limit", 10),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    types = {r["data_type"] for r in body["results"]}
    assert types <= {"DATA"}


@pytest.mark.asyncio
async def test_data_unknown_agent_returns_empty(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/data",
        params={"agent": "no-such-agent", "query": "anything"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent"] == "no-such-agent"
    assert body["total_matched"] == 0
    assert body["results"] == []


@pytest.mark.asyncio
async def test_data_limit_capped_at_20(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/data",
        params={"agent": "iga-analyst", "limit": 999},
    )
    # FastAPI 가 ge/le validator 로 422 반환
    assert resp.status_code == 422
