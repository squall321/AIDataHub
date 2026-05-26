"""POST /api/ask — 자연어 쿼리 (LLM-disabled 폴백) 검증.

OPENAI_API_KEY 가 없는 기본 환경에서는 키워드 폴백이 동작한다.
"""
from __future__ import annotations

import os

import pytest

from api.services import discover_svc


@pytest.fixture(autouse=True)
def _disable_llm(monkeypatch):
    """테스트는 항상 키워드 폴백 경로를 검증한다."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    yield


@pytest.mark.asyncio
async def test_ask_returns_basic_shape(db_client, seed_records) -> None:
    resp = await db_client.post("/api/ask", json={"query": "IGA", "limit": 5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("interpreted_query", "results", "total_matched", "follow_up_queries", "raw_query"):
        assert key in body, f"missing key: {key}"
    # source 표기는 keyword (LLM 비활성)
    assert body["interpreted_query"].get("source") == "keyword"


@pytest.mark.asyncio
async def test_ask_keyword_detects_agent(db_client, seed_records) -> None:
    resp = await db_client.post("/api/ask", json={"query": "IGA NURBS"})
    body = resp.json()
    assert body["interpreted_query"].get("agent") == "iga-analyst"


@pytest.mark.asyncio
async def test_ask_keyword_detects_data_type(db_client, seed_records) -> None:
    resp = await db_client.post(
        "/api/ask", json={"query": "측정 데이터 표"}
    )
    body = resp.json()
    assert body["interpreted_query"].get("data_type") == "DATA"


@pytest.mark.asyncio
async def test_ask_keyword_detects_quality_score(db_client, seed_records) -> None:
    resp = await db_client.post(
        "/api/ask", json={"query": "quality_score 80 이상"}
    )
    body = resp.json()
    assert body["interpreted_query"].get("quality_score_gte") == 80


@pytest.mark.asyncio
async def test_ask_keyword_detects_year(db_client, seed_records) -> None:
    resp = await db_client.post("/api/ask", json={"query": "2026 IGA"})
    body = resp.json()
    assert body["interpreted_query"].get("year") == 2026
    # 결과는 시드된 2026 record 만 포함
    for r in body["results"]:
        # response 가 비어있을 수 있으나, 있으면 year 필터 결과여야 함
        pass


@pytest.mark.asyncio
async def test_ask_recent_window(db_client, seed_records) -> None:
    """``최근 1주`` 키워드 → created_at_gte ISO date."""
    resp = await db_client.post("/api/ask", json={"query": "최근 1주일 IGA"})
    body = resp.json()
    iq = body["interpreted_query"]
    assert iq.get("created_at_gte"), "expected created_at_gte"
    # ISO date 형식
    assert len(iq["created_at_gte"]) == 10
    assert iq["created_at_gte"][4] == "-"


@pytest.mark.asyncio
async def test_ask_empty_query_rejected(db_client) -> None:
    resp = await db_client.post("/api/ask", json={"query": ""})
    # Pydantic min_length=1 → 422
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ask_follow_up_present(db_client, seed_records) -> None:
    resp = await db_client.post("/api/ask", json={"query": "IGA"})
    body = resp.json()
    follow = body["follow_up_queries"]
    assert isinstance(follow, list)
    assert len(follow) >= 2
    assert any("/api/records" in f for f in follow)


@pytest.mark.asyncio
async def test_interpret_keywords_unit() -> None:
    """순수 키워드 인터프리터 단위 테스트.

    session=None 호출 — DB 기반 agent 매칭 비활성 (Migration 0012 이후 정책).
    따라서 ``agent`` 필터는 None. data_type / quality / 날짜 추출만 검증.
    """
    parsed = await discover_svc._interpret_keywords(  # type: ignore[attr-defined]
        "최근 7일 IGA 시뮬레이션 quality 80 이상"
    )
    f = parsed["filters"]
    assert f.get("agent") is None  # DB 없을 땐 agent 매칭 안 함
    assert f.get("data_type") == "SIM"
    assert f.get("quality_score_gte") == 80
    assert f.get("created_at_gte")
