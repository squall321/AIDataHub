"""/api/docs/llm.txt — 단일 통합 마크다운 컨텐츠 검증."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_llm_doc_returns_markdown(db_client) -> None:
    resp = await db_client.get("/api/docs/llm.txt")
    assert resp.status_code == 200, resp.text
    text = resp.text
    # 마크다운 섹션 — 필수 키 presence
    for marker in (
        "What is this hub",
        "Core concepts",
        "ID format",
        "Key endpoints",
        "Common query patterns",
        "data_type",
        "/api/discover",
        "/api/schema",
        "/api/ask",
        "/api/records",
    ):
        assert marker in text, f"missing section/marker: {marker!r}"


@pytest.mark.asyncio
async def test_llm_doc_size_bounded(db_client) -> None:
    """5-10KB 정도여야 한다 (LLM 컨텍스트 비용 절약)."""
    resp = await db_client.get("/api/docs/llm.txt")
    body = resp.text
    assert 1500 < len(body) < 15000, f"unexpected llm.txt size: {len(body)}"


@pytest.mark.asyncio
async def test_llm_doc_lists_all_data_types(db_client) -> None:
    resp = await db_client.get("/api/docs/llm.txt")
    body = resp.text
    for dt in ("DOC", "DATA", "SIM", "CAD", "LOG", "FORM", "OTHER"):
        assert dt in body


@pytest.mark.asyncio
async def test_llm_doc_id_pattern_documented(db_client) -> None:
    resp = await db_client.get("/api/docs/llm.txt")
    body = resp.text
    # ID 포맷 문구가 들어있어야 한다
    assert "DOC-HE-CAE-2026-0000000001" in body or "DATA_TYPE" in body


@pytest.mark.asyncio
async def test_llm_doc_recipe_present(db_client) -> None:
    """4-step 패턴 (discover → narrow → detail → traverse) 가 드러나야 한다."""
    resp = await db_client.get("/api/docs/llm.txt")
    body = resp.text.lower()
    assert "discover" in body
    assert "narrow" in body or "ask" in body
    assert "detail" in body
    assert "traverse" in body or "related" in body
