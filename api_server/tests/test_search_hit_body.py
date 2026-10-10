# 검색 히트가 절 본문을 자르지 않고 싣는지 — 좌석이 지식카드 본문을 앞 200자만 보던 결함의 회귀 시험
"""시맨틱·FTS·하이브리드·data_for_agent·tag_search 히트의 본문 길이.

예전에는 시맨틱 히트의 ``snippet`` 이 절 본문의 앞 200자, FTS 히트가 300자 창이었고
'…' 도 길이도 붙지 않았다. 심의 엔진은 지식카드 한 줄을 이 ``snippet`` 으로 만들어서
엔진 쪽 예산을 아무리 올려도 상류에서 200자가 끝이었다(카드 절 평균 230~450자).
운영 모델 창은 1,048,576 토큰이라 이 절단을 둘 까닭이 없다 — 화면 표시용 자르기는
대시보드(dashboard.js `slice(0, 200)`)와 군집 보기(cluster_svc)가 따로 한다.
"""
from __future__ import annotations

import pytest

# 카드 절 하나 — 200·300자 경계를 넘는 길이. 끝에 표식을 둬 '끝까지 왔는가' 를 본다.
_BODY = (
    "오링 압축률은 최악 공차에서도 20% 이상을 남긴다 — 규칙\n"
    + "공칭 압축률은 하한이 아니라 하한 + 공차 여유로 설정한다. " * 30
    + "끝표식-ZQX"
)
_SUMMARY = "실링 경로 요약 " * 60 + "요약끝-ZQY"


async def _seed(maker, *, embed: bool = True) -> None:
    from api.db.models import Agent, Record, RecordSection
    from api.services.embedding import get_embedder

    emb = get_embedder()
    async with maker() as s:
        s.add(Agent(agent_type="mech-ip-sealing", name="실링", description="방수 실링",
                    common_tags=["실링"], data_types=["DOC"]))
        rec = Record(
            id="DOC-CCT-MECH-2026-0000000142", data_type="DOC", team="CCT", group="MECH",
            year=2026, seq=142, title="오링압축률규칙", summary=_SUMMARY,
            tags=["실링", "오링"], agents=["mech-ip-sealing"], content={"raw": "x"},
        )
        s.add(rec)
        await s.flush()
        s.add(RecordSection(
            record_id=rec.id, section_id="1", level=1, title="규칙", content_text=_BODY,
            embedding=emb.encode(_BODY) if embed else None,
            embedding_model=emb.name if embed else None,
        ))
        await s.commit()


@pytest.fixture
def _hash_embedder(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")


@pytest.mark.asyncio
async def test_semantic_hit_carries_whole_section_body(_hash_embedder, test_session_maker):
    from api.services import search_svc

    await _seed(test_session_maker)
    async with test_session_maker() as s:
        hits = await search_svc.semantic_search(s, _BODY, top_k=3)
    assert hits and hits[0]["section_id"] == "1"
    # 종전과 같은 것 — 키·점수·출처는 그대로다.
    assert hits[0]["record_id"] == "DOC-CCT-MECH-2026-0000000142"
    assert hits[0]["section_title"] == "규칙" and hits[0]["score"] >= 0.99
    assert hits[0]["snippet"] == _BODY, f"본문이 {len(hits[0]['snippet'])}자에서 잘렸다(원문 {len(_BODY)}자)"


@pytest.mark.asyncio
async def test_fts_section_hit_carries_whole_section_body(_hash_embedder, test_session_maker):
    from api.services import search_svc

    await _seed(test_session_maker)
    async with test_session_maker() as s:
        items, total = await search_svc.fts_search(s, "공차", limit=5)
    sec = [i for i in items if i.get("section_id") == "1"]
    assert sec, items
    assert total >= 1
    assert sec[0]["snippet"] == _BODY, f"FTS 본문이 {len(sec[0]['snippet'])}자 창으로 잘렸다"


@pytest.mark.asyncio
async def test_fts_record_hit_carries_whole_summary(_hash_embedder, test_session_maker):
    """제목·요약으로 맞은 레코드 히트 — 요약 전문."""
    from api.services import search_svc

    await _seed(test_session_maker)
    async with test_session_maker() as s:
        items, _ = await search_svc.fts_search(s, "요약끝-ZQY", limit=5)
    rec = [i for i in items if i.get("section_id") is None]
    assert rec, items
    assert rec[0]["snippet"] == _SUMMARY


@pytest.mark.asyncio
async def test_hybrid_hit_carries_whole_section_body(_hash_embedder, test_session_maker):
    """심의 엔진의 기본 모드다 — 시맨틱·FTS 어느 쪽에서 온 히트든 전문."""
    from api.services import search_svc

    await _seed(test_session_maker)
    async with test_session_maker() as s:
        hits = await search_svc.hybrid_search(s, "공차", top_k=3)
    sec = [h for h in hits if h.get("section_id") == "1"]
    assert sec and sec[0]["snippet"] == _BODY


@pytest.mark.asyncio
async def test_agent_search_hit_carries_whole_section_body(
    _hash_embedder, monkeypatch, test_session_maker
):
    """소비자 쪽 — 엔진이 부르는 MCP agent_search 가 돌려주는 히트의 본문."""
    from api import mcp_runtime

    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    await _seed(test_session_maker)
    for mode in ("semantic", "hybrid", "fts"):
        out = await mcp_runtime.agent_search(agent_type="mech-ip-sealing", q="공차", mode=mode)
        sec = [h for h in out["hits"] if h.get("section_id") == "1"]
        assert sec, (mode, out)
        assert sec[0]["snippet"].endswith("끝표식-ZQX"), (mode, len(sec[0]["snippet"]))


@pytest.mark.asyncio
async def test_data_for_agent_snippet_is_whole(_hash_embedder, test_session_maker):
    from api.services import search_svc

    await _seed(test_session_maker)
    async with test_session_maker() as s:
        out = await search_svc.data_for_agent(s, "mech-ip-sealing", query="공차")
    sec = [r for r in out["results"] if r.get("section_id") == "1"]
    assert sec and sec[0]["snippet"] == _BODY


@pytest.mark.asyncio
async def test_tag_search_summary_is_whole(_hash_embedder, monkeypatch, test_session_maker):
    from api import mcp_runtime

    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    await _seed(test_session_maker)
    out = await mcp_runtime.tag_search(tags="실링,오링")
    assert out and out[0]["summary"] == _SUMMARY
