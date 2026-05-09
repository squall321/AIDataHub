"""감사 로그 (audit_log) 테스트.

- audit_log 테이블 스키마/모델 형태 확인.
- ``log_action`` 헬퍼가 INSERT/UPDATE/DELETE 이벤트를 기록하는지 검증.
- 라우터를 통한 INSERT/PATCH/DELETE 시 감사 이벤트가 추적되는지 통합 검증.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_audit_log_schema_columns(test_session_maker) -> None:
    """audit_log 테이블이 정의된 컬럼을 모두 가져야 한다."""
    from api.db.models import AuditLog

    cols = {c.name for c in AuditLog.__table__.columns}
    assert {
        "id",
        "record_id",
        "actor",
        "action",
        "field_changes",
        "request_id",
        "created_at",
    } <= cols


@pytest.mark.asyncio
async def test_log_action_helper_writes_row(test_session_maker) -> None:
    """log_action 헬퍼가 단순 INSERT 이벤트를 audit_log 에 추가."""
    from api.db.models import AuditLog
    from api.services.audit import log_action

    async with test_session_maker() as session:
        await log_action(
            session,
            action="INSERT",
            record_id="DOC-HE-CAE-2026-000777",
            actor="cli",
            field_changes={"title": [None, "test"]},
            request_id="req-abc",
        )
        await session.commit()

    async with test_session_maker() as session:
        rows = (await session.execute(select(AuditLog))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.action == "INSERT"
        assert row.record_id == "DOC-HE-CAE-2026-000777"
        assert row.actor == "cli"
        assert row.request_id == "req-abc"
        assert row.field_changes == {"title": [None, "test"]}


@pytest.mark.asyncio
async def test_audit_event_on_insert_via_route(db_client, test_session_maker) -> None:
    """POST /api/records 가 INSERT 이벤트를 audit_log 에 남긴다."""
    from api.db.models import AuditLog

    payload = {
        "id": "DOC-HE-CAE-2026-000111",
        "data_type": "DOC",
        "division": "HE",
        "team": "CAE",
        "year": 2026,
        "seq": 111,
        "title": "audit insert",
        "summary": "",
        "tags": [],
        "agents": [],
        "content": {"x": 1},
    }
    resp = await db_client.post("/api/records", json=payload)
    assert resp.status_code == 201, resp.text

    async with test_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.record_id == payload["id"])
                )
            )
            .scalars()
            .all()
        )
        assert any(r.action == "INSERT" for r in rows)


@pytest.mark.asyncio
async def test_audit_event_on_update_via_patch(
    db_client, seed_records, test_session_maker
) -> None:
    """PATCH 가 UPDATE 이벤트와 변경 필드 diff 를 기록한다."""
    from api.db.models import AuditLog

    rid = seed_records["rec1"]
    resp = await db_client.patch(
        f"/api/records/{rid}",
        json={"summary": "audit-updated", "tags": ["AUDIT"]},
    )
    assert resp.status_code == 200, resp.text

    async with test_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.record_id == rid)
                    .where(AuditLog.action == "UPDATE")
                )
            )
            .scalars()
            .all()
        )
        assert rows, "UPDATE audit row not found"
        diff = rows[-1].field_changes or {}
        # 변경된 필드 중 일부가 들어있어야 함.
        assert "summary" in diff or "tags" in diff


@pytest.mark.asyncio
async def test_audit_event_on_delete_via_route(
    db_client, seed_records, test_session_maker
) -> None:
    """DELETE (soft) 가 DELETE 이벤트를 남긴다."""
    from api.db.models import AuditLog

    rid = seed_records["rec3"]
    resp = await db_client.delete(f"/api/records/{rid}")
    assert resp.status_code == 204

    async with test_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.record_id == rid)
                    .where(AuditLog.action == "DELETE")
                )
            )
            .scalars()
            .all()
        )
        assert rows, "DELETE audit row missing"
