# 레코드 자동 바인딩이 훑기 상한(기본 500건)에 걸렸을 때 말하는지 — '자기 카드' 가 말없이 덜 묶이던 것의 회귀 시험
"""bind_matching_records 는 태그 후필터 **앞**에서 ``limit`` 건만(정렬 없이) 훑는다. 후보가 그보다
많으면 뒤쪽 레코드는 그 좌석 것이 못 되는데, 응답은 scanned·bound_count 뿐이라 알 길이 없었다.
상한 자체는 쓰기 도구의 범위라 그대로 두고(바꾸면 한 번에 묶는 수가 달라진다) 걸렸다는 사실을 싣는다.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_scan_limit_hit_is_reported(test_session_maker):
    from api.db.models import Record
    from api.services import agent_draft_svc

    async with test_session_maker() as s:
        for i in range(5):
            s.add(Record(id=f"DOC-HE-CAE-2026-00000006{i:02d}", data_type="DOC", team="HE",
                         group="CAE", year=2026, seq=600 + i, title=f"t{i}", summary="",
                         tags=["낙하"], agents=[], content={"raw": "x"}))
        await s.commit()
    async with test_session_maker() as s:
        out = await agent_draft_svc.bind_matching_records(
            s, agent_type="seat-b", required_doc_type=None, required_tags=[],
            common_tags=["낙하"], data_types=["DOC"], limit=3)
    assert out["scanned"] == 3 and out["bound_count"] == 3
    assert out["scan_limit"] == 3 and out["scan_truncated"] is True
    assert out["bound_ids_omitted"] == 0

    async with test_session_maker() as s:
        out2 = await agent_draft_svc.bind_matching_records(
            s, agent_type="seat-c", required_doc_type=None, required_tags=[],
            common_tags=["낙하"], data_types=["DOC"], limit=10)
    assert out2["scanned"] == 5 and out2["scan_truncated"] is False
