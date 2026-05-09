"""/api/analytics 엔드포인트 테스트."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_distribution(db_client, seed_records) -> None:
    resp = await db_client.get("/api/analytics/distribution")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["by_type"]["DOC"] == 2
    assert body["by_type"]["DATA"] == 1
    assert body["by_division"]["HE"] == 3
    assert body["by_team"]["CAE"] == 3
    assert body["by_year"]["2026"] == 2
    assert body["by_year"]["2025"] == 1


@pytest.mark.asyncio
async def test_common_tags(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/analytics/common-tags", params={"agent": "iga-analyst"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    tag_set = {item["tag"] for item in body}
    # iga-analyst 가 다루는 rec1, rec2 의 태그가 모두 포함되어야 함
    assert "IGA" in tag_set
    iga_count = next(item for item in body if item["tag"] == "IGA")["count"]
    assert iga_count == 2


@pytest.mark.asyncio
async def test_common_tags_unknown_agent_empty(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/analytics/common-tags", params={"agent": "no-such"}
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_cross_agent(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/analytics/cross-agent",
        params=[("agents", "iga-analyst"), ("agents", "oga-analyst")],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agents"] == ["iga-analyst", "oga-analyst"]
    # 두 에이전트가 모두 다루는 레코드는 rec2 만
    assert body["count"] == 1
    assert body["shared_records"][0]["id"] == seed_records["rec2"]


@pytest.mark.asyncio
async def test_timeline(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/analytics/timeline", params={"year": 2026}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["year"] == 2026
    assert len(body["monthly"]) == 12
    total = sum(m["count"] for m in body["monthly"])
    # 2026 년도 레코드는 rec1, rec2 → 합 2
    assert total == 2
