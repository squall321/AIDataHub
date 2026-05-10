"""``/api/meta/options`` — 클라이언트 메타 카탈로그 검증."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_options_returns_all_keys(db_client) -> None:
    """응답 top-level 키 스냅샷 — 확장 폼이 의존하는 모든 필드가 존재해야 함."""
    resp = await db_client.get("/api/meta/options")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    expected_keys = {
        "version",
        "teams",
        "groups",
        "agents",
        "classifications",
        "statuses",
        "derivations",
        "languages",
        "data_types",
        "supported_extensions",
        "max_upload_mb",
        "allow_custom",
    }
    actual_keys = set(body.keys())
    missing = expected_keys - actual_keys
    assert not missing, f"missing keys: {missing}"

    # version 형식 확인
    assert body["version"] == "1.0"

    # 정적 옵션 sanity check
    assert "internal" in body["classifications"]
    assert "draft" in body["statuses"]
    assert "original" in body["derivations"]
    assert "ko" in body["languages"]
    assert "DOC" in body["data_types"]
    assert isinstance(body["max_upload_mb"], int)
    assert isinstance(body["allow_custom"], dict)
    assert body["allow_custom"]["team"] is False
    assert body["allow_custom"]["group"] is True


@pytest.mark.asyncio
async def test_options_supported_extensions_includes_pdf(db_client) -> None:
    resp = await db_client.get("/api/meta/options")
    assert resp.status_code == 200
    exts = resp.json()["supported_extensions"]
    # 핵심 확장자 집합이 모두 포함되어야 한다.
    for required in (".docx", ".pdf", ".pptx", ".md", ".markdown", ".xlsx"):
        assert required in exts, f"{required} missing from supported_extensions"


@pytest.mark.asyncio
async def test_options_cache_control_header(db_client) -> None:
    """``Cache-Control: public, max-age=300`` 헤더 보강 검증."""
    resp = await db_client.get("/api/meta/options")
    assert resp.status_code == 200
    cc = resp.headers.get("cache-control") or resp.headers.get("Cache-Control")
    assert cc is not None, "Cache-Control header missing"
    assert "max-age=300" in cc
    assert "public" in cc


@pytest.mark.asyncio
async def test_options_teams_and_groups_consistent(db_client) -> None:
    """``teams`` 의 모든 코드가 ``groups`` dict 의 키로 등장해야 한다."""
    resp = await db_client.get("/api/meta/options")
    body = resp.json()
    groups = body["groups"]
    for div in body["teams"]:
        assert div in groups, f"team {div!r} missing in groups mapping"
        assert isinstance(groups[div], list)
        assert all(isinstance(t, str) for t in groups[div])


@pytest.mark.asyncio
async def test_options_agents_match_seeded(db_client, test_session_maker) -> None:
    """``api.seed`` 의 STANDARD_AGENTS 5종을 등록한 뒤 응답 ``agents`` 와 매칭."""
    from api.db.models import Agent
    from api.seed.agents_data import STANDARD_AGENTS

    async with test_session_maker() as s:
        for spec in STANDARD_AGENTS:
            s.add(
                Agent(
                    agent_type=spec["agent_type"],
                    name=spec["name"],
                    description=spec["description"],
                    common_tags=list(spec.get("common_tags", [])),
                    data_types=list(spec.get("data_types", [])),
                )
            )
        await s.commit()

    resp = await db_client.get("/api/meta/options")
    assert resp.status_code == 200
    agents = resp.json()["agents"]
    types = {a["agent_type"] for a in agents}
    seeded_types = {a["agent_type"] for a in STANDARD_AGENTS}
    assert seeded_types.issubset(types), (
        f"seeded agent_types {seeded_types - types} missing"
    )
    # data_types 직렬화 확인
    iga = next(a for a in agents if a["agent_type"] == "iga-analyst")
    assert "DOC" in iga["data_types"]
    assert iga["name"] == "IGA 해석 분석가"
