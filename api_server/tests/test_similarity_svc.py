# similarity_svc(유사도 제안형 자동분류) 단위 테스트.
"""_signature(순수) + suggest_by_similarity 집계 로직 검증.

집계: doc_type/graph_type 다수결 + tags 합집합 + team/group 은 needs_human 후보.
SQLite 테스트라 on-the-fly 폴백 경로. HashEmbedder(결정론적)로 동일 시그니처=동일 벡터.
"""
from __future__ import annotations

import pytest

from api.services import similarity_svc as sim


# ── _signature (순수) ─────────────────────────────────────────────
def test_signature_title_and_headers():
    s = sim._signature(title="SUS304", headers=["strain", "stress"])
    assert "SUS304" in s and "strain" in s and "stress" in s


def test_signature_caption_fallback():
    s = sim._signature(caption="cap only")
    assert s == "cap only"


def test_signature_empty():
    assert sim._signature() == "(empty)"


# ── suggest_by_similarity (집계 로직, HashEmbedder) ────────────────
async def _seed(session, rid, **over):
    from api.db.models import Record

    # title 을 query caption 과 동일("인장")하게 둬 _record_signature(title 우선)와
    # query _signature(caption)가 같은 문자열 → hash embedder 동일 벡터 → score≈1.0.
    base = dict(
        id=rid, data_type="DATA", team="HE", group="CAE", year=2026, seq=1,
        title=over.pop("title", "인장"), summary="", tags=[], agents=[],
        content=over.pop("content", {"caption": "인장", "headers": ["strain", "stress"]}),
    )
    base.update(over)
    session.add(Record(**base))
    await session.flush()


@pytest.mark.asyncio
async def test_suggest_aggregates_and_splits_human(test_session, monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    # provider 캐시 초기화 (다른 테스트 잔재 방지)
    from api.services import embedding
    embedding._EMBEDDER_CACHE.clear()

    # 동일 시그니처(caption+headers) 2건 — team/group/doc_type/tags 일관
    for rid in ("DATA-HE-CAE-2026-0000000001", "DATA-HE-CAE-2026-0000000002"):
        await _seed(test_session, rid, doc_type="material_test_data", tags=["stress-strain", "material"],
                    content={"caption": "인장", "headers": ["strain", "stress"], "graph_type": "stress_strain"})

    # 같은 시그니처로 질의 → 이웃 매칭 (hash: 동일 텍스트=동일 벡터=score 1.0)
    res = await sim.suggest_by_similarity(
        test_session, caption="인장", headers=["strain", "stress"], data_type="DATA",
    )
    assert res["neighbors"], "이웃이 있어야 함"
    # AI 가 채울 제안: doc_type/graph_type/tags
    assert res["suggested"]["doc_type"]["value"] == "material_test_data"
    assert res["suggested"]["graph_type"]["value"] == "stress_strain"
    assert set(res["suggested"]["tags"]["value"]) >= {"stress-strain", "material"}
    # 사람이 정할 것: team/group 은 needs_human 후보로만 (자동 확정 X)
    assert res["needs_human"]["team"]["candidates"] == ["HE"]
    assert res["needs_human"]["group"]["candidates"] == ["CAE"]
    # suggested 에는 team/group 이 없어야 함 (B3 — 자동 확정 금지)
    assert "team" not in res["suggested"] and "group" not in res["suggested"]


@pytest.mark.asyncio
async def test_suggest_empty_corpus(test_session, monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    from api.services import embedding
    embedding._EMBEDDER_CACHE.clear()

    res = await sim.suggest_by_similarity(
        test_session, caption="첫 데이터", headers=["a"], data_type="DATA",
    )
    assert res["confidence"] == "none"
    assert res["neighbors"] == []
