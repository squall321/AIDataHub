# agent_search as_of — valid_from 이 그 날 뒤인 카드는 빼고, 날짜 없는 카드는 부른 쪽이 고른다
"""AD-07 — agent_search 가 as_of 를 받지 않아 소급 심의가 그 시점 뒤에 생긴 카드까지 근거로 썼다.

믿을 날짜는 records.valid_from(유효 시작 — 동기화 카드는 원천 날짜, 업로드 카드는
올린 사람이 적은 날)이다. created_at 은 허브에 적재한 날이라 쓰지 않는다. 날짜 없는 카드는
부른 쪽이 고른다(as_of_undated) — 기본은 남기고 세는 것이다. 업로드 · 가져오기 카드는 올린
쪽이 날짜를 줄 때만 차고 논문 카드 길은 주지 않아, 빼면 지식만 가진 좌석이 통째로
빈다(HWAXPortal docs/delib-engine-feedback D-15 — 어떻게 볼지는 사람 결정으로 남았다).
히트마다 valid_from 을 실어 어느 근거가 날짜 미확인인지 보인다. as_of 를 안 주면 종전 그대로다.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import TextClause

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
    assert all("valid_from" not in h for h in out["hits"]), "히트 꼴도 종전 그대로다"


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
@pytest.mark.parametrize("mode", ["semantic", "hybrid", "fts", "tag"])
async def test_each_hit_carries_its_date_so_undated_evidence_shows(_env, test_session_maker, mode):
    """as_of 를 주면 히트마다 valid_from 이 실린다 — 날짜 없이 남긴 카드는 null 이라, 부른 쪽이
    인용한 근거 가운데 어느 것이 날짜 미확인인지 안다(as_of_undated_kept 는 좌석 전체의 수라
    그것을 모른다)."""
    await _seed(test_session_maker)
    out = await _env.agent_search(agent_type=_AT, q=_q(mode), mode=mode, as_of="2025-06-01")
    assert {h["record_id"]: h["valid_from"] for h in out["hits"]} == {
        _BEFORE: "2025-05-31", _SAME: "2025-06-01", _UNDATED: None}, mode


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["semantic", "hybrid", "fts", "tag"])
async def test_strict_mode_also_drops_undated_cards(_env, test_session_maker, mode):
    """as_of_undated='exclude'(엄격) — 날짜 없는 카드도 뺀다. 뺀 수는 as_of_undated_excluded 에 싣고
    남긴 수 칸은 없다 — 어느 칸이 왔는지로 부른 쪽이 고른 정책이 걸렸는지 안다."""
    await _seed(test_session_maker)
    out = await _env.agent_search(
        agent_type=_AT, q=_q(mode), mode=mode, as_of="2025-06-01", as_of_undated="exclude")
    assert _ids(out) == {_BEFORE, _SAME}, mode
    cfg = out["applied_config"]
    assert (cfg["as_of_excluded_later"], cfg["as_of_undated_excluded"]) == (1, 1)
    assert cfg["agent_record_scope"] == 2 and "as_of_undated_kept" not in cfg
    assert {h["valid_from"] for h in out["hits"]} == {"2025-05-31", "2025-06-01"}


@pytest.mark.asyncio
async def test_strict_refusal_counts_undated_too(_env, test_session_maker):
    """남은 것이 날짜 없는 카드뿐이면 엄격에서는 거절한다 — 까닭에 둘 다 세고 '0 mapped' 와 다르다.
    기본(남김)에서는 같은 좌석이 날짜 없는 카드로 답한다."""
    await _seed(test_session_maker, {_AFTER: date(2025, 6, 2), _UNDATED: None})
    strict = await _env.agent_search(
        agent_type=_AT, q="낙하", mode="fts", as_of="2025-06-01", as_of_undated="exclude")
    assert strict["refused"] is True and strict["hits"] == []
    reason = strict["applied_config"]["reason"]
    assert reason == (
        f"agent '{_AT}' has no records dated on or before as_of 2025-06-01 "
        "(1 later, 1 undated excluded)")
    assert not _EMPTY_SCOPE_RE.search(reason), "에이전트 서버가 '묶인 자료 0건' 으로 읽는다"
    kept = await _env.agent_search(agent_type=_AT, q="낙하", mode="fts", as_of="2025-06-01")
    assert kept["refused"] is False and _ids(kept) == {_UNDATED}


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
    undated = schema["properties"]["as_of_undated"]
    assert (undated.get("enum"), undated.get("default")) == (["keep", "exclude"], "keep")
    assert "as_of_undated" not in schema.get("required", [])
    assert "as_of_undated" in (tools["agent_search"].description or "")

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


@pytest.mark.asyncio
@pytest.mark.parametrize("args, why", [
    # FastMCP 는 주석이 `str` 이 아닌 인자(`str | None`)의 글을 JSON 으로 먼저 푼다 — 글 "null" 이
    # None(안 줌)이 되어 말없이 거르지 않았다. 함수를 바로 부르는 시험으로는 보이지 않는다.
    ({"as_of": "null"}, r"as_of must be an ISO date .*'null'"),
    ({"as_of": " null "}, r"as_of must be an ISO date .*' null '"),
    # JSON null 도 '안 줌' 이 아니다 — 거르지 않으려면 키를 뺀다(이 서버의 선택 글 인자는
    # 모두 str = "" 이다).
    ({"as_of": None}, r"agent_searchArguments\s+as_of\s+Input should be a valid string"),
    ({"as_of": "2025-06-01", "as_of_undated": "strict"},
     r"agent_searchArguments\s+as_of_undated\s+Input should be 'keep' or 'exclude'"),
])
async def test_mcp_layer_never_turns_bad_input_into_no_filter(monkeypatch, args, why):
    from mcp.server.fastmcp.exceptions import ToolError

    from api import mcp_runtime

    def _no_db():  # pragma: no cover - 불리면 그 자체가 실패다
        raise AssertionError("as_of 가 틀렸는데 DB 를 열었다")

    monkeypatch.setattr(mcp_runtime, "SessionLocal", _no_db)
    with pytest.raises(ToolError, match=why):
        await mcp_runtime.mcp.call_tool("agent_search", {"agent_type": _AT, "q": "낙하", **args})


# --- 운영 갈래 — 시험 DB(SQLite)는 `@>` 를 몰라 위 시험은 모두 폴백 갈래를 탄다 ---
# (그 시험들은 aiosqlite 가 없으면 건너뛴다. 아래는 DB 없이 돈다.)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _PgSession:
    """운영 갈래(`records.agents @> ARRAY[좌석]`)를 타게 하는 세션 대역 — DB 도 aiosqlite 도
    쓰지 않는다.

    방언은 PostgreSQL 이라고 답하고(statement_limit · is_postgres 가 그것을 본다), 좌석 범위 문장은
    PostgreSQL 방언으로 컴파일해 꼴을 확인한 뒤 **그 문장이 고른 칸**을 카드에서 읽어 돌려준다 —
    날짜 칸을 버리거나 다른 칸(created_at)으로 바꾸면 결과가 달라진다. 폴백(`&&`)으로 빠지면
    실패한다.
    """

    def __init__(self, agent, cards):
        self.agent, self.cards, self.sql = agent, cards, []
        self._bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def get_bind(self):
        return self._bind

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, model, key):
        return self.agent if key == self.agent.agent_type else None

    async def execute(self, stmt, *a, **kw):
        if isinstance(stmt, TextClause):  # statement_limit 의 SET LOCAL
            self.sql.append(stmt.text)
            return _Rows([])
        compiled = stmt.compile(dialect=postgresql.dialect())
        self.sql.append(str(compiled))
        assert "records.agents @> " in str(compiled), f"운영 갈래의 꼴이 아니다: {compiled}"
        assert [self.agent.agent_type] in compiled.params.values(), compiled.params
        cols = [c.key for c in stmt.selected_columns]
        return _Rows([tuple(getattr(c, k) for k in cols) for c in self.cards])


@pytest.fixture
def _pg(monkeypatch):
    """운영 갈래로 agent_search 를 부른다 — 검색 함수 대역은 받은 범위(record_ids)를 적고 그
    카드를 낸다."""
    from api import mcp_runtime
    from api.db.models import Agent
    from api.services import search_svc

    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    calls: list[tuple[str, list[str]]] = []

    def _hit(rid):
        return {"record_id": rid, "title": rid, "data_type": "DOC", "section_id": "1",
                "section_title": "본문", "snippet": "낙하 충격", "tags": ["낙하"], "score": 0.5}

    async def _fts(session, q, *, limit=20, offset=0, record_ids=None, data_types=None):
        calls.append(("fts", list(record_ids)))
        return [_hit(r) for r in record_ids][:limit], len(record_ids)

    async def _semantic(session, query, *, top_k=5, data_types=None, record_ids=None,
                        tag_boost=None, min_score=None):
        calls.append(("semantic" if min_score is None else "semantic>=", list(record_ids)))
        # 좌석 문턱(min_score)을 넘는 것은 없다 — 거절 뒤 near_miss 가 문턱 없이 다시 묻는다.
        return [] if min_score is not None else [_hit(r) for r in record_ids][:top_k]

    monkeypatch.setattr(search_svc, "fts_search", _fts)
    monkeypatch.setattr(search_svc, "semantic_search", _semantic)
    # 허브 적재일(created_at)은 모두 as_of 뒤다 — 날짜 칸을 그것으로 바꾸면 결과가 틀어진다.
    cards = [SimpleNamespace(id=rid, agents=[_AT], valid_from=vf, depth=0,
                             created_at=datetime(2026, 8, 1, 9, 0)) for rid, vf in _DATES.items()]

    async def run(retrieval_config=None, **kw):
        agent = Agent(agent_type=_AT, name="낙하", description="낙하 충격", common_tags=["낙하"],
                      data_types=["DOC"], retrieval_config=retrieval_config or {"top_k": 10})
        sess = _PgSession(agent, cards)
        monkeypatch.setattr(mcp_runtime, "SessionLocal", lambda: sess)
        out = await mcp_runtime.agent_search(agent_type=_AT, q="낙하", **kw)
        assert sum("records.agents @> " in s for s in sess.sql) == 1, sess.sql
        return out, calls

    return run


@pytest.mark.asyncio
@pytest.mark.parametrize("undated, scope", [
    ({}, [_BEFORE, _SAME, _UNDATED]),
    ({"as_of_undated": "keep"}, [_BEFORE, _SAME, _UNDATED]),
    ({"as_of_undated": "exclude"}, [_BEFORE, _SAME]),
])
async def test_production_branch_filters_by_valid_from(_pg, undated, scope):
    """운영(PostgreSQL)에서 실제로 도는 갈래가 valid_from 으로 거른다 — 위 시험들은 SQLite 라
    폴백만 탔고, 운영 갈래에서 날짜를 버리거나 created_at 을 읽어도 모두 통과했다(10-30 반대
    검토 M1 · M2)."""
    out, calls = await _pg(mode="fts", as_of="2025-06-01", **undated)
    assert calls == [("fts", scope)]
    assert {h["record_id"]: h["valid_from"] for h in out["hits"]} == {
        rid: (_DATES[rid].isoformat() if _DATES[rid] else None) for rid in scope}
    cfg = out["applied_config"]
    assert (cfg["as_of"], cfg["as_of_excluded_later"], cfg["agent_record_scope"]) == (
        "2025-06-01", 1, len(scope))
    strict = undated.get("as_of_undated") == "exclude"
    assert cfg["as_of_undated_excluded" if strict else "as_of_undated_kept"] == 1


@pytest.mark.asyncio
async def test_production_branch_without_as_of_searches_the_whole_seat(_pg):
    out, calls = await _pg(mode="fts")
    assert calls == [("fts", list(_DATES))]
    assert "as_of" not in out["applied_config"]
    assert all("valid_from" not in h for h in out["hits"])


@pytest.mark.asyncio
async def test_near_miss_stays_inside_the_as_of_scope(_pg):
    """좌석 문턱에 걸려 거절하면 near_miss 가 문턱 없이 다시 묻는다 — 그때도 거른 범위여야 한다.
    거르기 전 범위로 물으면 as_of 뒤 카드의 제목 · id 가 '참고용 후보' 로 샌다(반대 검토 M4)."""
    out, calls = await _pg(retrieval_config={"top_k": 10, "score_threshold": 0.9},
                           mode="semantic", as_of="2025-06-01")
    assert out["refused"] is True and out["hits"] == []
    scope = [_BEFORE, _SAME, _UNDATED]
    assert calls == [("semantic>=", scope), ("semantic", scope)]
    assert [n["record_id"] for n in out["near_miss"]] == scope
