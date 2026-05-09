"""/api/data 엔드포인트 테스트.

본 라우터는 두 가지 모드를 지원한다:
1. **에이전트 모드** (Cline SR 호환) — ``agent=...`` 가 주어진 경우.
2. **DATA 카탈로그 모드** — ``agent`` 미지정 시. DATA 타입 record 만.

추가로 ``/{id}/rows``, ``/{id}/columns``, ``/{id}/aggregate`` 도 검증한다.
"""
from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio


# ===========================================================================
# 1. 에이전트 모드 (legacy) — 기존 동작 유지 확인
# ===========================================================================
@pytest.mark.asyncio
async def test_data_returns_doc_section_match(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/data",
        params={"agent": "iga-analyst", "query": "offset", "limit": 5},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent"] == "iga-analyst"
    assert body["query"] == "offset"
    assert body["total_matched"] >= 1
    matched_ids = [r["record_id"] for r in body["results"]]
    assert seed_records["rec1"] in matched_ids
    section_hits = [r for r in body["results"] if r["data_type"] == "DOC"]
    assert any(r["section_id"] for r in section_hits)


@pytest.mark.asyncio
async def test_data_filters_data_type(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/data",
        params=[
            ("agent", "iga-analyst"),
            ("data_types", "DATA"),
            ("limit", 10),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    types = {r["data_type"] for r in body["results"]}
    assert types <= {"DATA"}


@pytest.mark.asyncio
async def test_data_unknown_agent_returns_empty(db_client, seed_records) -> None:
    resp = await db_client.get(
        "/api/data",
        params={"agent": "no-such-agent", "query": "anything"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent"] == "no-such-agent"
    assert body["total_matched"] == 0
    assert body["results"] == []


# ===========================================================================
# 2. DATA 카탈로그 모드 (신규)
# ===========================================================================
@pytest_asyncio.fixture
async def seed_data_records(test_session_maker) -> dict[str, Any]:
    """DATA 타입 풀 페이로드 record 시드 (Stress-Strain Curve 가정 데이터).

    ``rule_compliant.json`` 형태와 정합 — headers/rows/units/context/
    column_descriptions 포함.
    """
    from api.db.models import Record

    headers = ["Strain", "Stress", "Region"]
    rows = [
        [0.0, 0.0, "Elastic"],
        [0.0005, 100.0, "Elastic"],
        [0.001, 200.0, "Elastic"],
        [0.00125, 250.0, "Yield"],
        [0.005, 270.0, "Yield"],
        [0.01, 300.0, "Strain Hardening"],
        [0.05, 402.0, "Strain Hardening"],
        [0.1, 441.0, "Strain Hardening"],
        [0.15, 450.0, "UTS"],
        [0.18, 438.0, "Necking"],
        [0.2, 410.0, "Fracture"],
    ]
    content = {
        "headers": headers,
        "rows": rows,
        "units": ["-", "MPa", None],
        "context": {
            "method": "ASTM E8/E8M",
            "material": "SS400",
            "condition": "상온 25°C",
        },
        "column_descriptions": {
            "Strain": "공칭 변형률 (engineering strain)",
            "Stress": "공칭 응력 (engineering stress)",
            "Region": "변형 영역",
        },
        "units_map": {"Stress": "MPa"},
    }

    async with test_session_maker() as session:
        ss = Record(
            id="DATA-HE-CAE-2026-008002",
            data_type="DATA",
            division="HE",
            team="CAE",
            year=2026,
            seq=8002,
            title="Stress-Strain Curve — SS400 가정 데이터",
            summary="SS400 인장시험 가정 데이터.",
            tags=["SS400", "StressStrain", "Tensile", "Steel"],
            agents=["material-reviewer", "cae-reporter"],
            content=content,
            domain="material-test",
            classification="internal",
            status="approved",
        )
        # 두 번째 DATA record (rows 적음, 다른 domain)
        small = Record(
            id="DATA-HE-CAE-2026-008003",
            data_type="DATA",
            division="HE",
            team="CAE",
            year=2026,
            seq=8003,
            title="짧은 측정 데이터",
            summary="3행 미니 데이터.",
            tags=["mini", "Tensile"],
            agents=["cae-reporter"],
            content={
                "headers": ["t", "v"],
                "rows": [[0.0, 1.0], [0.1, 2.0], [0.2, 3.0]],
                "units": ["s", "V"],
            },
            domain="electrical-test",
            classification="internal",
            status="draft",
        )
        # DOC record (카탈로그 모드에서 제외되어야 함)
        doc = Record(
            id="DOC-HE-CAE-2026-008004",
            data_type="DOC",
            division="HE",
            team="CAE",
            year=2026,
            seq=8004,
            title="문서 (DATA 카탈로그에서 제외)",
            summary="DOC type",
            tags=["StressStrain"],
            agents=[],
            content={"sections": []},
            domain="material-test",
        )
        session.add_all([ss, small, doc])
        await session.commit()
    return {
        "ss": "DATA-HE-CAE-2026-008002",
        "small": "DATA-HE-CAE-2026-008003",
        "doc": "DOC-HE-CAE-2026-008004",
    }


@pytest.mark.asyncio
async def test_data_catalog_no_filter_returns_only_data_type(
    db_client, seed_data_records
) -> None:
    resp = await db_client.get("/api/data")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 2
    ids = {it["id"] for it in body["items"]}
    assert seed_data_records["ss"] in ids
    assert seed_data_records["small"] in ids
    # DOC record 는 제외되어야 한다
    assert seed_data_records["doc"] not in ids
    # 카탈로그 응답 구조 검증
    ss_item = next(it for it in body["items"] if it["id"] == seed_data_records["ss"])
    assert ss_item["columns"] == ["Strain", "Stress", "Region"]
    assert ss_item["rows"] == 11
    assert ss_item["units"] == ["-", "MPa", None]
    assert ss_item["domain"] == "material-test"
    assert ss_item["context"]["material"] == "SS400"


@pytest.mark.asyncio
async def test_data_catalog_filter_by_domain(db_client, seed_data_records) -> None:
    resp = await db_client.get(
        "/api/data", params={"domain": "material-test"}
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = {it["id"] for it in body["items"]}
    assert seed_data_records["ss"] in ids
    assert seed_data_records["small"] not in ids


@pytest.mark.asyncio
async def test_data_catalog_filter_by_tags_csv(
    db_client, seed_data_records
) -> None:
    resp = await db_client.get(
        "/api/data", params={"tags": "Tensile"}
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = {it["id"] for it in body["items"]}
    assert seed_data_records["ss"] in ids
    assert seed_data_records["small"] in ids
    # AND 모드: SS400 추가하면 small 은 빠짐
    resp2 = await db_client.get(
        "/api/data", params={"tags": "SS400,Tensile"}
    )
    assert resp2.status_code == 200
    ids2 = {it["id"] for it in resp2.json()["items"]}
    assert seed_data_records["ss"] in ids2
    assert seed_data_records["small"] not in ids2


@pytest.mark.asyncio
async def test_data_catalog_min_rows(db_client, seed_data_records) -> None:
    resp = await db_client.get("/api/data", params={"min_rows": 5})
    assert resp.status_code == 200
    ids = {it["id"] for it in resp.json()["items"]}
    assert seed_data_records["ss"] in ids
    assert seed_data_records["small"] not in ids  # 3행만 있음


# ===========================================================================
# 3. /api/data/{id}/rows
# ===========================================================================
@pytest.mark.asyncio
async def test_data_rows_basic(db_client, seed_data_records) -> None:
    rid = seed_data_records["ss"]
    resp = await db_client.get(f"/api/data/{rid}/rows")
    assert resp.status_code == 200
    body = resp.json()
    assert body["record_id"] == rid
    assert body["headers"] == ["Strain", "Stress", "Region"]
    assert body["units"] == ["-", "MPa", None]
    assert body["total_rows"] == 11
    assert len(body["rows"]) == 11


@pytest.mark.asyncio
async def test_data_rows_pagination(db_client, seed_data_records) -> None:
    rid = seed_data_records["ss"]
    resp = await db_client.get(
        f"/api/data/{rid}/rows", params={"limit": 3, "offset": 2}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_rows"] == 11
    assert len(body["rows"]) == 3
    # offset 2 → 3번째 행이 [0.001, 200.0, "Elastic"]
    assert body["rows"][0][2] == "Elastic"


@pytest.mark.asyncio
async def test_data_rows_where_filter(db_client, seed_data_records) -> None:
    rid = seed_data_records["ss"]
    resp = await db_client.get(
        f"/api/data/{rid}/rows", params={"where": "Region:Yield"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_rows"] == 2
    for r in body["rows"]:
        assert r[2] == "Yield"


@pytest.mark.asyncio
async def test_data_rows_where_invalid_column(
    db_client, seed_data_records
) -> None:
    rid = seed_data_records["ss"]
    resp = await db_client.get(
        f"/api/data/{rid}/rows", params={"where": "NoSuchCol:foo"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_data_rows_404_on_unknown(db_client, seed_data_records) -> None:
    resp = await db_client.get("/api/data/DATA-XX-YY-2026-999999/rows")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_data_rows_404_on_doc_record(
    db_client, seed_data_records
) -> None:
    """DOC type record 에 대해 rows 호출 → 404."""
    rid = seed_data_records["doc"]
    resp = await db_client.get(f"/api/data/{rid}/rows")
    assert resp.status_code == 404


# ===========================================================================
# 4. /api/data/{id}/columns
# ===========================================================================
@pytest.mark.asyncio
async def test_data_columns(db_client, seed_data_records) -> None:
    rid = seed_data_records["ss"]
    resp = await db_client.get(f"/api/data/{rid}/columns")
    assert resp.status_code == 200
    body = resp.json()
    assert body["record_id"] == rid
    items = body["items"]
    cols = {i["column"]: i for i in items}
    assert set(cols) == {"Strain", "Stress", "Region"}
    assert cols["Strain"]["unit"] == "-"
    assert cols["Stress"]["unit"] == "MPa"
    assert cols["Strain"]["dtype"] in ("float", "int")
    assert cols["Region"]["dtype"] == "enum"
    assert "공칭 변형률" in cols["Strain"]["description"]


# ===========================================================================
# 5. /api/data/{id}/aggregate
# ===========================================================================
@pytest.mark.asyncio
async def test_data_aggregate_max(db_client, seed_data_records) -> None:
    rid = seed_data_records["ss"]
    resp = await db_client.get(
        f"/api/data/{rid}/aggregate",
        params={"op": "max", "column": "Stress"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["op"] == "max"
    assert body["column"] == "Stress"
    assert body["result"] == 450.0
    assert body["unit"] == "MPa"


@pytest.mark.asyncio
async def test_data_aggregate_avg(db_client, seed_data_records) -> None:
    rid = seed_data_records["ss"]
    resp = await db_client.get(
        f"/api/data/{rid}/aggregate",
        params={"op": "avg", "column": "Strain"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["op"] == "avg"
    assert body["result"] is not None
    assert 0 < body["result"] < 1.0


@pytest.mark.asyncio
async def test_data_aggregate_count(db_client, seed_data_records) -> None:
    rid = seed_data_records["ss"]
    resp = await db_client.get(
        f"/api/data/{rid}/aggregate", params={"op": "count"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["op"] == "count"
    assert body["result"] == 11


@pytest.mark.asyncio
async def test_data_aggregate_group_by(db_client, seed_data_records) -> None:
    rid = seed_data_records["ss"]
    resp = await db_client.get(
        f"/api/data/{rid}/aggregate",
        params={"op": "max", "column": "Stress", "group_by": "Region"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["group_by"] == "Region"
    rows = body["result"]
    by_region = {r["Region"]: r["max_Stress"] for r in rows}
    # UTS 최대 = 450, Strain Hardening 최대 = 441
    assert by_region["UTS"] == 450.0
    assert by_region["Strain Hardening"] == 441.0
    assert by_region["Elastic"] == 200.0


@pytest.mark.asyncio
async def test_data_aggregate_invalid_op(db_client, seed_data_records) -> None:
    rid = seed_data_records["ss"]
    resp = await db_client.get(
        f"/api/data/{rid}/aggregate",
        params={"op": "median", "column": "Stress"},
    )
    # FastAPI Literal validation → 422
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_data_aggregate_missing_column_for_non_count(
    db_client, seed_data_records
) -> None:
    rid = seed_data_records["ss"]
    resp = await db_client.get(
        f"/api/data/{rid}/aggregate", params={"op": "avg"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_data_aggregate_unknown_column(
    db_client, seed_data_records
) -> None:
    rid = seed_data_records["ss"]
    resp = await db_client.get(
        f"/api/data/{rid}/aggregate",
        params={"op": "max", "column": "NoSuch"},
    )
    assert resp.status_code == 422
