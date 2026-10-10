# FTS OR 폴백이 질의 앞 12토큰만 쓴다는 사실을 남기는지 — 확장어가 말없이 빠지던 것의 회귀 시험
"""AND 매칭이 0건이면 토큰 OR 로 한 번 더 묻는데, sql_compat 가 앞 12토큰만 쓴다(FTS 비용 — 이번에
늘리지 않는다). agent_search 는 현장 용어를 질의 **뒤에** 덧붙이므로 원 질의가 길면 덧붙인 용어가
통째로 빠진다. 상한은 두고, 걸렸으면 경고를 남긴다.
"""
from __future__ import annotations

import logging

import pytest


@pytest.mark.asyncio
async def test_or_fallback_token_cap_is_logged(test_session_maker, caplog):
    from api.db.models import Record, RecordSection
    from api.services import search_svc

    async with test_session_maker() as s:
        s.add(Record(id="DOC-HE-CAE-2026-0000000701", data_type="DOC", team="HE", group="CAE",
                     year=2026, seq=701, title="t", summary="", tags=[], agents=[],
                     content={"raw": "x"}))
        await s.flush()
        s.add(RecordSection(record_id="DOC-HE-CAE-2026-0000000701", section_id="1", level=1,
                            title="절", content_text="스웰링 가스발생 본문"))
        await s.commit()

    q = " ".join(f"없는말{i}" for i in range(12)) + " 스웰링"      # 13번째 토큰만 맞는다
    caplog.set_level(logging.WARNING, logger="api.services.search_svc")
    async with test_session_maker() as s:
        items, total = await search_svc.fts_search(s, q, limit=5)
    assert items == [] and total == 0          # 종전과 같다 — 상한은 그대로 둔다
    assert any("12" in r.getMessage() and "토큰" in r.getMessage() for r in caplog.records), \
        "OR 폴백이 뒤 토큰을 버렸는데 경고가 없다"
