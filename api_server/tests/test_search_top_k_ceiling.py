# 의미검색 top_k 천장 — 기본은 없고, AIDH_SEARCH_TOP_K_MAX 는 운영자가 낮출 때만 쓰는 천장인지
"""예전에는 AIDH_SEARCH_TOP_K_MAX 기본 100 이 호출자 몰래 걸렸다(서버 로그 INFO 한 줄뿐).

hybrid 는 top_k × 3 을 시맨틱 후보로 받으므로 top_k 34 부터 후보가 100 에서 잘렸고, 호출자가
좌석 카드 전부를 달라고 해도 100 절이 끝이었다. 1백만 토큰 창에서 건수를 정하는 것은 호출자다.
"""
from __future__ import annotations

import logging

import pytest

_N = 130


async def _seed(maker) -> None:
    from api.db.models import Record, RecordSection
    from api.services.embedding import get_embedder

    emb = get_embedder()
    async with maker() as s:
        rec = Record(
            id="DOC-HE-CAE-2026-0000000777", data_type="DOC", team="HE", group="CAE",
            year=2026, seq=777, title="많은 절", summary="", tags=[], agents=["seat-a"],
            content={"raw": "x"},
        )
        s.add(rec)
        await s.flush()
        for i in range(_N):
            t = f"절 {i} 본문 — 낙하 충격 시 코너 응력 {i}"
            s.add(RecordSection(record_id=rec.id, section_id=f"{i}", level=1, title=f"절{i}",
                                content_text=t, embedding=emb.encode(t), embedding_model=emb.name))
        await s.commit()


@pytest.fixture
def _hash(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.delenv("AIDH_SEARCH_TOP_K_MAX", raising=False)


@pytest.mark.asyncio
async def test_no_default_ceiling(_hash, test_session_maker):
    from api.services import search_svc

    await _seed(test_session_maker)
    async with test_session_maker() as s:
        hits = await search_svc.semantic_search(s, "낙하 충격", top_k=120)
    assert len(hits) == 120, f"top_k 120 을 달라 했는데 {len(hits)}건 — 기본 천장이 남아 있다"


@pytest.mark.asyncio
async def test_env_ceiling_lowers_and_warns(_hash, monkeypatch, test_session_maker, caplog):
    """운영자가 낮춘 천장은 지킨다 — 다만 INFO 가 아니라 WARNING 으로 남긴다."""
    from api.services import search_svc

    monkeypatch.setenv("AIDH_SEARCH_TOP_K_MAX", "7")
    await _seed(test_session_maker)
    caplog.set_level(logging.INFO, logger="api.services.search_svc")
    async with test_session_maker() as s:
        hits = await search_svc.semantic_search(s, "낙하 충격", top_k=20)
    assert len(hits) == 7
    warn = [r for r in caplog.records if r.levelno >= logging.WARNING and "AIDH_SEARCH_TOP_K_MAX" in r.getMessage()]
    assert warn, "천장에 잘렸는데 WARNING 이 없다"


@pytest.mark.asyncio
async def test_under_ceiling_is_untouched(_hash, monkeypatch, test_session_maker):
    """특성화 — 천장 아래 요청은 종전과 같다."""
    from api.services import search_svc

    monkeypatch.setenv("AIDH_SEARCH_TOP_K_MAX", "100")
    await _seed(test_session_maker)
    async with test_session_maker() as s:
        hits = await search_svc.semantic_search(s, "낙하 충격", top_k=10)
    assert len(hits) == 10
