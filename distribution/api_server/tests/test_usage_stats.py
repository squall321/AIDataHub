"""Usage stats 추적 + 분석 엔드포인트 테스트 (Migration 0008 / G5)."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_get_record_increments_read_count(
    db_client, seed_records, test_session_maker
) -> None:
    """``GET /api/records/{id}`` 호출 시 ``read_count`` 가 증가한다."""
    from api.db.models import Record

    rid = seed_records["rec1"]

    resp1 = await db_client.get(f"/api/records/{rid}")
    assert resp1.status_code == 200
    resp2 = await db_client.get(f"/api/records/{rid}")
    assert resp2.status_code == 200
    # 백그라운드 작업이 완료될 시간을 잠시 양보.
    await asyncio.sleep(0.2)

    async with test_session_maker() as session:
        rec = (
            await session.execute(select(Record).where(Record.id == rid))
        ).scalar_one_or_none()
        assert rec is not None
        assert (rec.read_count or 0) >= 2
        assert rec.last_accessed_at is not None


@pytest.mark.asyncio
async def test_analytics_usage_returns_top_records(
    db_client, seed_records, test_session_maker
) -> None:
    """``/api/analytics/usage`` 가 read_count 순으로 상위 N 개를 반환."""
    from api.db.models import Record

    rid_high = seed_records["rec1"]
    rid_low = seed_records["rec3"]

    # 사전에 read_count 를 직접 셋업 (백그라운드 타이밍에 의존하지 않음).
    async with test_session_maker() as session:
        rec_high = (
            await session.execute(select(Record).where(Record.id == rid_high))
        ).scalar_one()
        rec_low = (
            await session.execute(select(Record).where(Record.id == rid_low))
        ).scalar_one()
        rec_high.read_count = 50
        rec_low.read_count = 1
        await session.commit()

    resp = await db_client.get("/api/analytics/usage", params={"limit": 5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body["items"]
    assert items, "analytics/usage returned no items"
    # 첫 결과가 가장 많이 읽힌 레코드여야 함.
    assert items[0]["id"] == rid_high
    assert items[0]["read_count"] == 50


@pytest.mark.asyncio
async def test_usage_excludes_soft_deleted(
    db_client, seed_records, test_session_maker
) -> None:
    """soft-deleted 레코드는 ``analytics/usage`` 에서 제외된다."""
    from datetime import datetime, timezone

    from api.db.models import Record

    rid = seed_records["rec1"]
    async with test_session_maker() as session:
        rec = (
            await session.execute(select(Record).where(Record.id == rid))
        ).scalar_one()
        rec.read_count = 99
        rec.deleted_at = datetime.now(timezone.utc)
        await session.commit()

    resp = await db_client.get("/api/analytics/usage")
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    assert rid not in ids
