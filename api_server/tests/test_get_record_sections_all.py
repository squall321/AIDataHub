# get_record_sections 가 기본으로 레코드의 절을 전부 주는지 — 앞 10절(최대 50절)에서 말없이 끊기던 결함의 회귀 시험
"""예전에는 sections 를 안 주면 앞 10절, limit 을 올려도 50절이 끝이었고 offset 도 total 도
없어 50절 넘는 문서의 뒤를 읽을 방법이 없었다. 운영 창(1,048,576 토큰)에는 특허 한 건(평균
101,242자)도 들어간다 — 기본은 전부, limit·offset 은 호출자가 나눠 읽을 때 쓴다.
"""
from __future__ import annotations

import pytest

_RID = "DOC-HE-CAE-2026-0000000888"
_N = 60


async def _seed(maker) -> None:
    from api.db.models import Record, RecordSection

    async with maker() as s:
        s.add(Record(id=_RID, data_type="DOC", team="HE", group="CAE", year=2026, seq=888,
                     title="긴 문서", summary="", tags=[], agents=[], content={"raw": "x"}))
        await s.flush()
        for i in range(_N):
            s.add(RecordSection(record_id=_RID, section_id=f"s{i:02d}", level=1, title=f"절{i}",
                                content_text=f"본문 {i}"))
        await s.commit()


@pytest.fixture
def _rt(monkeypatch, test_session_maker):
    from api import mcp_runtime

    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    return mcp_runtime


@pytest.mark.asyncio
async def test_default_returns_every_section(_rt, test_session_maker):
    await _seed(test_session_maker)
    out = await _rt.get_record_sections(record_id=_RID)
    assert len(out) == _N, f"기본이 {len(out)}절에서 끊겼다(전체 {_N}절)"
    assert out[0]["section_id"] == "s00" and out[-1]["section_id"] == f"s{_N - 1}"


@pytest.mark.asyncio
async def test_limit_is_not_capped_at_fifty(_rt, test_session_maker):
    await _seed(test_session_maker)
    out = await _rt.get_record_sections(record_id=_RID, limit=_N)
    assert len(out) == _N


@pytest.mark.asyncio
async def test_offset_reads_the_tail(_rt, test_session_maker):
    await _seed(test_session_maker)
    out = await _rt.get_record_sections(record_id=_RID, limit=5, offset=_N - 5)
    assert [s["section_id"] for s in out] == [f"s{i:02d}" for i in range(_N - 5, _N)]
    tail = await _rt.get_record_sections(record_id=_RID, offset=_N - 3)   # limit 없이 offset 만
    assert [s["section_id"] for s in tail] == [f"s{i:02d}" for i in range(_N - 3, _N)]


@pytest.mark.asyncio
async def test_named_sections_keep_input_order(_rt, test_session_maker):
    """특성화 — sections 를 주면 그 절만, 입력 순서대로(종전과 같다)."""
    await _seed(test_session_maker)
    out = await _rt.get_record_sections(record_id=_RID, sections=["s07", "s02", "없는절"])
    assert [s["section_id"] for s in out] == ["s07", "s02"]
    assert out[0]["content_text"] == "본문 7"
