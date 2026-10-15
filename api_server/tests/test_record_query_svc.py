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


# ---------------------------------------------------------------------------
# q — 낱말마다 AND, 낱말 하나는 제목 OR 요약. '%'·'_' 는 글자 그대로다.
# 예전에는 q 전체를 한 덩어리로 ILIKE '%q%' 에 걸어, 카드에 있는 낱말 둘도 순서가 바뀌거나
# 사이에 낱말이 끼면 0건이었고(cae00 cam-aa-process '접착제 드리프트' 0건, 2026-10-15),
# 'Camera_module' 의 '_' 가 아무 글자에나 맞았다.
# ---------------------------------------------------------------------------
_Q_SEEDS = [  # (제목, 요약)
    ("접착제 물성과 온도에 따른 초점 드리프트", "요약말"),
    ("수율 100%", ""),
    ("부품 1000개", ""),
    ("Camera_module", ""),
    ("Camera module", ""),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("q, n", [
    ("드리프트 접착제", 1),        # 순서를 뒤집었다
    ("접착제 드리프트", 1),        # 사이에 낱말이 낀다
    ("  접착제   드리프트 ", 1),   # 공백 여럿
    ("접착제 요약말", 1),          # 한 낱말은 제목, 한 낱말은 요약
    ("접착제 없는말", 0),          # 한 낱말이 없다 → AND
    ("100%", 1),                   # 예전에는 '부품 1000개' 까지 2건
    ("Camera_module", 1),          # 예전에는 'Camera module' 까지 2건
    ("module Camera", 2),          # 'Camera_module' 도 두 낱말을 다 담는다
])
async def test_q_words_and(test_session, q, n):
    from api.services.record_query_svc import query_records

    for i, (title, summary) in enumerate(_Q_SEEDS, 1):
        await _seed(test_session, id=f"DOC-HE-CAE-2026-000000000{i}", title=title, summary=summary)
    _, total = await query_records(test_session, q=q)
    assert total == n


@pytest.mark.asyncio
async def test_q_word_cap_is_logged(test_session, caplog):
    """낱말 상한(FTS_OR_MAX_TOKENS) 뒤 낱말은 조건에서 빠진다 — 결과가 넓어지므로 말없이 두지 않는다."""
    import logging

    from api.services.record_query_svc import query_records
    from api.services.sql_compat import FTS_OR_MAX_TOKENS

    await _seed(test_session, id="DOC-HE-CAE-2026-0000000001", title=" ".join(
        f"w{i}" for i in range(FTS_OR_MAX_TOKENS)))
    caplog.set_level(logging.WARNING, logger="api.services.record_query_svc")
    q = " ".join(f"w{i}" for i in range(FTS_OR_MAX_TOKENS)) + " 없는말"
    _, total = await query_records(test_session, q=q)
    assert total == 1   # 13번째 낱말은 조건에 들지 않는다(상한은 그대로 둔다)
    assert any(str(FTS_OR_MAX_TOKENS) in r.getMessage() and "낱말" in r.getMessage()
               for r in caplog.records), "상한 뒤 낱말을 버렸는데 경고가 없다"
