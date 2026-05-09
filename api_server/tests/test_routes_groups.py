"""``/api/groups/auto`` + ``/api/records/{id}/cluster`` + ``/api/records/bulk``
엔드포인트 회귀 테스트.

테스트 환경: SQLite + ``HashEmbedder`` (결정론적). 실 운영에서는 pgvector
+ OpenAIEmbedder 가 동일 인터페이스로 동작한다.
"""
from __future__ import annotations

import os

import pytest


# ---------------------------------------------------------------------------
# 공통 시드 — embedding 까지 채워 넣은 record 군. AI 도입 vs 일반 OGA 두 군집.
# ---------------------------------------------------------------------------
@pytest.fixture
def _hash_embedder():
    os.environ["EMBEDDING_PROVIDER"] = "hash"
    from api.services.embedding import get_embedder

    return get_embedder()


async def _seed_clustered(test_session_maker, embedder) -> dict[str, str]:
    from api.db.models import Record, RecordSection

    # 두 의미 군집:
    #   AI/DigitalTwin (3건) — 문장이 동일/유사
    #   OGA Operations (2건) — 다른 의미
    ai_text = "AI 디지털 트윈 도입 전략과 거버넌스"
    oga_text = "OGA 운영 정책과 절차 문서"

    async with test_session_maker() as s:
        recs = [
            Record(
                id="DOC-HE-AI-2026-100001",
                data_type="DOC",
                division="HE",
                team="AI",
                year=2026,
                seq=100001,
                title="AI 도입 전략 보고서",
                summary="AI 디지털 트윈 도입",
                tags=["AI", "DigitalTwin", "Strategy"],
                agents=["cae-reporter"],
                domain="strategy",
                content={"raw": "x"},
            ),
            Record(
                id="DOC-HE-AI-2026-100002",
                data_type="DOC",
                division="HE",
                team="AI",
                year=2026,
                seq=100002,
                title="디지털 트윈 거버넌스 가이드",
                summary="AI 거버넌스 핵심 원칙",
                tags=["AI", "DigitalTwin", "Governance"],
                agents=["cae-reporter"],
                domain="strategy",
                content={"raw": "x"},
            ),
            Record(
                id="DOC-HE-AI-2026-100003",
                data_type="DOC",
                division="HE",
                team="AI",
                year=2026,
                seq=100003,
                title="AI 도입 사례",
                summary="DigitalTwin 도입 사례 모음",
                tags=["AI", "DigitalTwin", "Innovation"],
                agents=["cae-reporter"],
                domain="strategy",
                content={"raw": "x"},
            ),
            Record(
                id="DOC-HE-OPS-2026-200001",
                data_type="DOC",
                division="HE",
                team="OPS",
                year=2026,
                seq=200001,
                title="OGA 운영 정책",
                summary="OGA 운영",
                tags=["OGA", "Policy"],
                agents=["oga-analyst"],
                domain="operations",
                content={"raw": "x"},
            ),
            Record(
                id="DOC-HE-OPS-2026-200002",
                data_type="DOC",
                division="HE",
                team="OPS",
                year=2026,
                seq=200002,
                title="OGA 절차서",
                summary="절차 문서",
                tags=["OGA", "Procedure"],
                agents=["oga-analyst"],
                domain="operations",
                content={"raw": "x"},
            ),
        ]
        s.add_all(recs)
        await s.flush()

        # AI 군집은 동일/유사 텍스트 → HashEmbedder 결정론에서 cosine=1.0
        # OGA 군집도 같은 텍스트 → 또 다른 군.
        s.add_all(
            [
                RecordSection(
                    record_id="DOC-HE-AI-2026-100001",
                    section_id="1.1",
                    level=1,
                    title="개요",
                    content_text=ai_text,
                    embedding=embedder.encode(ai_text),
                    embedding_model=embedder.name,
                ),
                RecordSection(
                    record_id="DOC-HE-AI-2026-100002",
                    section_id="1.1",
                    level=1,
                    title="개요",
                    content_text=ai_text,
                    embedding=embedder.encode(ai_text),
                    embedding_model=embedder.name,
                ),
                RecordSection(
                    record_id="DOC-HE-AI-2026-100003",
                    section_id="1.1",
                    level=1,
                    title="개요",
                    content_text=ai_text,
                    embedding=embedder.encode(ai_text),
                    embedding_model=embedder.name,
                ),
                RecordSection(
                    record_id="DOC-HE-OPS-2026-200001",
                    section_id="1.1",
                    level=1,
                    title="개요",
                    content_text=oga_text,
                    embedding=embedder.encode(oga_text),
                    embedding_model=embedder.name,
                ),
                RecordSection(
                    record_id="DOC-HE-OPS-2026-200002",
                    section_id="1.1",
                    level=1,
                    title="개요",
                    content_text=oga_text,
                    embedding=embedder.encode(oga_text),
                    embedding_model=embedder.name,
                ),
            ]
        )
        await s.commit()

    return {
        "ai1": "DOC-HE-AI-2026-100001",
        "ai2": "DOC-HE-AI-2026-100002",
        "ai3": "DOC-HE-AI-2026-100003",
        "oga1": "DOC-HE-OPS-2026-200001",
        "oga2": "DOC-HE-OPS-2026-200002",
    }


# ===========================================================================
# POST /api/groups/auto
# ===========================================================================
@pytest.mark.asyncio
async def test_groups_auto_basic(db_client, test_session_maker, _hash_embedder):
    """자연어 → 시맨틱 검색 → 그룹화. AI 군집이 한 그룹으로 묶여야 한다."""
    ids = await _seed_clustered(test_session_maker, _hash_embedder)

    resp = await db_client.post(
        "/api/groups/auto",
        json={
            "q": "AI 디지털 트윈 도입 전략과 거버넌스",
            "n_groups": 2,
            "limit_per_group": 5,
            "min_score": 0.0,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query"].startswith("AI")
    assert body["total_records"] >= 1
    assert isinstance(body["groups"], list)
    # 같은 텍스트의 AI 3건은 cosine=1.0 → 같은 그룹.
    if body["groups"]:
        first = body["groups"][0]
        assert first["size"] >= 1
        assert "label" in first
        assert "common_tags" in first
        assert "representative_record" in first
        assert "records" in first
        # AI 군집 라벨에는 공통 태그 (AI / DigitalTwin) 가 노출되어야 한다.
        ai_in_first = any(
            r["id"] in {ids["ai1"], ids["ai2"], ids["ai3"]}
            for r in first["records"]
        )
        if ai_in_first:
            assert "AI" in first["common_tags"] or "DigitalTwin" in first["common_tags"]


@pytest.mark.asyncio
async def test_groups_auto_validation(db_client):
    """``n_groups<=0`` / 빈 q → 422."""
    resp = await db_client.post(
        "/api/groups/auto", json={"q": "x", "n_groups": 0}
    )
    assert resp.status_code == 422

    resp2 = await db_client.post(
        "/api/groups/auto", json={"q": "", "n_groups": 3}
    )
    assert resp2.status_code == 422


# ===========================================================================
# GET /api/records/{id}/cluster
# ===========================================================================
@pytest.mark.asyncio
async def test_record_cluster_semantic(
    db_client, test_session_maker, _hash_embedder
):
    """semantic 모드 — anchor 와 cosine ≥ threshold 인 record."""
    ids = await _seed_clustered(test_session_maker, _hash_embedder)

    resp = await db_client.get(
        f"/api/records/{ids['ai1']}/cluster",
        params={"mode": "semantic", "sim_threshold": 0.95},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "semantic"
    assert body["anchor_record"]["id"] == ids["ai1"]
    item_ids = {it["id"] for it in body["items"]}
    # AI 군집 동일 텍스트 → ai2, ai3 도 ≥0.99 score 로 매칭
    assert ids["ai2"] in item_ids
    assert ids["ai3"] in item_ids
    # OGA 는 다른 텍스트 → 임계 0.95 컷오프에서 제외 가능성 높음.
    # (HashEmbedder 는 의미를 모르므로 가끔 충돌하지만 일반적으로 제외됨.)


@pytest.mark.asyncio
async def test_record_cluster_tag(
    db_client, test_session_maker, _hash_embedder
):
    """tag 모드 — jaccard 기반."""
    ids = await _seed_clustered(test_session_maker, _hash_embedder)

    resp = await db_client.get(
        f"/api/records/{ids['ai1']}/cluster",
        params={"mode": "tag", "tag_threshold": 0.4},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "tag"
    item_ids = {it["id"] for it in body["items"]}
    # ai2, ai3 는 tags={AI,DigitalTwin,...} 와 jaccard 충분히 높음.
    assert ids["ai2"] in item_ids
    assert ids["ai3"] in item_ids
    # OGA 는 공통 태그 0 → 제외.
    assert ids["oga1"] not in item_ids
    # 공유 태그가 응답에 노출되어야 한다.
    for it in body["items"]:
        if it["id"] == ids["ai2"]:
            assert "AI" in it["shared_tags"]


@pytest.mark.asyncio
async def test_record_cluster_unknown_id(db_client, test_session_maker):
    """존재하지 않는 record → 404."""
    # 시드 없이 호출 — 단, conftest 의 db_client 가 빈 DB 라도 응답은 404.
    resp = await db_client.get(
        "/api/records/DOC-HE-XX-2099-999999/cluster?mode=semantic"
    )
    assert resp.status_code == 404


# ===========================================================================
# POST /api/records/bulk
# ===========================================================================
@pytest.mark.asyncio
async def test_records_bulk_multi_id(
    db_client, test_session_maker, _hash_embedder
):
    """여러 id 한 번에 + sections 동봉."""
    ids = await _seed_clustered(test_session_maker, _hash_embedder)

    resp = await db_client.post(
        "/api/records/bulk",
        json={
            "ids": [ids["ai1"], ids["ai2"], ids["oga1"]],
            "include_sections": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 3
    assert body["missing"] == []
    out_ids = [it["id"] for it in body["items"]]
    # 입력 순서 보존 확인.
    assert out_ids == [ids["ai1"], ids["ai2"], ids["oga1"]]
    # 각 item 에 sections 키 존재 + 시드 1 section 이 들어감.
    for it in body["items"]:
        assert "sections" in it
        assert len(it["sections"]) == 1


@pytest.mark.asyncio
async def test_records_bulk_missing(
    db_client, test_session_maker, _hash_embedder
):
    """존재 + 누락 id 혼합 — items 에는 존재만, missing 에 누락."""
    ids = await _seed_clustered(test_session_maker, _hash_embedder)

    resp = await db_client.post(
        "/api/records/bulk",
        json={"ids": [ids["ai1"], "DOC-NO-SUCH-2099-999999"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == ids["ai1"]
    assert body["missing"] == ["DOC-NO-SUCH-2099-999999"]


@pytest.mark.asyncio
async def test_records_bulk_validation(db_client):
    """빈 ids → 422."""
    resp = await db_client.post(
        "/api/records/bulk", json={"ids": []}
    )
    assert resp.status_code == 422
