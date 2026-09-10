# 바인딩이 0건인 좌석은 전역 검색으로 넓히지 않고 거절한다 — 남의 문서를 자기 근거로 주지 않게
from __future__ import annotations

import pytest

from api import mcp_runtime
from api.db.models import Agent


async def _seed_empty_agent(maker) -> None:
    """레코드가 하나도 바인딩되지 않은 좌석. 신설 좌석에서 실제로 나오는 상태다."""
    async with maker() as s:
        s.add(
            Agent(
                agent_type="sim-thermal-sed",
                name="열·SED",
                description="열충격 SED",
                common_tags=["열충격"],
                data_types=["DOC"],
            )
        )
        await s.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fts", "semantic", "hybrid", "tag"])
async def test_agent_search_refuses_when_the_seat_has_no_records(
    monkeypatch, test_session_maker, mode
):
    """빈 범위를 '범위 제한 없음'으로 접으면 그 좌석이 **남의 문서**를 근거로 받는다.

    실측(2026-09-10) — sim-thermal-sed(바인딩 0건)에 hybrid 로 물으니 sw-app-messages 의
    MMS 문서 10건이 refused:false 로 돌아왔다. 열·구조 좌석이 소프트웨어 메시징 문서를
    자기 근거로 인용하게 된다. 모드와 무관하게 거절이어야 한다.
    """
    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    await _seed_empty_agent(test_session_maker)

    # 검색이 아예 불려서는 안 된다 — 불렸다면 범위가 전역으로 새어 나간 것이다.
    async def _boom(*a, **k):  # pragma: no cover - 불리면 그 자체가 실패다
        raise AssertionError("바인딩 0건 좌석인데 검색이 실행됐다 — 범위가 전역으로 샜다")

    from api.services import search_svc

    for name in ("fts_search", "semantic_search", "hybrid_search", "tag_search"):
        monkeypatch.setattr(search_svc, name, _boom, raising=False)

    out = await mcp_runtime.agent_search(
        agent_type="sim-thermal-sed", q="MMS 첨부 자동 다운로드 용량 제한", mode=mode
    )
    assert out["refused"] is True
    assert out["hits"] == [] and out["hit_count"] == 0
    assert "0 mapped records" in out["applied_config"]["reason"]


@pytest.mark.asyncio
async def test_standalone_tools_do_not_widen_an_empty_agent_scope(monkeypatch, test_session_maker):
    """독립 MCP 도구(semantic_search·hybrid_search)도 같다.

    `record_ids` 는 agent_type 이 주어졌을 때만 채워지므로 빈 리스트는 '사용자가 안 줬다'가
    아니라 '그 좌석이 0건'이다. `or None` 으로 접으면 search_svc 의 빈-범위 가드가 도달
    불가가 되어 전역 검색이 된다.
    """
    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    await _seed_empty_agent(test_session_maker)

    seen: dict[str, object] = {}

    async def _fake_semantic(_s, q, *, top_k, data_types=None, record_ids=None):
        seen["record_ids"] = record_ids
        return []

    async def _fake_hybrid(_s, q, *, top_k, data_types=None, record_ids=None, **kw):
        seen["record_ids"] = record_ids
        return []

    from api.services import search_svc

    monkeypatch.setattr(search_svc, "semantic_search", _fake_semantic)
    monkeypatch.setattr(search_svc, "hybrid_search", _fake_hybrid)

    await mcp_runtime.semantic_search(q="아무거나", agent_type="sim-thermal-sed")
    assert seen["record_ids"] == [], "빈 좌석 범위가 None(전역)으로 접혔다"

    seen.clear()
    await mcp_runtime.hybrid_search(q="아무거나", agent_type="sim-thermal-sed")
    assert seen["record_ids"] == [], "빈 좌석 범위가 None(전역)으로 접혔다"


@pytest.mark.asyncio
async def test_standalone_tools_still_search_globally_without_agent_type(
    monkeypatch, test_session_maker
):
    """agent_type 을 안 주면 종전대로 전역이다 — 이번 수정이 그것까지 좁히면 안 된다."""
    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    seen: dict[str, object] = {"record_ids": "unset"}

    async def _fake_semantic(_s, q, *, top_k, data_types=None, record_ids=None):
        seen["record_ids"] = record_ids
        return []

    from api.services import search_svc

    monkeypatch.setattr(search_svc, "semantic_search", _fake_semantic)
    await mcp_runtime.semantic_search(q="아무거나")
    assert seen["record_ids"] is None
