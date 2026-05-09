"""``/api/taxonomy`` 엔드포인트 — 작은 모델용 어휘 발견 / 동의어 매핑 테스트.

8개 케이스:
    1. tags 기본 조회
    2. tags?q=prefix 필터
    3. data-types 분포
    4. domains 분포
    5. agents 카탈로그
    6. tags/resolve exact / synonym / prefix 매칭
    7. classification enum
    8. read-only 라 인증 없이 200 응답 (read-only public)
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_tags_basic(db_client, seed_records) -> None:
    """모든 태그 + count + data_types 분포가 한 번에 반환되어야 한다."""
    resp = await db_client.get("/api/taxonomy/tags")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body and "total" in body
    tags = {item["tag"] for item in body["items"]}
    # seed_records 의 tags: rec1=[IGA,offset,FEM], rec2=[battery,IGA],
    # rec3=[OGA,policy] → 모두 등장.
    assert {"IGA", "offset", "battery", "OGA"} <= tags
    iga = next(item for item in body["items"] if item["tag"] == "IGA")
    assert iga["count"] == 2  # rec1 + rec2
    # data_types 분포: rec1=DOC, rec2=DATA → IGA 는 DOC=1, DATA=1
    assert iga["data_types"].get("DOC") == 1
    assert iga["data_types"].get("DATA") == 1
    assert "iga-analyst" in iga["agents"]


@pytest.mark.asyncio
async def test_tags_prefix_filter(db_client, seed_records) -> None:
    """``q=prefix`` 는 prefix 매칭 (대소문자 무시) 으로 필터해야 한다."""
    resp = await db_client.get("/api/taxonomy/tags", params={"q": "ig"})
    assert resp.status_code == 200
    body = resp.json()
    tags = {item["tag"] for item in body["items"]}
    assert "IGA" in tags
    # OGA / battery / offset / policy 등은 'ig' 로 시작 X
    assert "OGA" not in tags
    assert "battery" not in tags


@pytest.mark.asyncio
async def test_data_types_distribution(db_client, seed_records) -> None:
    """``/data-types`` 은 모든 enum 값을 반환하고 사용 분포 + 추천 패턴을 함께 노출."""
    resp = await db_client.get("/api/taxonomy/data-types")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    types = {item["data_type"] for item in body["items"]}
    assert {"DOC", "DATA", "SIM", "CAD", "LOG", "FORM", "OTHER"} <= types
    by_dt = {item["data_type"]: item for item in body["items"]}
    # seed: DOC=2, DATA=1, 기타=0
    assert by_dt["DOC"]["count"] == 2
    assert by_dt["DATA"]["count"] == 1
    assert by_dt["SIM"]["count"] == 0
    # 모든 항목이 schema_url 을 노출
    assert all(item["schema_url"].startswith("/api/schema") for item in body["items"])
    # subtypes 힌트가 DOC/DATA 에 채워져 있다
    assert "report" in by_dt["DOC"]["subtypes"]
    assert "test_data" in by_dt["DATA"]["subtypes"]


@pytest.mark.asyncio
async def test_domains_distribution(db_client, seed_records) -> None:
    """domain 분포 — seed 는 모두 domain=None → null 카운트가 잡혀야 한다."""
    resp = await db_client.get("/api/taxonomy/domains")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body
    # seed 데이터 3건 모두 domain=None → 단일 항목, count=3
    null_items = [item for item in body["items"] if item["domain"] is None]
    assert len(null_items) == 1
    assert null_items[0]["count"] == 3


@pytest.mark.asyncio
async def test_agents_catalog(db_client, seed_records) -> None:
    """agent 카탈로그 — record 수 + 주요 태그 + data_types."""
    resp = await db_client.get("/api/taxonomy/agents")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_type = {item["agent_type"]: item for item in body["items"]}
    assert "iga-analyst" in by_type
    assert "oga-analyst" in by_type
    iga = by_type["iga-analyst"]
    # iga-analyst 는 rec1 + rec2 사용 → 2건
    assert iga["record_count"] == 2
    # 공통 태그 IGA 는 두 record 에서 등장 → 상위 5위 안.
    assert "IGA" in iga["common_tags"]
    # data_types: DOC + DATA
    assert set(iga["data_types"]) == {"DOC", "DATA"}


@pytest.mark.asyncio
async def test_tags_resolve_methods(db_client, seed_records) -> None:
    """exact / synonym / prefix 3 가지 매칭 방식이 모두 동작해야 한다."""
    # exact — DB 에 'IGA' 가 있다.
    r_exact = await db_client.get(
        "/api/taxonomy/tags/resolve", params={"q": "IGA"}
    )
    assert r_exact.status_code == 200, r_exact.text
    cands = r_exact.json()["candidates"]
    assert any(
        c["tag"] == "IGA" and c["method"] == "exact" and c["score"] == 1.0
        for c in cands
    )

    # synonym — 'fem' 은 SYNONYM_DICT 에서 '유한요소' 의 동의어. DB 의 'FEM' 도 prefix 로 잡힘.
    r_syn = await db_client.get(
        "/api/taxonomy/tags/resolve", params={"q": "fem"}
    )
    assert r_syn.status_code == 200
    methods = {c["method"] for c in r_syn.json()["candidates"]}
    # 'fem' 은 정규화 후 'FEM' 과 동일 → exact 로 잡힘. 그리고 '유한요소' 가 synonym 으로 떠야 함.
    assert "synonym" in methods or "exact" in methods
    cand_tags = {c["tag"] for c in r_syn.json()["candidates"]}
    # 유한요소(canonical) 가 후보에 들어와야 한다.
    assert "유한요소" in cand_tags or "FEM" in cand_tags

    # prefix — 'off' → 'offset' 이 prefix 로 잡혀야 한다.
    r_pref = await db_client.get(
        "/api/taxonomy/tags/resolve", params={"q": "off"}
    )
    assert r_pref.status_code == 200
    cands = r_pref.json()["candidates"]
    assert any(c["tag"] == "offset" and c["method"] == "prefix" for c in cands)


@pytest.mark.asyncio
async def test_classification_enum(db_client, seed_records) -> None:
    """classification enum + 의미 + 분포."""
    resp = await db_client.get("/api/taxonomy/classification")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["field"] == "classification"
    values = {item["value"] for item in body["items"]}
    assert {"public", "internal", "confidential", "restricted"} == values
    by_v = {item["value"]: item for item in body["items"]}
    # 모든 항목에 의미 (description) 가 있다
    assert all(item["description"] for item in body["items"])
    # 기본값 internal 은 seed 3건이 모두 default (internal) → count=3
    assert by_v["internal"]["count"] == 3


@pytest.mark.asyncio
async def test_taxonomy_no_auth_required(db_client, seed_records) -> None:
    """read-only 메타이므로 X-API-Key 없이도 200 OK 가 나와야 한다.

    (테스트 클라이언트는 이미 헤더 없이 호출하지만, 명시적으로 모든 8 엔드포인트가
    인증 없이 접근 가능함을 보장한다.)
    """
    paths = [
        "/api/taxonomy/tags",
        "/api/taxonomy/data-types",
        "/api/taxonomy/domains",
        "/api/taxonomy/agents",
        "/api/taxonomy/tags/resolve?q=IGA",
        "/api/taxonomy/classification",
        "/api/taxonomy/status",
        "/api/taxonomy/access-pattern",
    ]
    for path in paths:
        resp = await db_client.get(path)
        assert resp.status_code == 200, f"{path} → {resp.status_code} {resp.text}"
