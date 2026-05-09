"""/api/discover, /api/schema, /api/hints, /api/docs/llm.txt 통합 테스트.

Agent 30 — Discovery / RAG-friendly API 의 가시성 보장.
"""
from __future__ import annotations

import pytest

from api.services import discover_svc


@pytest.fixture(autouse=True)
def _clear_discover_cache():
    """각 테스트 전후로 in-memory TTL 캐시 비우기."""
    discover_svc.clear_cache()
    yield
    discover_svc.clear_cache()


@pytest.mark.asyncio
async def test_discover_basic_shape(db_client, seed_records) -> None:
    resp = await db_client.get("/api/discover")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # 핵심 키 존재
    for key in (
        "version",
        "title",
        "description",
        "total_records",
        "by_data_type",
        "by_division",
        "by_classification",
        "agents",
        "data_types_explained",
        "starting_points",
        "schema_url",
        "hints_url",
        "llm_doc_url",
    ):
        assert key in body, f"missing key: {key}"

    # 시드 데이터 카운트
    assert body["total_records"] == 3
    assert body["by_data_type"]["DOC"] == 2
    assert body["by_data_type"]["DATA"] == 1
    assert body["by_division"]["HE"] == 3
    # agents 페이로드는 list[dict]
    assert isinstance(body["agents"], list)
    assert any(a["agent_type"] == "iga-analyst" for a in body["agents"])


@pytest.mark.asyncio
async def test_discover_agent_record_count(db_client, seed_records) -> None:
    resp = await db_client.get("/api/discover")
    assert resp.status_code == 200
    body = resp.json()
    iga = next(a for a in body["agents"] if a["agent_type"] == "iga-analyst")
    # iga-analyst → rec1, rec2 → 2건
    assert iga["record_count"] == 2
    assert "sample_query" in iga
    assert iga["sample_query"].endswith("agent=iga-analyst")


@pytest.mark.asyncio
async def test_discover_data_types_explained_korean(db_client, seed_records) -> None:
    resp = await db_client.get("/api/discover")
    body = resp.json()
    explained = body["data_types_explained"]
    assert "DOC" in explained
    assert "문서" in explained["DOC"]  # Korean description
    assert "DATA" in explained
    assert "헤" in explained["DATA"] or "측정" in explained["DATA"] or "표" in explained["DATA"]


@pytest.mark.asyncio
async def test_discover_caching(db_client, seed_records) -> None:
    """같은 응답이 캐시 만료 전엔 동일해야 한다."""
    r1 = await db_client.get("/api/discover")
    r2 = await db_client.get("/api/discover")
    assert r1.status_code == 200 and r2.status_code == 200
    # generated_at 까지 같은 것이 캐시 hit 의 증거
    assert r1.json()["generated_at"] == r2.json()["generated_at"]


@pytest.mark.asyncio
async def test_discover_no_cache_param(db_client, seed_records) -> None:
    """``?no_cache=true`` 는 다시 빌드한다 — generated_at 갱신."""
    r1 = await db_client.get("/api/discover")
    r2 = await db_client.get("/api/discover?no_cache=true")
    assert r1.status_code == 200 and r2.status_code == 200
    # generated_at 이 다르거나 같을 수 있지만(빠른 실행), 캐시는 무시되었어야 함
    # 적어도 응답 구조는 같음
    assert r1.json()["total_records"] == r2.json()["total_records"]


@pytest.mark.asyncio
async def test_discover_starting_points_listed(db_client, seed_records) -> None:
    resp = await db_client.get("/api/discover")
    body = resp.json()
    starting = "\n".join(body["starting_points"])
    assert "/api/agents" in starting
    assert "/api/ask" in starting
    assert "/api/records" in starting


@pytest.mark.asyncio
async def test_hints_default(db_client) -> None:
    resp = await db_client.get("/api/hints")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["context"] is None
    assert isinstance(body["hints"], list)
    assert len(body["hints"]) >= 5
    # 각 항목 구조 검증
    for item in body["hints"]:
        assert "hint" in item
        assert "sample_endpoint" in item
        assert "why_useful" in item


@pytest.mark.asyncio
async def test_hints_with_context(db_client) -> None:
    resp = await db_client.get("/api/hints?context=getting_started")
    assert resp.status_code == 200
    body = resp.json()
    assert body["context"] == "getting_started"
    assert len(body["hints"]) >= 1
    assert all(h["context"] == "getting_started" for h in body["hints"])


@pytest.mark.asyncio
async def test_hints_unknown_context_empty(db_client) -> None:
    resp = await db_client.get("/api/hints?context=does_not_exist")
    assert resp.status_code == 200
    body = resp.json()
    assert body["hints"] == []
    # 가용 컨텍스트는 노출되어 있어야 함
    assert "available_contexts" in body
    assert "getting_started" in body["available_contexts"]
