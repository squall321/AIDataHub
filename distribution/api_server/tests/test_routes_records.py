"""/api/records 엔드포인트 테스트."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_list_records_happy(db_client, seed_records) -> None:
    resp = await db_client.get("/api/records")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["limit"] == 20
    assert body["offset"] == 0
    assert len(body["items"]) == 3
    ids = {r["id"] for r in body["items"]}
    assert ids == set(seed_records.values())


@pytest.mark.asyncio
async def test_list_records_filter_by_year_and_team(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/records",
        params={"year": 2026, "group": "CAE", "data_type": "DOC"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == seed_records["rec1"]


@pytest.mark.asyncio
async def test_get_single_record(db_client, seed_records) -> None:
    rid = seed_records["rec1"]
    resp = await db_client.get(f"/api/records/{rid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == rid
    assert body["data_type"] == "DOC"
    assert "IGA" in body["tags"]


@pytest.mark.asyncio
async def test_get_missing_record_returns_404(db_client, seed_records) -> None:
    resp = await db_client.get("/api/records/DOES-NOT-EXIST")
    assert resp.status_code == 404
    body = resp.json()
    # Agent 12 의 unified 에러 핸들러: {"error": {"code","message","details","request_id"}}
    assert body["error"]["code"] == "NOT_FOUND"
    assert "not found" in body["error"]["message"].lower()


@pytest.mark.asyncio
async def test_create_and_delete_record(db_client) -> None:
    payload = {
        "id": "DOC-HE-CAE-2026-000099",
        "data_type": "DOC",
        "team": "HE",
        "group": "CAE",
        "year": 2026,
        "seq": 99,
        "title": "신규 테스트 문서",
        "summary": "create flow",
        "tags": ["x"],
        "agents": ["iga-analyst"],
        "content": {"hi": 1},
    }
    resp = await db_client.post("/api/records", json=payload)
    assert resp.status_code == 201, resp.text
    assert resp.json()["id"] == payload["id"]

    # Duplicate -> 409
    resp_dup = await db_client.post("/api/records", json=payload)
    assert resp_dup.status_code == 409

    # Delete
    resp_del = await db_client.delete(f"/api/records/{payload['id']}")
    assert resp_del.status_code == 204

    # Not found after delete
    resp_after = await db_client.get(f"/api/records/{payload['id']}")
    assert resp_after.status_code == 404


@pytest.mark.asyncio
async def test_patch_record_summary(db_client, seed_records) -> None:
    rid = seed_records["rec1"]
    resp = await db_client.patch(
        f"/api/records/{rid}",
        json={"summary": "updated summary", "tags": ["NEW"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"] == "updated summary"
    assert body["tags"] == ["NEW"]
