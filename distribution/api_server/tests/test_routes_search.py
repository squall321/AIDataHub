"""/api/search 엔드포인트 테스트."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_search_tag_mode(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/search",
        params=[("mode", "tag"), ("tags", "IGA")],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "tag"
    # IGA 태그를 포함한 레코드: rec1, rec2
    ids = {r["id"] for r in body["items"]}
    assert seed_records["rec1"] in ids
    assert seed_records["rec2"] in ids


@pytest.mark.asyncio
async def test_search_tag_requires_tags(db_client, seed_records) -> None:
    resp = await db_client.get("/api/search", params={"mode": "tag"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_search_fts_mode(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/search",
        params={"mode": "fts", "q": "offset"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "fts"
    assert body["total"] >= 1
    record_ids = {item["record_id"] for item in body["items"]}
    assert seed_records["rec1"] in record_ids


@pytest.mark.asyncio
async def test_search_fts_requires_q(db_client, seed_records) -> None:
    resp = await db_client.get("/api/search", params={"mode": "fts"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_search_semantic_empty_corpus(db_client) -> None:
    """seed 가 임베딩이 없는 상태에서도 200 + 빈 결과를 반환한다.

    임베딩 컬럼이 ``NULL`` 인 섹션은 후보에서 제외되므로 미백필 환경에서는
    items=[] 가 정상.
    """
    resp = await db_client.get(
        "/api/search",
        params={"mode": "semantic", "q": "anything"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "semantic"
    assert body["q"] == "anything"
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_search_semantic_requires_q(db_client) -> None:
    resp = await db_client.get("/api/search", params={"mode": "semantic"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_search_semantic_with_embeddings(
    db_client, test_session_maker
) -> None:
    """embedding 채워진 섹션은 시맨틱 검색에서 score 와 함께 반환된다."""
    import os
    os.environ["EMBEDDING_PROVIDER"] = "hash"

    from api.db.models import Record, RecordSection
    from api.services.embedding import get_embedder

    embedder = get_embedder()
    text = "AI 디지털 트윈 도입 전략"

    async with test_session_maker() as s:
        rec = Record(
            id="DOC-HE-AI-2026-099001",
            data_type="DOC",
            division="HE",
            team="AI",
            year=2026,
            seq=99001,
            title="smoke",
            summary="smoke",
            tags=["smoke"],
            agents=[],
            content={"raw": "x"},
        )
        s.add(rec)
        await s.flush()
        sec = RecordSection(
            record_id=rec.id,
            section_id="1.1",
            level=2,
            title="개요",
            content_text=text,
            embedding=embedder.encode(text),
            embedding_model=embedder.name,
        )
        s.add(sec)
        await s.commit()

    resp = await db_client.get(
        "/api/search",
        params={"mode": "semantic", "q": text, "limit": 3},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "semantic"
    assert body["total"] >= 1
    top = body["items"][0]
    assert top["record_id"] == "DOC-HE-AI-2026-099001"
    assert top["score"] >= 0.99  # 동일 텍스트 → 결정론적 cosine 1.0
