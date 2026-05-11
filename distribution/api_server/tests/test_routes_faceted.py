"""/api/search/faceted, /api/search/by-tags 다층 필터링 테스트."""
from __future__ import annotations

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def faceted_seed(test_session_maker) -> dict[str, str]:
    """faceted 검색 시나리오 시드.

    DOC 7건 + DATA 3건, 다양한 domain/tags/agent/year/quality_score.
    """
    from api.db.models import Record

    records = []
    # 주: id 형식 — DATA_TYPE-TEAM-GROUP-YEAR-SEQ
    seed_specs = [
        # (id, dtype, year, tags, agents, domain, classification, status, quality)
        ("DOC-HE-CAE-2026-0000100001", "DOC", 2026, ["IGA", "NURBS"],
            ["iga-analyst"], "cae", "internal", "approved", 90),
        ("DOC-HE-CAE-2026-0000100002", "DOC", 2026, ["IGA", "stress"],
            ["iga-analyst", "cae-reporter"], "cae", "internal", "approved", 85),
        ("DOC-HE-CAE-2025-0000100003", "DOC", 2025, ["IGA"],
            ["iga-analyst"], "cae", "internal", "review", 70),
        ("DOC-HE-EDU-2026-0000100004", "DOC", 2026, ["lecture", "NURBS"],
            ["edu-tutor"], "lecture", "public", "approved", 60),
        ("DOC-HE-EDU-2026-0000100005", "DOC", 2026, ["lecture"],
            ["edu-tutor"], "lecture", "public", "draft", 50),
        ("DOC-HE-CAE-2026-0000100006", "DOC", 2026, ["낙하시험"],
            ["cae-reporter"], "cae", "internal", "approved", 80),
        ("DOC-HE-CAE-2026-0000100007", "DOC", 2026, ["낙하시험", "stress"],
            ["cae-reporter"], "cae", "internal", "approved", 75),
        ("DATA-HE-CAE-2026-0000100008", "DATA", 2026, ["SS400", "stress"],
            ["material-reviewer"], "material-test", "internal", "approved", 88),
        ("DATA-HE-CAE-2026-0000100009", "DATA", 2026, ["battery"],
            ["cae-reporter"], "material-test", "internal", "approved", 82),
        ("DATA-HE-CAE-2025-0000100010", "DATA", 2025, ["IGA"],
            ["iga-analyst"], "cae", "internal", "review", 65),
    ]
    for rid, dt, year, tags, agents, domain, cls, status, q in seed_specs:
        seq = int(rid.split("-")[-1])
        records.append(
            Record(
                id=rid,
                data_type=dt,
                team="HE",
                group="CAE" if "CAE" in rid else "EDU",
                year=year,
                seq=seq,
                title=f"{dt} record {rid}",
                summary=f"summary for {rid}",
                tags=tags,
                agents=agents,
                content=({"headers": ["x"], "rows": [[1]]} if dt == "DATA" else {}),
                domain=domain,
                classification=cls,
                status=status,
                quality_score=q,
            )
        )

    async with test_session_maker() as session:
        session.add_all(records)
        await session.commit()

    return {r.id: r.id for r in records}


# ===========================================================================
# /api/search/faceted
# ===========================================================================
@pytest.mark.asyncio
async def test_faceted_no_filters_returns_all_with_facets(
    db_client, faceted_seed
) -> None:
    resp = await db_client.get("/api/search/faceted")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 10
    facets = body["facets"]
    assert "data_type" in facets
    assert facets["data_type"].get("DOC", 0) == 7
    assert facets["data_type"].get("DATA", 0) == 3
    # tag facet 에 IGA 가 4건
    assert facets["tags"].get("IGA", 0) == 4
    # domain facet
    assert facets["domain"].get("cae", 0) == 6
    # status facet
    assert facets["status"].get("approved", 0) == 7


@pytest.mark.asyncio
async def test_faceted_data_type_csv(db_client, faceted_seed) -> None:
    resp = await db_client.get(
        "/api/search/faceted", params={"data_type": "DATA"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    for it in body["items"]:
        assert it["data_type"] == "DATA"
    # facet 도 좁혀진 결과 위에서 산출 — DOC=0
    assert body["facets"]["data_type"].get("DOC", 0) == 0
    assert body["facets"]["data_type"].get("DATA", 0) == 3


@pytest.mark.asyncio
async def test_faceted_data_type_multi_csv(db_client, faceted_seed) -> None:
    resp = await db_client.get(
        "/api/search/faceted", params={"data_type": "DOC,DATA"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 10


@pytest.mark.asyncio
async def test_faceted_tag_filter(db_client, faceted_seed) -> None:
    resp = await db_client.get(
        "/api/search/faceted", params={"tags": "IGA"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 4  # IGA 태그 record


@pytest.mark.asyncio
async def test_faceted_agent_filter(db_client, faceted_seed) -> None:
    resp = await db_client.get(
        "/api/search/faceted", params={"agent": "iga-analyst"}
    )
    assert resp.status_code == 200
    body = resp.json()
    # iga-analyst 가 agents 에 포함된 record: 100001,100002,100003,100010 → 4
    assert body["total"] == 4


@pytest.mark.asyncio
async def test_faceted_year_range(db_client, faceted_seed) -> None:
    resp = await db_client.get(
        "/api/search/faceted",
        params={"year_from": 2026, "year_to": 2026},
    )
    assert resp.status_code == 200
    body = resp.json()
    for it in body["items"]:
        assert it["year"] == 2026


@pytest.mark.asyncio
async def test_faceted_min_quality(db_client, faceted_seed) -> None:
    resp = await db_client.get(
        "/api/search/faceted", params={"min_quality": 80}
    )
    assert resp.status_code == 200
    body = resp.json()
    for it in body["items"]:
        assert (it["quality_score"] or 0) >= 80


@pytest.mark.asyncio
async def test_faceted_combined_filters_AND(
    db_client, faceted_seed
) -> None:
    """다축 AND 조합: domain=cae & status=approved & min_quality=80."""
    resp = await db_client.get(
        "/api/search/faceted",
        params={
            "domain": "cae",
            "status": "approved",
            "min_quality": 80,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    for it in body["items"]:
        assert it["domain"] == "cae"
        assert it["status"] == "approved"
        assert (it["quality_score"] or 0) >= 80
    # 매치: 100001(90), 100002(85), 100006(80)
    assert body["total"] == 3


@pytest.mark.asyncio
async def test_faceted_classification_filter(
    db_client, faceted_seed
) -> None:
    resp = await db_client.get(
        "/api/search/faceted", params={"classification": "public"}
    )
    assert resp.status_code == 200
    body = resp.json()
    for it in body["items"]:
        assert it["classification"] == "public"
    assert body["total"] == 2  # 100004, 100005


# ===========================================================================
# /api/search/by-tags
# ===========================================================================
@pytest.mark.asyncio
async def test_by_tags_all_match_default(
    db_client, faceted_seed
) -> None:
    """match=all (default) — IGA AND NURBS 모두 가진 record 만."""
    resp = await db_client.get(
        "/api/search/by-tags", params={"tags": "IGA,NURBS"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["match"] == "all"
    # 두 태그 모두 가진 record: 100001 만
    assert body["total"] == 1
    assert body["items"][0]["id"] == "DOC-HE-CAE-2026-0000100001"


@pytest.mark.asyncio
async def test_by_tags_any_match(db_client, faceted_seed) -> None:
    """match=any — IGA OR NURBS 중 하나라도."""
    resp = await db_client.get(
        "/api/search/by-tags",
        params={"tags": "IGA,NURBS", "match": "any"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["match"] == "any"
    # IGA: 4, NURBS: 2 (100001,100004), 교집합 100001 → union=5
    assert body["total"] == 5


@pytest.mark.asyncio
async def test_by_tags_requires_tags(db_client, faceted_seed) -> None:
    resp = await db_client.get("/api/search/by-tags")
    assert resp.status_code == 422  # FastAPI required param missing


@pytest.mark.asyncio
async def test_by_tags_empty_tags_string(db_client, faceted_seed) -> None:
    resp = await db_client.get("/api/search/by-tags", params={"tags": ",,"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_by_tags_pagination(db_client, faceted_seed) -> None:
    resp = await db_client.get(
        "/api/search/by-tags",
        params={"tags": "IGA", "match": "any", "limit": 2, "offset": 0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) <= 2
    assert body["total"] == 4
