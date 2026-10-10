# get_context_bundle 이 절 본문을 자르지 않고, 좌석의 레코드·절 총수를 말하는지 — 발췌 500자(마크다운 200자) 절단의 회귀 시험
"""JS 심의 파이프라인의 좌석이 발언 전에 이 번들로 지식카드를 읽는다. 예전엔 절마다 앞 500자,
markdown 은 그것을 다시 200자로 깎고 '...' 를 붙였고, 좌석이 레코드를 몇 건 가졌는지는 없었다.
"""
from __future__ import annotations

import pytest

_AT = "mech-ip-sealing"
_BODY = "낙하 후 디스플레이 코너 방수 PSA 박리 — 원인 메커니즘\n" + "코너의 응력 집중이 박리를 시작한다. " * 40 + "절끝-ZQB"
_SAMPLES = [f"표본 질의 {i}" for i in range(7)]


async def _seed(maker) -> None:
    from api.db.models import Agent, Record, RecordSection

    async with maker() as s:
        s.add(Agent(agent_type=_AT, name="실링", description="방수 실링", common_tags=["실링"],
                    data_types=["DOC"], sample_queries=_SAMPLES))
        for i in range(3):
            rid = f"DOC-CCT-MECH-2026-00000003{i:02d}"
            s.add(Record(id=rid, data_type="DOC", team="CCT", group="MECH", year=2026,
                         seq=300 + i, title=f"카드{i}", summary="", tags=[], agents=[_AT],
                         content={"raw": "x"}))
            await s.flush()
            n = 12 if i == 0 else 1
            for j in range(n):
                s.add(RecordSection(record_id=rid, section_id=f"{j}", level=1, title=f"절{j}",
                                    content_text=_BODY))
        await s.commit()


@pytest.mark.asyncio
async def test_bundle_excerpt_is_whole_and_counts_are_reported(test_session_maker):
    from api.services import recommend_svc

    await _seed(test_session_maker)
    async with test_session_maker() as s:
        b = await recommend_svc.build_context_bundle(
            s, agent_type=_AT, max_records=3, max_sections_per_record=8)
    recs = b["records"]
    assert len(recs) == 3
    sec = recs[0]["key_sections"][0]
    assert sec["excerpt"] == _BODY, f"발췌가 {len(sec['excerpt'])}자에서 잘렸다(원문 {len(_BODY)}자)"
    big = {r["id"]: r for r in recs}["DOC-CCT-MECH-2026-0000000300"]
    assert len(big["key_sections"]) == 8 and big["sections_total"] == 12, "절 총수가 없다"

    async with test_session_maker() as s:
        one = await recommend_svc.build_context_bundle(s, agent_type=_AT, max_records=1)
    assert one["totals"]["records_returned"] == 1
    assert one["totals"]["records_bound"] == 3, "좌석의 레코드 총수가 없다"


@pytest.mark.asyncio
async def test_bundle_markdown_is_whole(test_session_maker):
    from api.services import recommend_svc

    await _seed(test_session_maker)
    async with test_session_maker() as s:
        b = await recommend_svc.build_context_bundle(s, agent_type=_AT)
    md = recommend_svc.render_context_bundle_markdown(b)
    assert "절끝-ZQB" in md, "markdown 발췌가 잘렸다"
    assert all(q in md for q in _SAMPLES), "sample_queries 가 5개에서 잘렸다"
