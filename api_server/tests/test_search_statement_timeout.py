# 검색 트랜잭션의 문장 한도(AIDH_SEARCH_STATEMENT_TIMEOUT_S)가 걸리고, 걸렸을 때 손잡이 이름을 말하는지
from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.sql.elements import TextClause

from api import mcp_runtime
from api.config import settings
from api.services import search_svc


class _FakeSession:
    """돌린 문장을 적어 두는 가짜 세션. 방언 이름만 진짜처럼 답한다(is_postgres 가 그것을 본다)."""

    def __init__(self, dialect: str = "postgresql") -> None:
        self.statements: list[str] = []
        self._bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect))

    def get_bind(self):
        return self._bind

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt.text if isinstance(stmt, TextClause) else str(stmt))


def _pg_error(sqlstate: str, message: str) -> DBAPIError:
    """asyncpg 방언이 올리는 모양 — orig 에 sqlstate 가 달린 DBAPIError."""
    orig = Exception(message)
    orig.sqlstate = sqlstate  # type: ignore[attr-defined]
    return DBAPIError("SELECT 1", {}, orig)


async def test_sets_the_limit_for_this_transaction_only():
    """⚠ 회귀 방지 — 한도가 없으면 멈춘 쿼리가 끝없이 기다리며 풀 자리를 쥔다.

    부른 쪽(게이트웨이)만 포기하고 AIDataHub 안의 문장은 계속 돈다. SET **LOCAL** 이어야 한다 —
    세션 단위로 걸면 그 연결을 다음에 빌리는 적재·동기화·임베딩 잡까지 끊는다.
    """
    session = _FakeSession()
    async with search_svc.statement_limit(session):
        pass
    assert session.statements == ["SET LOCAL statement_timeout = 90000"]


async def test_limit_follows_the_knob(monkeypatch):
    monkeypatch.setattr(settings, "aidh_search_statement_timeout_s", 1.5)
    session = _FakeSession()
    async with search_svc.statement_limit(session):
        pass
    assert session.statements == ["SET LOCAL statement_timeout = 1500"]


async def test_zero_turns_the_limit_off(monkeypatch):
    monkeypatch.setattr(settings, "aidh_search_statement_timeout_s", 0)
    session = _FakeSession()
    async with search_svc.statement_limit(session):
        pass
    assert session.statements == []


async def test_sqlite_is_left_alone():
    """SQLite(스모크·시험)는 SET LOCAL 을 모른다 — 문법 오류로 검색이 통째로 죽으면 안 된다."""
    session = _FakeSession("sqlite")
    async with search_svc.statement_limit(session):
        pass
    assert session.statements == []


async def test_expiry_names_the_knob():
    """PG 의 원문('canceling statement due to statement timeout')은 어느 설정이 판정했는지 말하지 않는다."""
    with pytest.raises(TimeoutError) as err:
        async with search_svc.statement_limit(_FakeSession()):
            raise _pg_error("57014", "canceling statement due to statement timeout")
    assert str(err.value) == (
        "검색이 90초 안에 끝나지 않아 취소했다(AIDH_SEARCH_STATEMENT_TIMEOUT_S)"
    )
    assert isinstance(err.value.__cause__, DBAPIError)      # 원문은 사슬에 남는다


async def test_expiry_swallowed_by_a_fallback_still_names_the_knob():
    """MCP 도구의 좌석 id 조회는 오류를 ``except Exception`` 으로 받아 폴백 문장을 돌린다.

    PG 에서는 그 폴백이 '트랜잭션이 중단됐다'(25P02)로 실패해 원래 사유를 덮었다 — records 를 잠근 PG 에서
    한도는 1초에 걸렸는데 나간 문구는 'current transaction is aborted' 였다.
    """
    with pytest.raises(TimeoutError) as err:
        async with search_svc.statement_limit(_FakeSession()):
            try:
                raise _pg_error("57014", "canceling statement due to statement timeout")
            except Exception:  # noqa: BLE001 — 도구의 폴백과 같은 모양
                raise _pg_error("25P02", "current transaction is aborted")
    assert "AIDH_SEARCH_STATEMENT_TIMEOUT_S" in str(err.value)


async def test_other_database_errors_pass_through_unchanged():
    """한도와 무관한 오류에 '시간 초과' 딱지를 붙이면 엉뚱한 손잡이를 돌리게 된다."""
    boom = _pg_error("42P01", 'relation "nope" does not exist')
    with pytest.raises(DBAPIError) as err:
        async with search_svc.statement_limit(_FakeSession()):
            raise boom
    assert err.value is boom


async def test_cancel_is_not_blamed_on_the_knob_when_the_limit_is_off(monkeypatch):
    """한도를 끈 박스에서 난 취소(관리자의 pg_cancel_backend 등)를 이 손잡이 탓으로 돌리지 않는다."""
    monkeypatch.setattr(settings, "aidh_search_statement_timeout_s", 0)
    boom = _pg_error("57014", "canceling statement due to user request")
    with pytest.raises(DBAPIError) as err:
        async with search_svc.statement_limit(_FakeSession()):
            raise boom
    assert err.value is boom


# ---------------------------------------------------------------------------
# MCP 검색 도구 넷이 실제로 이 한도 아래서 도는지
# ---------------------------------------------------------------------------
class _Stop(Exception):
    """한도를 건 직후 도구를 멈춘다 — 검색 자체는 이 시험의 관심이 아니다."""


class _WiredSession(_FakeSession):
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *args, **kwargs):
        raise _Stop

    async def execute(self, stmt, *args, **kwargs):
        if not isinstance(stmt, TextClause):
            raise _Stop
        await super().execute(stmt)


@pytest.mark.parametrize(
    "tool, kwargs",
    [
        ("agent_search", {"agent_type": "any-seat", "q": "q", "mode": "hybrid"}),
        ("semantic_search", {"q": "q"}),
        ("hybrid_search", {"q": "q"}),
        ("fts_search", {"q": "q"}),
    ],
)
async def test_mcp_search_tools_run_under_the_limit(monkeypatch, tool, kwargs):
    """손잡이가 이름만 있고 도구에 안 걸리면 없는 것이다 — 도구가 여는 트랜잭션의 **첫 문장**이 한도다."""
    session = _WiredSession()
    monkeypatch.setattr(mcp_runtime, "SessionLocal", lambda: session)

    async def _stop(*args, **kwargs):
        raise _Stop

    for name in ("semantic_search", "hybrid_search", "fts_search"):
        monkeypatch.setattr(search_svc, name, _stop)

    with pytest.raises(_Stop):
        await getattr(mcp_runtime, tool)(**kwargs)
    assert session.statements == ["SET LOCAL statement_timeout = 90000"]


def test_pool_wait_plus_statement_limit_stays_inside_the_callers_wait():
    """층 계약 — 안쪽(풀 대기 + 문장 한도)이 부르는 쪽보다 먼저 걸려야 사유가 구체적으로 나온다.

    심의 엔진은 지식카드 조회 1건을 KNOWLEDGE_TIMEOUT_S(180초, HWAXAgentServer) 기다린다. 이쪽 합이
    그보다 크면 엔진이 먼저 포기해 '풀이 찼다'·'문장이 멈췄다' 대신 이름 없는 시간 초과만 남는다.
    한쪽 기본값을 올리면 다른 쪽을 같이 본다.
    """
    caller_wait_s = 180
    fresh = type(settings)(_env_file=None)          # 박스 .env 가 아니라 코드 기본값을 본다
    assert fresh.db_pool_timeout + fresh.aidh_search_statement_timeout_s == 150 < caller_wait_s
    assert fresh.aidh_db_connect_timeout_s < fresh.db_pool_timeout


# ---------------------------------------------------------------------------
# 실 PostgreSQL — 문장이 정말로 취소되는지 (opt-in: AIDH_TEST_PG_URL, conftest 의 pg_engine 과 같은 규약)
# 스키마를 만들지 않고 pg_sleep 만 돌린다. 미설정이면 skip.
# ---------------------------------------------------------------------------
@pytest.fixture
async def pg_maker():
    url = os.environ.get("AIDH_TEST_PG_URL")
    if not url:
        pytest.skip("AIDH_TEST_PG_URL 미설정 — 실 PG 문장 취소 시험 skip")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # 연결 하나만 쓴다 — 뒤 시험이 '같은 연결을 다음에 빌린 쪽' 을 보려면 그래야 한다.
    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — 도달 불가는 실패가 아니라 skip
        await engine.dispose()
        pytest.skip(f"PG 도달 실패 — skip: {exc}")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_pg_cancels_a_stuck_statement_and_names_the_knob(pg_maker, monkeypatch):
    monkeypatch.setattr(settings, "aidh_search_statement_timeout_s", 0.3)
    started = time.monotonic()
    with pytest.raises(TimeoutError) as err:
        async with pg_maker() as session, search_svc.statement_limit(session):
            await session.execute(text("SELECT pg_sleep(5)"))
    assert time.monotonic() - started < 3
    assert str(err.value) == (
        "검색이 0.3초 안에 끝나지 않아 취소했다(AIDH_SEARCH_STATEMENT_TIMEOUT_S)"
    )


async def test_pg_expiry_swallowed_by_a_fallback_still_names_the_knob(pg_maker, monkeypatch):
    """삼킨 뒤 돌린 문장은 PG 가 25P02 로 거절한다 — 그 오류의 사슬에서 취소를 찾아 손잡이를 말한다."""
    monkeypatch.setattr(settings, "aidh_search_statement_timeout_s", 0.3)
    with pytest.raises(TimeoutError) as err:
        async with pg_maker() as session, search_svc.statement_limit(session):
            try:
                await session.execute(text("SELECT pg_sleep(5)"))
            except Exception:  # noqa: BLE001 — 도구의 폴백과 같은 모양
                await session.execute(text("SELECT 1"))
    assert "AIDH_SEARCH_STATEMENT_TIMEOUT_S" in str(err.value)


async def test_pg_limit_does_not_outlive_the_transaction(pg_maker, monkeypatch):
    """SET LOCAL 이라 트랜잭션과 함께 풀린다 — 같은 연결을 다음에 빌리는 일은 한도 없이 돈다."""
    monkeypatch.setattr(settings, "aidh_search_statement_timeout_s", 0.3)
    async with pg_maker() as session:
        before = (await session.execute(text("SHOW statement_timeout"))).scalar()
    async with pg_maker() as session, search_svc.statement_limit(session):
        inside = (await session.execute(text("SHOW statement_timeout"))).scalar()
    async with pg_maker() as session:
        after = (await session.execute(text("SHOW statement_timeout"))).scalar()
        await session.execute(text("SELECT pg_sleep(0.6)"))     # 한도(0.3초)가 남았다면 여기서 취소된다
    assert inside == "300ms"
    assert after == before
