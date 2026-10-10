# recommend_agents 응답의 설명 전문과 '더 있다' — 폴백 카탈로그 설명 200자·relevant_tools 설명 300자에서 말없이 자르던 것의 회귀 시험
from __future__ import annotations

import pytest

_LONG_DESC = "회로 보호 소자 선정과 서지 내성 검토를 맡는다. " * 12 + "설명끝-ZQD"
_LONG_TOOL = "열해석 결과를 요약해 부품별 최고 온도를 표로 낸다. " * 15 + "도구끝-ZQT"


@pytest.fixture
def _rt(monkeypatch, test_session_maker):
    from api import mcp_runtime

    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.delenv("AIDH_SEARCH_TOP_K_MAX", raising=False)
    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    return mcp_runtime


@pytest.mark.asyncio
async def test_fallback_catalog_description_is_whole(_rt, test_session_maker):
    """맞는 전문가가 없을 때 돌려주는 카탈로그 — 설명이 200자에서 끊기면 호출자가 고를 근거를 잃는다."""
    from api.db.models import Agent

    async with test_session_maker() as s:
        s.add(Agent(agent_type="passive-esd", name="ESD", description=_LONG_DESC,
                    common_tags=["ESD"], data_types=["DOC"], sample_queries=["서지 내성"]))
        await s.commit()
    out = await _rt.recommend_agents(q="zzqqxx", top_k_tools=0)
    assert out["fallback"] is True
    assert out["catalog"][0]["description"] == _LONG_DESC


@pytest.mark.asyncio
async def test_relevant_tool_description_is_whole(_rt, test_session_maker):
    from api.db.models import MCPUpload
    from api.services import tool_embedding_svc

    manifest = {"name": "thermal_digest", "description": _LONG_TOOL}
    async with test_session_maker() as s:
        s.add(MCPUpload(name="thermal_digest", current_sha="b" * 64, manifest=manifest))
        await s.commit()
    async with test_session_maker() as s:
        await tool_embedding_svc.sync_tool_embedding(s, name="thermal_digest", manifest=manifest)
    async with test_session_maker() as s:
        hits = await tool_embedding_svc.search_tools(
            s, tool_embedding_svc.build_description_text(manifest), top_k=3)
    assert hits and hits[0]["name"] == "thermal_digest"
    assert hits[0]["description"] == _LONG_TOOL


@pytest.mark.asyncio
async def test_says_how_many_were_ranked(_rt, test_session_maker):
    """top_k(기본 5) 밖에 후보가 더 있으면 응답이 그 수를 말한다."""
    from api.db.models import Agent

    async with test_session_maker() as s:
        for i in range(4):
            s.add(Agent(agent_type=f"pwr-swelling-{i}", name=f"스웰링 {i}",
                        description="배터리 스웰링 가스 발생 원인 분석", common_tags=["스웰링"],
                        data_types=["DOC"]))
        await s.commit()
    out = await _rt.recommend_agents(q="배터리 스웰링", top_k=2, top_k_tools=0)
    assert len(out["agents"]) == 2
    assert out["ranked_total"] == 4
    assert out["more_available"] is True
