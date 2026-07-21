# 조회 표면 보강 검증 — 도메인 롤업(순수), discover compact, list_tags, 검색 soft-delete 필터
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api import mcp_runtime
from api.db.models import Agent, Record
from api.services import search_svc
from api.services.discover_svc import build_domain_rollup


# ---------------------------------------------------------------------------
# 순수 함수 — DB 불필요, 어느 환경에서나 실행
# ---------------------------------------------------------------------------
def test_domain_rollup_groups_by_prefix():
    agents = [
        {"agent_type": "cam-ois-control", "record_count": 30},
        {"agent_type": "cam-vcm-actuator", "record_count": 30},
        {"agent_type": "pwr-dcdc-regulator", "record_count": 25},
        {"agent_type": "misc", "record_count": 1},
    ]
    rollup = build_domain_rollup(agents)
    by_dom = {r["domain"]: r for r in rollup}
    assert by_dom["cam"] == {"domain": "cam", "agent_count": 2, "record_count": 60}
    assert by_dom["pwr"]["agent_count"] == 1
    assert by_dom["misc"]["record_count"] == 1
    # agent_count 내림차순 정렬
    assert rollup[0]["domain"] == "cam"


def test_domain_rollup_empty_and_missing_fields():
    assert build_domain_rollup([]) == []
    assert build_domain_rollup([{"agent_type": "", "record_count": 5}]) == []
    rollup = build_domain_rollup([{"agent_type": "rf-nfc"}])  # record_count 부재 → 0
    assert rollup == [{"domain": "rf", "agent_count": 1, "record_count": 0}]


# ---------------------------------------------------------------------------
# DB 픽스처 기반 — aiosqlite 환경(CI)에서 실행
# ---------------------------------------------------------------------------
async def _seed_records(maker) -> None:
    async with maker() as s:
        s.add_all(
            [
                Agent(
                    agent_type="cam-test",
                    name="CAM",
                    description="d",
                    common_tags=[],
                    data_types=["DOC"],
                ),
                Record(
                    id="DOC-HE-CAE-2026-0000000101",
                    data_type="DOC",
                    team="HE",
                    group="CAE",
                    year=2026,
                    seq=101,
                    title="살아있는 문서",
                    summary="active",
                    tags=["alive-tag"],
                    agents=["cam-test"],
                    content={"raw": "..."},
                ),
                Record(
                    id="DOC-HE-CAE-2026-0000000102",
                    data_type="DOC",
                    team="HE",
                    group="CAE",
                    year=2026,
                    seq=102,
                    title="삭제된 문서",
                    summary="deleted",
                    tags=["alive-tag", "dead-tag"],
                    agents=["cam-test"],
                    content={"raw": "..."},
                    deleted_at=datetime.now(UTC),
                ),
            ]
        )
        await s.commit()


@pytest.mark.asyncio
async def test_tag_search_excludes_soft_deleted(test_session_maker):
    await _seed_records(test_session_maker)
    async with test_session_maker() as s:
        rows, _total = await search_svc.tag_search(s, ["alive-tag"], limit=10)
    ids = {r.id for r in rows}
    assert "DOC-HE-CAE-2026-0000000101" in ids
    assert "DOC-HE-CAE-2026-0000000102" not in ids


@pytest.mark.asyncio
async def test_fts_search_excludes_soft_deleted(test_session_maker):
    await _seed_records(test_session_maker)
    async with test_session_maker() as s:
        items, _total = await search_svc.fts_search(s, "문서", limit=10)
    ids = {it["record_id"] for it in items}
    assert "DOC-HE-CAE-2026-0000000102" not in ids


@pytest.mark.asyncio
async def test_list_tags_tool(monkeypatch, test_session_maker):
    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    await _seed_records(test_session_maker)
    out = await mcp_runtime.list_tags()
    tags = {it["tag"]: it for it in out["items"]}
    # 삭제 레코드만 가진 dead-tag 는 어휘에서 제외 (_aggregate_tags 가 deleted 필터)
    assert "alive-tag" in tags
    assert "dead-tag" not in tags
    assert tags["alive-tag"]["count"] == 1
    assert tags["alive-tag"]["agents"] == ["cam-test"]


@pytest.mark.asyncio
async def test_discover_compact_replaces_agents_with_rollup(monkeypatch, test_session_maker):
    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    await _seed_records(test_session_maker)
    from api.services import discover_svc

    discover_svc.clear_cache()
    full = await mcp_runtime.discover()
    assert "agents" in full  # 기본 호출은 기존 응답 그대로(하위호환)
    discover_svc.clear_cache()
    slim = await mcp_runtime.discover(compact=True)
    assert "agents" not in slim
    assert any(d["domain"] == "cam" for d in slim["agents_by_domain"])
    # soft-delete 제외 집계 — 살아있는 레코드 1건만
    assert slim["total_records"] == 1


@pytest.mark.asyncio
async def test_list_agent_domains_tool(monkeypatch, test_session_maker):
    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    await _seed_records(test_session_maker)
    from api.services import discover_svc

    discover_svc.clear_cache()
    rollup = await mcp_runtime.list_agent_domains()
    by_dom = {r["domain"]: r for r in rollup}
    assert by_dom["cam"]["agent_count"] == 1
    assert by_dom["cam"]["record_count"] == 1  # 삭제 레코드는 count 제외
