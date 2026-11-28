# agent_search 건수 — 호출자가 올릴 수 있고, 더 있으면 응답이 말하는지
"""예전 agent_search 는 인자가 (agent_type, q, mode) 뿐이라 건수가 좌석 설정 top_k(없으면 10)로
묶였다. 카드 수천 장 좌석도 질의 한 번에 10건이었고, '더 있다' 는 말도 없었다(hit_count 는 자른
뒤의 수). 좌석 설정은 기본값으로 남기고, 호출자가 정하면 그것을 쓴다.
"""
from __future__ import annotations

import pytest

_AT = "rel-drop-impact"
_N = 6


async def _seed(maker, *, seat_top_k: int | None = 2) -> list[str]:
    from api.db.models import Agent, Record, RecordSection
    from api.services.embedding import get_embedder

    emb = get_embedder()
    texts: list[str] = []
    async with maker() as s:
        s.add(Agent(agent_type=_AT, name="낙하", description="낙하 충격", common_tags=["낙하"],
                    data_types=["DOC"],
                    retrieval_config=({"top_k": seat_top_k} if seat_top_k else {})))
        for i in range(_N):
            rid = f"DOC-HE-CAE-2026-00000005{i:02d}"
            s.add(Record(id=rid, data_type="DOC", team="HE", group="CAE", year=2026,
                         seq=500 + i, title=f"낙하카드{i}", summary=f"낙하 카드 {i}",
                         tags=["낙하", "충격"], agents=[_AT], content={"raw": "x"}))
            await s.flush()
            t = f"낙하 충격 코너 응력 카드 {i} 본문"
            texts.append(t)
            s.add(RecordSection(record_id=rid, section_id="1", level=1, title=f"카드{i}",
                                content_text=t, embedding=emb.encode(t), embedding_model=emb.name))
        await s.commit()
    return texts


@pytest.fixture
def _env(monkeypatch, test_session_maker):
    from api import mcp_runtime

    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.delenv("AIDH_SEARCH_TOP_K_MAX", raising=False)
    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    return mcp_runtime


@pytest.mark.asyncio
async def test_default_is_seat_config_and_says_there_is_more(_env, test_session_maker):
    await _seed(test_session_maker, seat_top_k=2)
    out = await _env.agent_search(agent_type=_AT, q="낙하 충격", mode="semantic")
    assert out["hit_count"] == 2 and out["applied_config"]["top_k"] == 2
    assert out["applied_config"]["top_k_source"] == "seat"
    assert out["more_available"] is True, "좌석에 6장이 맞는데 2장만 주고 더 있다는 말이 없다"


@pytest.mark.asyncio
async def test_default_hits_match_plain_semantic_search(_env, test_session_maker):
    """특성화 — 호출자가 top_k 를 안 주면 종전과 같은 히트·같은 순서다."""
    from api.services import search_svc
    from api.services.recommend_svc import expand_query

    await _seed(test_session_maker, seat_top_k=2)
    out = await _env.agent_search(agent_type=_AT, q="낙하 충격", mode="semantic")
    async with test_session_maker() as s:
        ref = await search_svc.semantic_search(s, expand_query("낙하 충격"), top_k=2,
                                               data_types=["DOC"],
                                               record_ids=[f"DOC-HE-CAE-2026-00000005{i:02d}" for i in range(_N)])
    assert [(h["record_id"], h["score"]) for h in out["hits"]] == [(h["record_id"], h["score"]) for h in ref]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["semantic", "hybrid", "fts", "tag"])
async def test_caller_top_k_overrides_seat(_env, test_session_maker, mode):
    await _seed(test_session_maker, seat_top_k=2)
    q = "낙하,충격" if mode == "tag" else "낙하"
    out = await _env.agent_search(agent_type=_AT, q=q, mode=mode, top_k=_N)
    assert out["hit_count"] == _N, (mode, out["hit_count"])
    assert out["applied_config"]["top_k"] == _N
    assert out["applied_config"]["top_k_source"] == "caller"
    assert out["more_available"] is False


@pytest.mark.asyncio
async def test_seat_without_config_defaults_to_ten(_env, test_session_maker):
    await _seed(test_session_maker, seat_top_k=None)
    out = await _env.agent_search(agent_type=_AT, q="낙하", mode="semantic")
    assert out["applied_config"]["top_k"] == 10 and out["hit_count"] == _N
    assert out["more_available"] is False


# ── 쪽 넘김 · 줄인 꼴(HWAXPortal docs/request-1125 2절 B-1) — 기본값이면 응답이 글자 그대로 ───────────────────────
@pytest.mark.asyncio
async def test_default_response_has_no_new_keys(_env, test_session_maker):
    await _seed(test_session_maker, seat_top_k=2)
    out = await _env.agent_search(agent_type=_AT, q="낙하 충격", mode="semantic")
    assert not {"offset", "truncated", "next"} & set(out), out.keys()
    assert all("snippet_chars" not in h for h in out["hits"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["semantic", "hybrid", "fts", "tag"])
async def test_offset_pages_follow_one_ranking(_env, test_session_maker, mode):
    await _seed(test_session_maker, seat_top_k=2)
    q = "낙하,충격" if mode == "tag" else "낙하"
    whole = await _env.agent_search(agent_type=_AT, q=q, mode=mode, top_k=_N)
    p1 = await _env.agent_search(agent_type=_AT, q=q, mode=mode, top_k=2, offset=0, brief=True)
    assert p1["next"] == {"offset": 2} and p1["offset"] == 0
    p2 = await _env.agent_search(agent_type=_AT, q=q, mode=mode, top_k=2, offset=p1["next"]["offset"])
    p3 = await _env.agent_search(agent_type=_AT, q=q, mode=mode, top_k=2, offset=p2["next"]["offset"])
    ids = [h["record_id"] for page in (p1, p2, p3) for h in page["hits"]]
    assert ids == [h["record_id"] for h in whole["hits"]], (mode, ids)
    assert p3["more_available"] is False and p3["next"] is None
    assert p2["applied_config"]["top_k"] == 2
    empty = await _env.agent_search(agent_type=_AT, q=q, mode=mode, top_k=2, offset=50)
    assert empty["hits"] == [] and empty["refused"] is False, "둘째 쪽부터 빈 쪽은 거절이 아니다"


@pytest.mark.asyncio
async def test_brief_cuts_snippet_and_fields_keep_only_asked(_env, test_session_maker):
    from api.db.models import RecordSection

    await _seed(test_session_maker, seat_top_k=2)
    async with test_session_maker() as s:
        from sqlalchemy import update

        await s.execute(update(RecordSection).values(content_text="낙하 충격 " + "가" * 1000))
        await s.commit()
    full = await _env.agent_search(agent_type=_AT, q="낙하 충격", mode="fts", top_k=2)
    brief = await _env.agent_search(agent_type=_AT, q="낙하 충격", mode="fts", top_k=2, brief=True)
    assert len(full["hits"][0]["snippet"]) > 1000
    h = brief["hits"][0]
    assert len(h["snippet"]) == 401 and h["snippet"].endswith("…") and h["snippet_chars"] == len(full["hits"][0]["snippet"])
    assert brief["truncated"] is True
    slim = await _env.agent_search(agent_type=_AT, q="낙하 충격", mode="fts", top_k=2, fields=["title", "section_id"])
    assert all(set(x) == {"record_id", "title", "section_id"} for x in slim["hits"]), slim["hits"]
    assert slim["truncated"] is False
