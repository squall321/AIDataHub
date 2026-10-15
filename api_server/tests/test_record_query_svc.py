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


# ---------------------------------------------------------------------------
# q 본문 검색 — 스위치(AIDH_RECORDS_Q_BODY_SEARCH, 기본 끔)를 켜고 전문가 필터가 있을 때만
# 본문 절(record_sections)까지 본다. 요약이 빈 카드(mx-whitepaper-analyst 427장 중 320장)는
# 제목 몇 글자로만 걸렸다. 켜기 전에는 운영 DB 사본에서 속도를 잰다(설정 주석).
# ---------------------------------------------------------------------------
async def _seed_body(session, rid, *texts, agents=("a",)):
    from api.db.models import RecordSection

    await _seed(session, id=rid, title="AP", summary="", agents=list(agents))
    for i, t in enumerate(texts, 1):
        session.add(RecordSection(record_id=rid, section_id=str(i), level=1, title=f"절{i}",
                                  content_text=t))
    await session.flush()


@pytest.mark.asyncio
async def test_q_matches_section_body_with_agents(test_session, monkeypatch):
    from api.config import settings
    from api.services.record_query_svc import query_records

    monkeypatch.setattr(settings, "aidh_records_q_body_search", True)
    await _seed_body(test_session, "DOC-HE-CAE-2026-0000000001", "모바일 AP 발열")
    _, total = await query_records(test_session, q="모바일 발열", agents=["a"])
    assert total == 1  # 두 낱말 다 본문에만 있다
    _, total = await query_records(test_session, q="모바일 발열")
    assert total == 0  # 전문가 필터가 없으면 본문은 보지 않는다
    _, total = await query_records(test_session, q="모바일 없는말", agents=["a"])
    assert total == 0  # 본문에서도 낱말마다 AND


@pytest.mark.asyncio
async def test_q_body_words_may_sit_in_different_sections(test_session, monkeypatch):
    from api.config import settings
    from api.services.record_query_svc import query_records

    monkeypatch.setattr(settings, "aidh_records_q_body_search", True)
    await _seed_body(test_session, "DOC-HE-CAE-2026-0000000001", "모바일 개요", "발열 대책")
    _, total = await query_records(test_session, q="모바일 발열", agents=["a"])
    assert total == 1  # 낱말마다 EXISTS 를 따로 두므로 절이 달라도 맞는다
    _, total = await query_records(test_session, q="AP 발열", agents=["a"])
    assert total == 1  # 한 낱말은 제목, 한 낱말은 본문


@pytest.mark.asyncio
async def test_q_body_search_is_off_by_default(test_session, monkeypatch):
    from api.config import Settings, settings
    from api.services.record_query_svc import query_records

    # 코드 기본값 — 박스의 env·.env 와 무관하게 끔이어야 한다.
    assert Settings.model_fields["aidh_records_q_body_search"].default is False
    monkeypatch.setattr(settings, "aidh_records_q_body_search", False)
    await _seed_body(test_session, "DOC-HE-CAE-2026-0000000001", "모바일 AP 발열")
    _, total = await query_records(test_session, q="모바일 발열", agents=["a"])
    assert total == 0  # 스위치를 켜기 전에는 제목·요약만 본다


_DESC_PROBE = """
from api.mcp_runtime import mcp
from api.routes.records import router
r = next(r for r in router.routes if r.path == "/api/records" and "GET" in r.methods)
q = next(p for p in r.dependant.query_params if p.name == "q")
print(mcp._tool_manager.get_tool("list_records").description)
print("@@REST@@", q.field_info.description)
"""


@pytest.mark.parametrize("on", [False, True])
def test_q_descriptions_follow_body_switch(on, tmp_path):
    """설명문은 기동 때 한 번 만들어진다 — 스위치대로 본문을 말해야 모델·사람이 q 를 바로 쓴다.

    별 프로세스로 띄운다. DB 를 부르는 기동 등록(연합·업로드 도구)은 끄고, DB 주소는 닿지 않는
    곳으로 둔다 — 이 시험이 어떤 DB 에도 닿지 않게.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    src = str(Path(__file__).resolve().parents[1] / "src")
    env = {**os.environ, "PYTHONPATH": src, "AIDH_MCP_FEDERATION": "off", "AIDH_MCP_UPLOADS": "off",
           "DATABASE_URL": "postgresql+asyncpg://nobody:x@127.0.0.1:1/none",
           "AIDH_RECORDS_Q_BODY_SEARCH": "true" if on else "false"}
    out = subprocess.run([sys.executable, "-c", _DESC_PROBE], capture_output=True, text=True,
                         env=env, cwd=tmp_path, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    mcp_desc, rest_desc = out.stdout.split("@@REST@@")
    if on:
        assert "낱말마다 제목·요약에서, agents 를 주면 본문에서도 부분 일치" in mcp_desc
        assert "(+ section body with agent)" in rest_desc
    else:
        assert "낱말마다 제목·요약에서 부분 일치" in mcp_desc
        assert "body" not in rest_desc
