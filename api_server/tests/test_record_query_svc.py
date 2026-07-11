# record_query_svc.query_records(정형 필터 조회) 단위 테스트.
"""MCP list_records / REST /api/records 가 공유하는 필터·페이징 로직 검증.

team/group/doc_type/data_type/tags/q 필터 + soft-delete 제외 + 페이징.
"""
from __future__ import annotations

import pytest


async def _seed(session, **over):
    from api.db.models import Record

    _id = over.get("id", "DOC-HE-CAE-2026-0000000001")
    base = dict(
        id=_id, data_type="DOC", team="HE", group="CAE", year=2026,
        seq=int(_id.rsplit("-", 1)[-1]),  # id 끝번호 = 고유 seq (자연키 uq 충돌 방지)
        title="seed", summary="", tags=[], agents=[], content={},
    )
    base.update(over)
    session.add(Record(**base))
    await session.flush()


@pytest.mark.asyncio
async def test_filter_by_team_and_data_type(test_session):
    from api.services.record_query_svc import query_records

    await _seed(test_session, id="DOC-HE-CAE-2026-0000000001", data_type="DOC")
    await _seed(test_session, id="DATA-MX-VOC-2026-0000000001", data_type="DATA", team="MX", group="VOC")

    rows, total = await query_records(test_session, team="HE")
    assert total == 1 and rows[0].team == "HE"

    rows, total = await query_records(test_session, data_type="DATA")
    assert total == 1 and rows[0].data_type == "DATA"


@pytest.mark.asyncio
async def test_filter_by_doc_type(test_session):
    from api.services.record_query_svc import query_records

    await _seed(test_session, id="DOC-HE-CAE-2026-0000000001", doc_type="manual")
    await _seed(test_session, id="DOC-HE-CAE-2026-0000000002", doc_type="report")

    rows, total = await query_records(test_session, doc_type="manual")
    assert total == 1 and rows[0].doc_type == "manual"


@pytest.mark.asyncio
async def test_soft_deleted_excluded(test_session):
    from datetime import datetime, timezone

    from api.services.record_query_svc import query_records

    await _seed(test_session, id="DOC-HE-CAE-2026-0000000001")
    await _seed(test_session, id="DOC-HE-CAE-2026-0000000002",
                deleted_at=datetime.now(timezone.utc))

    rows, total = await query_records(test_session, team="HE")
    assert total == 1  # soft-deleted 제외

    rows, total = await query_records(test_session, team="HE", include_deleted=True)
    assert total == 2  # 명시하면 포함


@pytest.mark.asyncio
async def test_tags_filter_and(test_session):
    from api.services.record_query_svc import query_records

    await _seed(test_session, id="DOC-HE-CAE-2026-0000000001", tags=["x", "y"])
    await _seed(test_session, id="DOC-HE-CAE-2026-0000000002", tags=["x"])

    rows, total = await query_records(test_session, tags=["x", "y"])
    assert total == 1  # 모두 포함(AND)


@pytest.mark.asyncio
async def test_pagination(test_session):
    from api.services.record_query_svc import query_records

    for i in range(1, 6):
        await _seed(test_session, id=f"DOC-HE-CAE-2026-000000000{i}", seq=i)

    rows, total = await query_records(test_session, team="HE", limit=2, offset=0)
    assert total == 5 and len(rows) == 2


@pytest.mark.asyncio
async def test_to_summary_excludes_body(test_session):
    from api.services.record_query_svc import query_records, to_summary

    await _seed(test_session, id="DOC-HE-CAE-2026-0000000001",
                content={"big": "x" * 9999}, tags=["t"])
    rows, _ = await query_records(test_session, team="HE")
    s = to_summary(rows[0])
    assert "content" not in s  # 본문 제외 (토큰 절약)
    assert s["id"] and s["tags"] == ["t"]
