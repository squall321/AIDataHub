"""/api/search 엔드포인트 테스트."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_search_tag_mode(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/search",
        params=[("mode", "tag"), ("tags", "IGA")],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "tag"
    # IGA 태그를 포함한 레코드: rec1, rec2
    ids = {r["id"] for r in body["items"]}
    assert seed_records["rec1"] in ids
    assert seed_records["rec2"] in ids


@pytest.mark.asyncio
async def test_search_tag_requires_tags(db_client, seed_records) -> None:
    resp = await db_client.get("/api/search", params={"mode": "tag"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_search_fts_mode(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/search",
        params={"mode": "fts", "q": "offset"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "fts"
    assert body["total"] >= 1
    record_ids = {item["record_id"] for item in body["items"]}
    assert seed_records["rec1"] in record_ids


@pytest.mark.asyncio
async def test_search_fts_requires_q(db_client, seed_records) -> None:
    resp = await db_client.get("/api/search", params={"mode": "fts"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_search_semantic_stub(db_client) -> None:
    resp = await db_client.get(
        "/api/search",
        params={"mode": "semantic", "q": "anything"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "semantic"
    assert "not yet implemented" in body["error"]
    assert body["suggested"] == "use mode=fts"
