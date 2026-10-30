# agent_search as_of — 그 날 뒤로 날짜(valid_from)가 찍힌 카드는 빼고, 날짜 없는 카드는 남기고 센다
"""AD-07 — agent_search 가 as_of 를 받지 않아 소급 심의가 그 시점 뒤에 생긴 카드까지 근거로 썼다.

믿을 날짜는 records.valid_from(동기화가 원천 날짜로 채운다)이다. created_at 은 허브에 적재한 날이라
쓰지 않는다. 날짜 없는 카드는 남기고 센다 — 업로드 · 가져오기 카드는 올린 쪽이 날짜를 줄 때만 차고
논문 카드 길은 주지 않아, 빼면 지식만 가진 좌석이 통째로 빈다(HWAXPortal docs/delib-engine-feedback
D-15). as_of 를 안 주면 종전 그대로다.
"""
from __future__ import annotations

import json
import re
from datetime import date

import pytest

_AT = "rel-drop-impact"
# 좌석 카드 넷 — as_of 2025-06-01 기준 하루 전 · 같은 날(경계) · 하루 뒤 · 날짜 없음.
_BEFORE, _SAME, _AFTER, _UNDATED = (f"DOC-HE-CAE-2026-00000007{i:02d}" for i in range(4))
_DATES: dict[str, date | None] = {
    _BEFORE: date(2025, 5, 31), _SAME: date(2025, 6, 1), _AFTER: date(2025, 6, 2), _UNDATED: None,
}
# as_of 를 안 줬을 때 applied_config 의 칸 — 이 수정 앞과 같아야 한다(새 칸이 새지 않는다).
_KEYS_WITHOUT_AS_OF = {
    "top_k", "top_k_source", "score_threshold", "threshold_basis", "data_type_filter",
    "tag_boost_applied", "max_depth", "required_tags", "excluded_tags", "agent_record_scope",
}
# HWAXAgentServer card_review.py 의 판정 그대로 — 이 말이 보이면 '묶인 자료 0건' 으로 읽는다.
_EMPTY_SCOPE_RE = re.compile(r"has 0 mapped records|0 mapped")


async def _seed(maker, dates: dict[str, date | None] = _DATES) -> None:
    from api.db.models import Agent, Record, RecordSection
    from api.services.embedding import get_embedder

    emb = get_embedder()
    async with maker() as s:
        s.add(Agent(agent_type=_AT, name="낙하", description="낙하 충격", common_tags=["낙하"],
                    data_types=["DOC"], retrieval_config={"top_k": 10}))
        for i, (rid, vf) in enumerate(dates.items()):
            s.add(Record(id=rid, data_type="DOC", team="HE", group="CAE", year=2026,
                         seq=700 + i, title=f"낙하카드{i}", summary=f"낙하 카드 {i}",
                         tags=["낙하", "충격"], agents=[_AT], content={"raw": "x"}, valid_from=vf))
            await s.flush()
            t = f"낙하 충격 코너 응력 카드 {i} 본문"
            s.add(RecordSection(record_id=rid, section_id="1", level=1, title=f"카드{i}",
                                content_text=t, embedding=emb.encode(t), embedding_model=emb.name))
        await s.commit()


@pytest.fixture
def _env(monkeypatch, test_session_maker):
    from api import mcp_runtime

    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.delenv("AIDH_SEARCH_TOP_K_MAX", raising=False)
    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    return mcp_runtime


def _ids(out: dict) -> set[str]:
    return {h["record_id"] for h in out["hits"]}


def _q(mode: str) -> str:
    return "낙하,충격" if mode == "tag" else "낙하"


def _counts(out: dict) -> tuple[int, int, int]:
    """(그 날 뒤라 뺀 수, 날짜 없이 남긴 수, 찾은 범위의 카드 수)."""
    cfg = out["applied_config"]
    return cfg["as_of_excluded_later"], cfg["as_of_undated_kept"], cfg["agent_record_scope"]


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_without_as_of_nothing_changes(_env, test_session_maker, blank):
    """안 주거나 빈 글이면 거르지 않는다 — 결과도, applied_config 의 칸도 종전 그대로다."""
    await _seed(test_session_maker)
    plain = await _env.agent_search(agent_type=_AT, q="낙하 충격", mode="semantic")
    out = await _env.agent_search(agent_type=_AT, q="낙하 충격", mode="semantic", as_of=blank)
    assert out == plain
    assert set(out["applied_config"]) == _KEYS_WITHOUT_AS_OF
    assert _ids(out) == set(_DATES) and out["applied_config"]["agent_record_scope"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["semantic", "hybrid", "fts", "tag"])
async def test_as_of_drops_only_cards_dated_after_it(_env, test_session_maker, mode):
    """같은 날은 남고(valid_from <= as_of) 하루 뒤는 빠진다. 날짜 없는 카드는 남고 센다 —
    모드와 무관하다."""
    await _seed(test_session_maker)
    out = await _env.agent_search(agent_type=_AT, q=_q(mode), mode=mode, as_of="2025-06-01")
    assert _ids(out) == {_BEFORE, _SAME, _UNDATED}, mode
    assert out["refused"] is False
    assert out["applied_config"]["as_of"] == "2025-06-01"
    assert _counts(out) == (1, 1, 3)


@pytest.mark.asyncio
async def test_boundary_is_inclusive_by_day(_env, test_session_maker):
    """경계 — valid_from 이 as_of 와 같은 날이면 남고, as_of 를 하루 당기면 빠진다."""
    await _seed(test_session_maker)
    same_day = await _env.agent_search(agent_type=_AT, q="낙하", mode="fts", as_of="2025-06-01")
    day_before = await _env.agent_search(agent_type=_AT, q="낙하", mode="fts", as_of="2025-05-31")
    assert _SAME in _ids(same_day)
    assert _ids(day_before) == {_BEFORE, _UNDATED}
    assert day_before["applied_config"]["as_of_excluded_later"] == 2


@pytest.mark.asyncio
async def test_as_of_only_removes_cards_and_keeps_the_order(_env, test_session_maker):
    """특성화 — 남은 카드의 순서 · 점수는 as_of 없이 찾은 것과 같다(뺄 뿐 순위를 바꾸지 않는다)."""
    await _seed(test_session_maker)
    plain = await _env.agent_search(agent_type=_AT, q="낙하 충격", mode="semantic")
    cut = await _env.agent_search(
        agent_type=_AT, q="낙하 충격", mode="semantic", as_of="2025-06-01")
    assert [(h["record_id"], h["score"]) for h in cut["hits"]] == [
        (h["record_id"], h["score"]) for h in plain["hits"] if h["record_id"] != _AFTER]


@pytest.mark.asyncio
async def test_undated_cards_are_kept_and_counted(_env, test_session_maker):
    """날짜 없는 카드는 확인할 날짜가 없어 남긴다 — 대신 수를 싣는다(말없이 섞지 않는다)."""
    await _seed(test_session_maker)
    out = await _env.agent_search(agent_type=_AT, q="낙하", mode="fts", as_of="2000-01-01")
    assert _ids(out) == {_UNDATED}
    assert _counts(out) == (3, 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fts", "semantic", "hybrid", "tag"])
async def test_seat_with_every_card_later_refuses_without_searching(
    _env, test_session_maker, monkeypatch, mode
):
    """묶인 카드가 다 as_of 뒤면 거절한다. 까닭은 '0 mapped'(묶인 자료 0건)와 달라야 한다."""
    await _seed(test_session_maker, {_BEFORE: date(2025, 5, 31), _SAME: date(2025, 6, 1)})
    from api.services import search_svc

    async def _boom(*a, **k):  # pragma: no cover - 불리면 그 자체가 실패다
        raise AssertionError("as_of 가 범위를 비웠는데 검색이 실행됐다")

    for name in ("fts_search", "semantic_search", "hybrid_search", "tag_search"):
        monkeypatch.setattr(search_svc, name, _boom)

    out = await _env.agent_search(agent_type=_AT, q=_q(mode), mode=mode, as_of="2025-05-30")
    assert out["refused"] is True and out["hits"] == []
    cfg = out["applied_config"]
    assert cfg["reason"] == (
        f"agent '{_AT}' has no records dated on or before as_of 2025-05-30 (2 later excluded)")
    assert not _EMPTY_SCOPE_RE.search(cfg["reason"]), "에이전트 서버가 '묶인 자료 0건' 으로 읽는다"
    assert cfg["as_of"] == "2025-05-30"
    assert (cfg["as_of_excluded_later"], cfg["as_of_undated_kept"]) == (2, 0)


@pytest.mark.asyncio
async def test_empty_seat_refusal_echoes_as_of_only_when_given(_env, test_session_maker):
    """as_of 를 받았으면 거절에도 싣는다 — 부른 쪽은 그 칸이 없으면 '거름이 안 걸렸다' 로 읽는다
    (as_of 를 모르는 옛 판은 낯선 인자를 말없이 버린다). 안 줬으면 거절 꼴도 종전 그대로다."""
    from api.db.models import Agent

    async with test_session_maker() as s:
        s.add(Agent(agent_type="seat-empty", name="빈 좌석", description="바인딩 0건",
                    common_tags=[], data_types=["DOC"]))
        await s.commit()

    plain = await _env.agent_search(agent_type="seat-empty", q="낙하", mode="fts")
    assert plain["applied_config"] == {"reason": "agent 'seat-empty' has 0 mapped records"}
    out = await _env.agent_search(agent_type="seat-empty", q="낙하", mode="fts", as_of="2025-06-01")
    assert out["refused"] is True
    assert out["applied_config"] == {
        "reason": "agent 'seat-empty' has 0 mapped records",
        "as_of": "2025-06-01", "as_of_excluded_later": 0, "as_of_undated_kept": 0,
    }


@pytest.mark.asyncio
async def test_tag_refusal_echoes_as_of(_env, test_session_maker):
    await _seed(test_session_maker)
    out = await _env.agent_search(agent_type=_AT, q=" , ", mode="tag", as_of="2025-06-01")
    assert out["refused"] is True
    assert out["applied_config"]["reason"] == "태그 없음"
    assert out["applied_config"]["as_of"] == "2025-06-01"


@pytest.mark.asyncio
@pytest.mark.parametrize("as_of, day", [
    ("2025-06-01T23:59:59", "2025-06-01"),
    ("2025-06-01T00:00:00Z", "2025-06-01"),
    ("2025-06-01 08:30", "2025-06-01"),
    ("20250601", "2025-06-01"),
    # 시간대를 바꾸지 않는다 — UTC 로 바꾸면 날짜가 넘어가는 둘이다.
    ("2025-06-01T23:00:00-05:00", "2025-06-01"),
    ("2025-06-02T01:00:00+09:00", "2025-06-02"),
])
async def test_datetime_as_of_counts_by_its_written_date(_env, test_session_maker, as_of, day):
    await _seed(test_session_maker)
    out = await _env.agent_search(agent_type=_AT, q="낙하", mode="fts", as_of=as_of)
    assert out["applied_config"]["as_of"] == day
    assert (_AFTER in _ids(out)) is (day == "2025-06-02")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    "yesterday", "2025/06/01", "06-01-2025", "2025-6-1", "2025", "2025-06",
    "2025-13-01", "2025-02-30", "2025-06-01T25:00", "2025-06-01abc", "null",
])
async def test_bad_as_of_is_an_error_before_any_db_work(monkeypatch, bad):
    """틀린 날짜를 '거름 없음' 으로 접으면 소급 심의가 그 날 뒤의 카드를 모른 채 쓴다 —
    오류로 돌려준다."""
    from api import mcp_runtime

    def _no_db():  # pragma: no cover - 불리면 그 자체가 실패다
        raise AssertionError("as_of 가 틀렸는데 DB 를 열었다")

    monkeypatch.setattr(mcp_runtime, "SessionLocal", _no_db)
    with pytest.raises(ValueError, match="as_of must be an ISO date") as err:
        await mcp_runtime.agent_search(agent_type=_AT, q="낙하", mode="fts", as_of=bad)
    assert repr(bad) in str(err.value), "무엇이 틀렸는지 받은 값을 함께 말한다"


def test_bad_as_of_message_says_why_when_the_date_is_impossible():
    from api.mcp_runtime import _parse_as_of

    with pytest.raises(ValueError, match="day is out of range for month"):
        _parse_as_of("2025-02-30")


@pytest.mark.asyncio
async def test_mcp_tool_offers_optional_as_of_and_reports_bad_input(monkeypatch):
    """게이트웨이 · 에이전트 서버가 지나는 길 — 도구 목록의 인자, 그리고 틀린 값은 도구
    오류의 글로 간다."""
    from mcp.server.fastmcp.exceptions import ToolError

    from api import mcp_runtime

    tools = {t.name: t for t in await mcp_runtime.mcp.list_tools()}
    schema = tools["agent_search"].inputSchema
    assert "as_of" in schema["properties"] and "as_of" not in schema.get("required", [])
    assert '"string"' in json.dumps(schema["properties"]["as_of"])
    assert "as_of" in (tools["agent_search"].description or "")

    def _no_db():  # pragma: no cover - 불리면 그 자체가 실패다
        raise AssertionError("as_of 가 틀렸는데 DB 를 열었다")

    monkeypatch.setattr(mcp_runtime, "SessionLocal", _no_db)
    with pytest.raises(ToolError, match=r"agent_search: as_of must be an ISO date .*'2025/06/01'"):
        await mcp_runtime.mcp.call_tool(
            "agent_search", {"agent_type": _AT, "q": "낙하", "as_of": "2025/06/01"})
    # 숫자는 글로 바꿔 받지 않는다(인자 검사가 막는다).
    with pytest.raises(ToolError, match="as_of"):
        await mcp_runtime.mcp.call_tool(
            "agent_search", {"agent_type": _AT, "q": "낙하", "as_of": 20250601})
