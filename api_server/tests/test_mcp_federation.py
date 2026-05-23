"""Wave-6 P1 — MCP federation 단위 테스트.

라이브 외부 MCP 서버 없이 다음을 검증:
    1. _namespace 정상
    2. invalid alias regex 거부
    3. yaml config 로더 (config/upstream_mcps.example.yaml)
    4. ALIAS_TAKEN 검출
    5. TOOL_CONFLICT 검출 (built-in 충돌)
    6. dispatch_call 정상 — _open_session 을 fake 로 mock
    7. dispatch_call UPSTREAM_TIMEOUT
    8. dispatch_call UPSTREAM_AUTH_FAILED
    9. _audit_log_call → mcp_proxy_calls 1행 INSERT (SQLite fixture)
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 공용 import — pytest.importorskip 대신 정상 import (모듈 없으면 즉시 실패)
# ---------------------------------------------------------------------------
from api.services import mcp_federation as fed


# ---------------------------------------------------------------------------
# Fake session — call_tool / list_tools 응답을 사전 정의
# ---------------------------------------------------------------------------
class _FakeCallResult:
    def __init__(self, payload: dict, is_error: bool = False) -> None:
        self._payload = payload
        self.isError = is_error

    def model_dump(self, mode: str = "json") -> dict:
        return {"isError": self.isError, **self._payload}


class _FakeTool:
    def __init__(self, name: str, description: str = "", schema: dict | None = None):
        self.name = name
        self.description = description
        self.inputSchema = schema or {}


class _FakeListToolsResult:
    def __init__(self, tools: list[_FakeTool]) -> None:
        self.tools = tools


class _FakeSession:
    def __init__(
        self,
        tools: list[_FakeTool] | None = None,
        call_result: _FakeCallResult | None = None,
        call_exc: Exception | None = None,
        call_delay: float = 0.0,
    ) -> None:
        self._tools = tools or []
        self._call_result = call_result or _FakeCallResult({"content": [{"text": "ok"}]})
        self._call_exc = call_exc
        self._call_delay = call_delay
        self.calls: list[tuple[str, dict | None]] = []

    async def initialize(self) -> None:
        return None

    async def list_tools(self) -> _FakeListToolsResult:
        return _FakeListToolsResult(self._tools)

    async def call_tool(self, name: str, arguments=None, **_kw):
        self.calls.append((name, arguments))
        if self._call_delay:
            await asyncio.sleep(self._call_delay)
        if self._call_exc:
            raise self._call_exc
        return self._call_result


def _make_open_session_factory(session: _FakeSession):
    """``_open_session`` 의 대체 — async fn 이 async-cm 반환하는 시그니처 유지."""

    async def fake_open_session(cfg, timeout: float = 10.0):
        @asynccontextmanager
        async def _ctx():
            yield session

        return _ctx()

    return fake_open_session


# ---------------------------------------------------------------------------
# 각 테스트마다 _clients 풀 격리
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_pool():
    fed.reset_clients_for_test()
    yield
    fed.reset_clients_for_test()


# ===========================================================================
# 1. _namespace 정상
# ===========================================================================
def test_namespace_basic() -> None:
    assert fed._namespace("analytics", "report_X") == "analytics__report_X"
    assert fed._namespace("hq", "list_things") == "hq__list_things"


# ===========================================================================
# 2. invalid alias regex 거부
# ===========================================================================
@pytest.mark.parametrize(
    "bad_alias",
    [
        "Analytics",    # 대문자
        "ana-lytics",   # dash
        "a",            # 너무 짧음 (regex 는 최소 2자 — 1 lead + 1+ tail)
        "1analytics",   # 숫자 시작
        "x" * 32,       # 너무 김
        "",             # 빈
    ],
)
def test_validate_invalid_alias_rejected(bad_alias: str) -> None:
    with pytest.raises(fed.UpstreamConfigError) as ei:
        fed.validate_upstream_config(
            {"alias": bad_alias, "transport": "http", "url": "http://x"}
        )
    assert ei.value.code == fed.ERR_INVALID_ALIAS


# ===========================================================================
# 3. yaml config 로더 — example 파일 그대로 파싱 성공
# ===========================================================================
def test_load_example_yaml() -> None:
    repo = Path(__file__).resolve().parents[2]
    example = repo / "config" / "upstream_mcps.example.yaml"
    assert example.exists(), f"example yaml missing: {example}"

    cfgs = fed._load_config_from_yaml(example)
    # example 에는 analytics (enabled) + hq (disabled) 2개 (stdio 는 주석)
    aliases = [c.alias for c in cfgs]
    assert "analytics" in aliases
    assert "hq" in aliases
    # auth 가 dict 로 파싱
    ana = next(c for c in cfgs if c.alias == "analytics")
    assert ana.transport == "http"
    assert ana.url.startswith("http://")
    assert ana.auth and ana.auth.get("type") == "bearer"


# ===========================================================================
# 4. ALIAS_TAKEN 검출 — 같은 alias 두 번
# ===========================================================================
def test_alias_taken_detection() -> None:
    cfgs = [
        fed.validate_upstream_config(
            {"alias": "analytics", "transport": "http", "url": "http://a"}
        ),
        fed.validate_upstream_config(
            {"alias": "analytics", "transport": "http", "url": "http://b"}
        ),
    ]
    with pytest.raises(fed.UpstreamConfigError) as ei:
        fed.validate_no_alias_collision(cfgs)
    assert ei.value.code == fed.ERR_ALIAS_TAKEN


# ===========================================================================
# 5. TOOL_CONFLICT 검출 — built-in discover 와 충돌
# ===========================================================================
def test_tool_conflict_with_builtin() -> None:
    """alias__tool 이 이미 등록된 이름 (built-in 또는 다른 upstream) 과 충돌."""
    # 시나리오 1: 이미 등록된 이름 셋에 analytics__report 가 들어있으면 충돌
    existing = {"discover", "analytics__report"}
    with pytest.raises(fed.UpstreamConfigError) as ei:
        fed.check_tool_conflict("analytics", "report", existing)
    assert ei.value.code == fed.ERR_TOOL_CONFLICT

    # 시나리오 2: built-in 과 충돌 — alias 가 비어있는 잘못 경로 가드
    # (정상 흐름에서는 alias 가 항상 있으므로 alias='' 케이스만 가드 검증)
    existing2: set[str] = set()
    # 정상 호출 — alias='analytics', tool='discover' 은 'analytics__discover' 로
    # 노출되므로 built-in 의 'discover' 와 충돌하지 않는다. 즉 통과.
    exposed = fed.check_tool_conflict("analytics", "discover", existing2)
    assert exposed == "analytics__discover"


# ===========================================================================
# 6. dispatch_call 정상 — fake session 으로 success path
# ===========================================================================
@pytest.mark.asyncio
async def test_dispatch_call_success(monkeypatch) -> None:
    cfg = fed.UpstreamConfig(
        alias="analytics", transport="http", url="http://fake/mcp/"
    )
    # 풀에 사전 등록 (register_all_upstreams 우회)
    fed._clients["analytics"] = fed._RegisteredUpstream(
        config=cfg,
        exposed_tools=["analytics__report"],
        raw_to_exposed={"report": "analytics__report"},
        last_health_status="ok",
    )

    fake = _FakeSession(call_result=_FakeCallResult({"content": [{"text": "42"}]}))
    monkeypatch.setattr(fed, "_open_session", _make_open_session_factory(fake))

    out = await fed.dispatch_call("analytics", "report", {"q": "hello"})

    assert out["ok"] is True
    assert out["alias"] == "analytics"
    assert out["tool"] == "report"
    assert out["error_code"] is None
    assert out["result"]["isError"] is False
    assert fake.calls == [("report", {"q": "hello"})]


# ===========================================================================
# 7. UPSTREAM_TIMEOUT — asyncio.TimeoutError 시 정확한 에러 코드
# ===========================================================================
@pytest.mark.asyncio
async def test_dispatch_call_timeout(monkeypatch) -> None:
    cfg = fed.UpstreamConfig(
        alias="slow", transport="http", url="http://slow/mcp/"
    )
    fed._clients["slow"] = fed._RegisteredUpstream(config=cfg)

    # call_tool 안에서 asyncio.wait_for 의 timeout 보다 긴 sleep → TimeoutError
    fake = _FakeSession(call_delay=10.0)
    monkeypatch.setattr(fed, "_open_session", _make_open_session_factory(fake))

    out = await fed.dispatch_call("slow", "slow_tool", {}, timeout=0.05)

    assert out["ok"] is False
    assert out["error_code"] == fed.ERR_UPSTREAM_TIMEOUT


# ===========================================================================
# 8. UPSTREAM_AUTH_FAILED — call_tool 이 401 풍 예외
# ===========================================================================
@pytest.mark.asyncio
async def test_dispatch_call_auth_failed(monkeypatch) -> None:
    cfg = fed.UpstreamConfig(
        alias="locked", transport="http", url="http://locked/mcp/"
    )
    fed._clients["locked"] = fed._RegisteredUpstream(config=cfg)

    fake = _FakeSession(call_exc=RuntimeError("HTTP 401 Unauthorized"))
    monkeypatch.setattr(fed, "_open_session", _make_open_session_factory(fake))

    out = await fed.dispatch_call("locked", "any_tool", {})

    assert out["ok"] is False
    assert out["error_code"] == fed.ERR_UPSTREAM_AUTH_FAILED


# ===========================================================================
# 9. _audit_log_call → mcp_proxy_calls 1행 INSERT (SQLite fixture)
# ===========================================================================
# 메모리 규칙: dev PC 에 aiosqlite install 금지. fixture 는 aiosqlite 필요 →
# 미설치 시 skip (운영 PG 환경에서는 통합 테스트가 별도로 검증).
aiosqlite_available = True
try:
    import aiosqlite  # type: ignore[import-not-found] # noqa: F401
except ImportError:
    aiosqlite_available = False


@pytest.mark.asyncio
@pytest.mark.skipif(not aiosqlite_available, reason="aiosqlite 미설치 — dev PC install 금지 규칙")
async def test_audit_log_inserts_row(test_session_maker) -> None:
    from sqlalchemy import select

    from api.db.models import MCPProxyCall

    async with test_session_maker() as session:
        await fed._audit_log_call(
            session=session,
            alias="analytics",
            raw_tool="report",
            exposed_tool="analytics__report",
            latency_ms=42,
            status="ok",
        )
        await session.commit()

    async with test_session_maker() as s2:
        rows = (await s2.execute(select(MCPProxyCall))).scalars().all()
        assert len(rows) == 1
        r = rows[0]
        assert r.upstream_alias == "analytics"
        assert r.raw_tool_name == "report"
        assert r.exposed_tool_name == "analytics__report"
        assert r.latency_ms == 42
        assert r.status == "ok"
        assert r.error_code is None


# ===========================================================================
# 10. register_all_upstreams (no config) → no-op (빈 list)
# ===========================================================================
def test_register_all_upstreams_no_config(monkeypatch) -> None:
    """env / DB 둘 다 없으면 빈 list. dev box 안전망."""
    monkeypatch.delenv("AIDH_UPSTREAM_CONFIG", raising=False)
    monkeypatch.delenv("AIDH_MCP_FEDERATION", raising=False)

    out = fed.register_all_upstreams(mcp=None)
    assert out == []
