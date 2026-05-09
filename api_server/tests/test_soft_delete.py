"""Soft delete 라우터 통합 테스트 (Migration 0008)."""
from __future__ import annotations

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_default_list_excludes_deleted(
    db_client, seed_records, test_session_maker
) -> None:
    """기본 list 호출은 soft-deleted 레코드를 제외한다."""
    rid = seed_records["rec2"]
    resp_del = await db_client.delete(f"/api/records/{rid}")
    assert resp_del.status_code == 204

    resp = await db_client.get("/api/records")
    assert resp.status_code == 200
    body = resp.json()
    ids = {r["id"] for r in body["items"]}
    assert rid not in ids
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_include_deleted_returns_soft_deleted(
    db_client, seed_records
) -> None:
    """``?include_deleted=true`` 옵션은 soft-deleted 레코드까지 반환한다."""
    rid = seed_records["rec2"]
    await db_client.delete(f"/api/records/{rid}")

    resp = await db_client.get("/api/records", params={"include_deleted": "true"})
    assert resp.status_code == 200
    body = resp.json()
    ids = {r["id"] for r in body["items"]}
    assert rid in ids
    assert body["total"] == 3


@pytest.mark.asyncio
async def test_get_deleted_returns_404_by_default(
    db_client, seed_records
) -> None:
    """soft-deleted 레코드는 기본 GET 에서 404 응답."""
    rid = seed_records["rec1"]
    resp_del = await db_client.delete(f"/api/records/{rid}")
    assert resp_del.status_code == 204

    resp = await db_client.get(f"/api/records/{rid}")
    assert resp.status_code == 404

    # include_deleted 로는 조회 가능.
    resp2 = await db_client.get(
        f"/api/records/{rid}", params={"include_deleted": "true"}
    )
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_restore_endpoint_clears_deleted_at(
    db_client, seed_records, test_session_maker
) -> None:
    """POST /restore 가 ``deleted_at`` 을 NULL 로 되돌린다."""
    from api.db.models import Record

    rid = seed_records["rec3"]
    await db_client.delete(f"/api/records/{rid}")

    # 복원 전 — deleted_at 가 세팅됨.
    async with test_session_maker() as session:
        rec = (
            await session.execute(select(Record).where(Record.id == rid))
        ).scalar_one_or_none()
        assert rec is not None
        assert rec.deleted_at is not None

    resp = await db_client.post(f"/api/records/{rid}/restore")
    assert resp.status_code == 200, resp.text

    async with test_session_maker() as session:
        rec = (
            await session.execute(select(Record).where(Record.id == rid))
        ).scalar_one_or_none()
        assert rec is not None
        assert rec.deleted_at is None

    # 복원 후 GET 가능.
    resp2 = await db_client.get(f"/api/records/{rid}")
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_hard_delete_requires_bootstrap(
    db_client, seed_records
) -> None:
    """``?hard=true`` 는 bootstrap 키 없이는 403."""
    rid = seed_records["rec1"]
    resp = await db_client.delete(f"/api/records/{rid}", params={"hard": "true"})
    # 환경에 따라 401 (auth required) 또는 403 (bootstrap 미인증) 이 발생할 수 있다.
    assert resp.status_code in (401, 403)
