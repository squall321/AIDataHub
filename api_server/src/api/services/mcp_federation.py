"""Wave-6 P1 — MCP federation / proxy 서비스.

설계:
    AIDataHub 가 다수의 외부 FastMCP 서버를 단일 진입점으로 묶어 노출한다.
    부팅 시 ``upstream`` 설정 (yaml 또는 DB) → 각각의 외부 서버에 ``initialize``
    + ``tools/list`` 호출 → 받은 tool 들을 ``alias__tool_name`` 으로 네임스페이스
    부여 후 FastMCP 에 동적 등록한다. 등록된 도구는 호출 시점에 ``dispatch_call``
    이 원본 upstream 에 위임하고, 응답을 그대로 반환한다.

MVP 범위 (Phase 1):
    - HTTP (Streamable MCP) transport 전용. stdio 는 Phase 2 → NotImplementedError.
    - 부팅 시점 1회 등록 (헬스체크 worker 는 Phase 2).
    - per-upstream 단일 client 풀 (재연결은 호출 실패 시 lazy).
    - 호출당 ``mcp_proxy_calls`` 감사 행 1행 INSERT.

비활성:
    env ``AIDH_MCP_FEDERATION=off`` → 빈 list 반환 (회귀 안전망).

설정 우선순위:
    1. env ``AIDH_UPSTREAM_CONFIG`` (yaml 경로) — 지정되면 그것만.
    2. DB ``mcp_upstreams`` 테이블 — env 미지정 시 폴백.
    3. 둘 다 없으면 no-op (빈 list 반환).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수 / 정책
# ---------------------------------------------------------------------------
# 2~31 chars (1 lead + 1~30 tail) — 'hq' 같은 짧은 별칭 허용.
ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
NAMESPACE_SEP = "__"
DEFAULT_CALL_TIMEOUT = 60.0          # 단일 호출 read timeout (sec)
DEFAULT_INIT_TIMEOUT = 10.0          # initialize + list_tools timeout (sec)

# AIDataHub 자체 (built-in) 도구 이름 — federation tool 과 충돌 검사 시 사용.
# 동적 확장될 수 있으므로 실시간 검사는 ``_collect_existing_tool_names(mcp)`` 가 수행.
_BUILTIN_TOOL_NAMES: set[str] = {
    "discover", "list_agents", "recommend_agents",
    "get_agent_session",
    "agent_search", "semantic_search", "hybrid_search",
    "fts_search", "tag_search",
    "get_record", "get_record_sections",
    "get_context_bundle",
}


# ---------------------------------------------------------------------------
# 에러 코드 카탈로그 (wave-6 plan §8)
# ---------------------------------------------------------------------------
ERR_UPSTREAM_UNREACHABLE   = "UPSTREAM_UNREACHABLE"
ERR_UPSTREAM_AUTH_FAILED   = "UPSTREAM_AUTH_FAILED"
ERR_UPSTREAM_TIMEOUT       = "UPSTREAM_TIMEOUT"
ERR_UPSTREAM_PROTOCOL      = "UPSTREAM_PROTOCOL_ERROR"
ERR_TOOL_NOT_EXPOSED       = "TOOL_NOT_EXPOSED"
ERR_INVALID_ALIAS          = "INVALID_ALIAS"
ERR_ALIAS_TAKEN            = "ALIAS_TAKEN"
ERR_TOOL_CONFLICT          = "TOOL_CONFLICT"


class UpstreamConfigError(ValueError):
    """매니페스트 단위 pre-flight 실패. ``code`` 는 위 카탈로그 중 하나."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------
@dataclass
class UpstreamConfig:
    """단일 upstream 설정.

    HTTP transport 만 MVP. stdio 는 transport=stdio 로 받지만 register 시 reserve.
    """

    alias: str
    transport: str                              # http | stdio
    url: str | None = None                      # http 일 때
    command: str | None = None                  # stdio 일 때
    command_args: list[str] = field(default_factory=list)
    auth: dict[str, Any] | None = None          # {type: "bearer", env_var: "X_TOKEN"}
    description_prefix: str = ""
    tls_verify: bool = True
    enabled: bool = True
    rate_limit_per_min: int = 100
    env_passthrough: list[str] = field(default_factory=list)


@dataclass
class _RegisteredUpstream:
    """등록 시점 메타 — dispatch_call 이 alias → 연결 정보로 lookup 한다."""

    config: UpstreamConfig
    exposed_tools: list[str] = field(default_factory=list)
    raw_to_exposed: dict[str, str] = field(default_factory=dict)
    last_health_status: str = "unknown"
    last_tool_count: int = 0


# 모듈 레벨 풀 — 호출당 새 connection 회피.
# key=alias, value=_RegisteredUpstream
_clients: dict[str, _RegisteredUpstream] = {}


# ---------------------------------------------------------------------------
# 검증 / 네임스페이스
# ---------------------------------------------------------------------------
def _namespace(alias: str, tool_name: str) -> str:
    """``alias`` + ``tool_name`` → ``alias__tool_name`` (double underscore)."""
    if not ALIAS_RE.match(alias):
        raise UpstreamConfigError(
            ERR_INVALID_ALIAS,
            f"alias {alias!r} violates regex {ALIAS_RE.pattern}",
        )
    if not tool_name:
        raise UpstreamConfigError(ERR_TOOL_CONFLICT, "tool_name is empty")
    return f"{alias}{NAMESPACE_SEP}{tool_name}"


def _collect_existing_tool_names(mcp: Any) -> set[str]:
    """현재 FastMCP 에 등록된 tool 이름 셋. mcp=None 이면 built-in set 만 반환."""
    names: set[str] = set(_BUILTIN_TOOL_NAMES)
    if mcp is None:
        return names
    try:
        # FastMCP._tool_manager._tools: dict[name, Tool]
        tm = getattr(mcp, "_tool_manager", None)
        if tm is not None:
            inner = getattr(tm, "_tools", None)
            if isinstance(inner, dict):
                names.update(inner.keys())
    except Exception:  # pragma: no cover — defensive
        pass
    return names


def validate_upstream_config(raw: dict[str, Any]) -> UpstreamConfig:
    """raw dict → UpstreamConfig (pre-flight 단계: 형식 + alias regex)."""
    alias = str(raw.get("alias") or "").strip()
    if not ALIAS_RE.match(alias):
        raise UpstreamConfigError(
            ERR_INVALID_ALIAS,
            f"alias {alias!r} must match {ALIAS_RE.pattern} "
            f"(lowercase, 3~31 chars, snake_case)",
        )

    transport = str(raw.get("transport") or "http").lower()
    if transport not in ("http", "stdio"):
        raise UpstreamConfigError(
            ERR_UPSTREAM_PROTOCOL,
            f"transport must be http|stdio, got {transport!r}",
        )

    url = raw.get("url")
    command = raw.get("command")
    if transport == "http" and not url:
        raise UpstreamConfigError(
            ERR_UPSTREAM_PROTOCOL,
            f"alias {alias!r}: transport=http requires url",
        )
    if transport == "stdio" and not command:
        raise UpstreamConfigError(
            ERR_UPSTREAM_PROTOCOL,
            f"alias {alias!r}: transport=stdio requires command",
        )

    command_args = list(raw.get("command_args") or [])
    auth = raw.get("auth") or None
    if auth is not None and not isinstance(auth, dict):
        raise UpstreamConfigError(
            ERR_UPSTREAM_PROTOCOL,
            f"alias {alias!r}: auth must be a mapping or null",
        )

    return UpstreamConfig(
        alias=alias,
        transport=transport,
        url=str(url) if url else None,
        command=str(command) if command else None,
        command_args=[str(a) for a in command_args],
        auth=auth,
        description_prefix=str(raw.get("description_prefix") or ""),
        tls_verify=bool(raw.get("tls_verify", True)),
        enabled=bool(raw.get("enabled", True)),
        rate_limit_per_min=int(raw.get("rate_limit_per_min") or 100),
        env_passthrough=[str(e) for e in (raw.get("env_passthrough") or [])],
    )


def validate_no_alias_collision(configs: list[UpstreamConfig]) -> None:
    """같은 alias 가 두 번 이상 등장하면 ALIAS_TAKEN."""
    seen: set[str] = set()
    for c in configs:
        if c.alias in seen:
            raise UpstreamConfigError(
                ERR_ALIAS_TAKEN,
                f"alias {c.alias!r} appears more than once in config",
            )
        seen.add(c.alias)


def check_tool_conflict(
    alias: str, raw_tool_name: str, existing_names: set[str]
) -> str:
    """``alias__tool`` 이름이 기존 (built-in + 다른 upstream) 과 충돌하면 TOOL_CONFLICT."""
    exposed = _namespace(alias, raw_tool_name)
    if exposed in existing_names:
        raise UpstreamConfigError(
            ERR_TOOL_CONFLICT,
            f"exposed tool {exposed!r} already exists "
            f"(built-in or another upstream)",
        )
    # raw_tool_name 자체가 우리 built-in 14종 중 하나와 동일한 경우도 거부 —
    # alias 가 비어 있는 경로(잘못 호출)에 대한 방어.
    if raw_tool_name in _BUILTIN_TOOL_NAMES and alias == "":
        raise UpstreamConfigError(
            ERR_TOOL_CONFLICT,
            f"raw tool {raw_tool_name!r} collides with built-in",
        )
    return exposed


# ---------------------------------------------------------------------------
# 설정 로더 — yaml 또는 DB
# ---------------------------------------------------------------------------
def _load_config_from_yaml(path: Path) -> list[UpstreamConfig]:
    """``upstream_mcps.yaml`` → ``list[UpstreamConfig]``."""
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "mcp_federation yaml config 사용에는 PyYAML 이 필요"
        ) from e
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise UpstreamConfigError(
            ERR_UPSTREAM_PROTOCOL,
            f"upstream config root must be a mapping: {path}",
        )

    raw_list = data.get("upstreams") or []
    if not isinstance(raw_list, list):
        raise UpstreamConfigError(
            ERR_UPSTREAM_PROTOCOL,
            "upstreams must be a list",
        )

    out: list[UpstreamConfig] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        out.append(validate_upstream_config(raw))
    validate_no_alias_collision(out)
    return out


async def _load_config_from_db(session: Any) -> list[UpstreamConfig]:
    """``mcp_upstreams`` 테이블 → ``list[UpstreamConfig]`` (enabled 우선, 전부 반환).

    호출 측이 enabled 필터는 직접 적용한다.
    """
    from sqlalchemy import select
    from ..db.models import MCPUpstream

    rows = (
        await session.execute(select(MCPUpstream).order_by(MCPUpstream.alias))
    ).scalars().all()
    out: list[UpstreamConfig] = []
    for r in rows:
        try:
            cfg = validate_upstream_config(
                {
                    "alias": r.alias,
                    "transport": r.transport,
                    "url": r.url,
                    "command": r.command,
                    "command_args": r.command_args or [],
                    "auth": r.auth or None,
                    "description_prefix": r.description_prefix or "",
                    "tls_verify": r.tls_verify,
                    "enabled": r.enabled,
                    "rate_limit_per_min": r.rate_limit_per_min,
                }
            )
        except UpstreamConfigError as e:
            log.warning("mcp_federation: skip db row %s — %s", r.alias, e)
            continue
        out.append(cfg)
    return out


# ---------------------------------------------------------------------------
# Auth 헤더 빌드
# ---------------------------------------------------------------------------
def _build_auth_headers(cfg: UpstreamConfig) -> dict[str, str]:
    """auth 설정 → HTTP 헤더 dict (실토큰은 env 에서 읽음)."""
    headers: dict[str, str] = {}
    if not cfg.auth:
        return headers
    a_type = str(cfg.auth.get("type") or "").lower()
    env_var = str(cfg.auth.get("env_var") or "")
    if a_type == "bearer" and env_var:
        token = os.environ.get(env_var)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            log.warning(
                "mcp_federation: alias=%s bearer env_var %s not set",
                cfg.alias, env_var,
            )
    return headers


# ---------------------------------------------------------------------------
# Upstream 연결 — tools/list (등록 시) 와 call_tool (호출 시) 의 공용 helper
# ---------------------------------------------------------------------------
async def _open_session(cfg: UpstreamConfig, timeout: float):
    """upstream 에 streamable-http 연결 후 ClientSession initialize 까지 수행.

    ``async with _open_session(cfg) as session:`` 패턴으로 호출자가 책임 종료.
    test 환경은 monkeypatch 로 본 함수를 통째로 fake 한다.
    """
    if cfg.transport != "http":
        raise NotImplementedError(
            f"transport={cfg.transport} not yet supported (Phase 2: stdio)"
        )
    if not cfg.url:
        raise UpstreamConfigError(
            ERR_UPSTREAM_PROTOCOL,
            f"alias {cfg.alias!r}: missing url for http transport",
        )

    from contextlib import asynccontextmanager

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = _build_auth_headers(cfg)

    @asynccontextmanager
    async def _ctx():
        async with streamablehttp_client(
            cfg.url,
            headers=headers or None,
            timeout=timedelta(seconds=timeout),
        ) as (read, write, _get_sid):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    return _ctx()


async def _fetch_remote_tools(cfg: UpstreamConfig) -> list[dict[str, Any]]:
    """upstream tools/list 호출 → ``[{name, description}, ...]`` 반환.

    에러는 ``UpstreamConfigError`` 로 표준화 (UNREACHABLE / AUTH_FAILED / PROTOCOL).
    """
    try:
        async with await _open_session(cfg, timeout=DEFAULT_INIT_TIMEOUT) as session:
            result = await session.list_tools()
    except asyncio.TimeoutError as e:
        raise UpstreamConfigError(
            ERR_UPSTREAM_TIMEOUT,
            f"alias {cfg.alias!r}: list_tools timeout",
        ) from e
    except NotImplementedError:
        raise
    except Exception as e:
        msg = str(e).lower()
        if "401" in msg or "403" in msg or "unauthor" in msg:
            raise UpstreamConfigError(
                ERR_UPSTREAM_AUTH_FAILED,
                f"alias {cfg.alias!r}: auth failed ({e})",
            ) from e
        # 그 외 — 연결 실패로 분류
        raise UpstreamConfigError(
            ERR_UPSTREAM_UNREACHABLE,
            f"alias {cfg.alias!r}: list_tools failed ({e})",
        ) from e

    out: list[dict[str, Any]] = []
    for t in (result.tools or []):
        out.append(
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": getattr(t, "inputSchema", None) or {},
            }
        )
    return out


# ---------------------------------------------------------------------------
# Wrapper handler 생성 — wave-4 _make_handler 패턴 참고
# ---------------------------------------------------------------------------
def _make_wrapper(alias: str, raw_tool: str, input_schema: dict[str, Any]):
    """upstream tool 1개를 FastMCP 에 등록하기 위한 합성 시그니처 async handler.

    ``input_schema`` (JSON Schema) 의 properties 를 키워드 인자로 노출.
    호출 시 ``dispatch_call(alias, raw_tool, kwargs)`` 위임.
    """
    params: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {"return": dict}

    props = (input_schema or {}).get("properties") or {}
    required = set((input_schema or {}).get("required") or [])

    _jstype_map = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    for prop_name, prop_spec in props.items():
        pn = str(prop_name)
        py_t: Any = _jstype_map.get(
            (prop_spec or {}).get("type", "string"), Any
        )
        if pn in required:
            params.append(
                inspect.Parameter(
                    pn,
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    annotation=py_t,
                )
            )
        else:
            params.append(
                inspect.Parameter(
                    pn,
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    annotation=py_t,
                    default=None,
                )
            )
        annotations[pn] = py_t

    # input_schema 가 비어있는 경우 — 임의 kwargs 허용.
    if not params:
        params.append(
            inspect.Parameter(
                "kwargs",
                kind=inspect.Parameter.VAR_KEYWORD,
                annotation=Any,
            )
        )

    sig = inspect.Signature(parameters=params, return_annotation=dict)

    async def wrapper(**kwargs: Any) -> dict[str, Any]:
        # None default 는 미지정과 동일하게 처리 — upstream 으로 그대로 보내지 않음.
        cleaned = {k: v for k, v in kwargs.items() if v is not None}
        return await dispatch_call(alias, raw_tool, cleaned)

    wrapper.__name__ = f"{alias}__{raw_tool}"
    wrapper.__signature__ = sig  # type: ignore[attr-defined]
    wrapper.__annotations__ = annotations
    wrapper.__doc__ = f"[federation:{alias}] proxy to {raw_tool}"
    # 디버깅용 메타 (closure 안에서 접근 시 사용)
    wrapper._aidh_upstream_alias = alias  # type: ignore[attr-defined]
    wrapper._aidh_upstream_tool = raw_tool  # type: ignore[attr-defined]
    return wrapper


# ---------------------------------------------------------------------------
# Dispatch — proxy 호출 본체
# ---------------------------------------------------------------------------
async def dispatch_call(
    alias: str,
    raw_tool: str,
    args: dict[str, Any],
    timeout: float = DEFAULT_CALL_TIMEOUT,
) -> dict[str, Any]:
    """upstream 호출 — 결과를 ``{ok, result, error_code?}`` 형태로 정규화.

    호출 1건당 ``mcp_proxy_calls`` 행 1개 적재 (best-effort).
    """
    reg = _clients.get(alias)
    if reg is None:
        return _error_response(
            alias, raw_tool, ERR_TOOL_NOT_EXPOSED,
            f"alias {alias!r} not registered",
        )

    cfg = reg.config
    exposed = reg.raw_to_exposed.get(raw_tool) or _namespace(alias, raw_tool)

    t0 = time.monotonic()
    status = "ok"
    error_code: str | None = None
    result_payload: dict[str, Any] = {}

    try:
        async with await _open_session(cfg, timeout=timeout) as session:
            call_result = await asyncio.wait_for(
                session.call_tool(
                    raw_tool,
                    arguments=args or None,
                ),
                timeout=timeout,
            )
        # CallToolResult → dict 직렬화 (model_dump 사용)
        if hasattr(call_result, "model_dump"):
            result_payload = call_result.model_dump(mode="json")
        else:
            result_payload = {"raw": str(call_result)}
        if getattr(call_result, "isError", False):
            status = "upstream_error"
            error_code = ERR_UPSTREAM_PROTOCOL
    except asyncio.TimeoutError:
        status = "upstream_timeout"
        error_code = ERR_UPSTREAM_TIMEOUT
        result_payload = {"error": "upstream timeout"}
    except UpstreamConfigError as e:
        status = "config_error"
        error_code = e.code
        result_payload = {"error": str(e)}
    except NotImplementedError as e:
        status = "not_implemented"
        error_code = ERR_UPSTREAM_PROTOCOL
        result_payload = {"error": str(e)}
    except Exception as e:
        msg = str(e).lower()
        if "401" in msg or "403" in msg or "unauthor" in msg:
            status = "auth_failed"
            error_code = ERR_UPSTREAM_AUTH_FAILED
        else:
            status = "upstream_error"
            error_code = ERR_UPSTREAM_UNREACHABLE
        result_payload = {"error": str(e)}

    latency_ms = int((time.monotonic() - t0) * 1000)

    # 감사 로그 — best-effort, 실패해도 호출 응답에는 영향 없음
    try:
        await _audit_log_call(
            session=None,
            alias=alias,
            raw_tool=raw_tool,
            exposed_tool=exposed,
            latency_ms=latency_ms,
            status=status,
            error_code=error_code,
        )
    except Exception:  # pragma: no cover — 감사 실패는 silent
        log.exception("mcp_federation: audit log failed for %s/%s", alias, raw_tool)

    return {
        "ok": error_code is None,
        "alias": alias,
        "tool": raw_tool,
        "latency_ms": latency_ms,
        "result": result_payload,
        "error_code": error_code,
    }


# ---------------------------------------------------------------------------
# 감사 로그
# ---------------------------------------------------------------------------
async def _audit_log_call(
    session: Any,
    alias: str,
    raw_tool: str,
    exposed_tool: str,
    latency_ms: int,
    status: str,
    error_code: str | None = None,
    caller: str | None = None,
    client_ip: str | None = None,
    request_id: str | None = None,
) -> None:
    """``mcp_proxy_calls`` 1행 INSERT.

    ``session`` 이 주어지면 그 트랜잭션에 추가, 아니면 ``SessionLocal()`` 신규 열기.
    test 환경은 session 을 명시 전달해서 in-memory SQLite 에 적재 확인.
    """
    from ..db.models import MCPProxyCall

    row = MCPProxyCall(
        caller=caller,
        upstream_alias=alias,
        raw_tool_name=raw_tool,
        exposed_tool_name=exposed_tool,
        latency_ms=int(latency_ms),
        status=status,
        error_code=error_code,
        client_ip=client_ip,
        request_id=request_id,
    )

    if session is not None:
        session.add(row)
        try:
            await session.flush()
        except Exception:
            pass
        return

    # 운영 경로 — 별도 트랜잭션
    try:
        from ..db.base import SessionLocal
        async with SessionLocal() as s:
            s.add(row)
            await s.commit()
    except Exception:  # pragma: no cover — DB 미가용 환경 (test 등)
        log.debug("mcp_federation: audit log skipped (no DB)")


# ---------------------------------------------------------------------------
# 에러 응답 헬퍼
# ---------------------------------------------------------------------------
def _error_response(
    alias: str, raw_tool: str, code: str, message: str
) -> dict[str, Any]:
    return {
        "ok": False,
        "alias": alias,
        "tool": raw_tool,
        "latency_ms": 0,
        "result": {"error": message},
        "error_code": code,
    }


# ---------------------------------------------------------------------------
# 부팅 시 일괄 등록 — mcp_runtime.py 가 호출
# ---------------------------------------------------------------------------
def register_all_upstreams(mcp: Any) -> list[str]:
    """yaml 또는 DB 의 upstream 설정 → FastMCP 에 wrapper tool 동적 등록.

    Returns:
        등록 성공한 ``alias__tool_name`` 목록.

    env ``AIDH_MCP_FEDERATION=off`` 시 즉시 빈 리스트.
    설정 자체가 없으면 (yaml 미지정 + DB 미가용) 빈 리스트 (no-op).
    """
    if (os.environ.get("AIDH_MCP_FEDERATION") or "").lower() == "off":
        log.info("mcp_federation: disabled by env")
        return []

    # 설정 로드 — yaml 우선, 없으면 DB.
    configs: list[UpstreamConfig] = []
    yaml_path = os.environ.get("AIDH_UPSTREAM_CONFIG")
    if yaml_path:
        p = Path(yaml_path)
        if p.exists():
            try:
                configs = _load_config_from_yaml(p)
            except Exception as e:
                log.warning("mcp_federation: yaml load failed (%s) — %s", p, e)
                return []
        else:
            log.warning("mcp_federation: AIDH_UPSTREAM_CONFIG=%s not found", p)
            return []
    else:
        # DB 폴백 — sync 호출이므로 asyncio.run 으로 short-lived 루프 사용.
        try:
            configs = asyncio.run(_load_configs_from_db_or_empty())
        except Exception as e:
            log.debug("mcp_federation: db load skipped — %s", e)
            return []

    if not configs:
        log.info("mcp_federation: no upstreams configured")
        return []

    # enabled 만 시도. 충돌 검사용 기존 이름 셋.
    existing = _collect_existing_tool_names(mcp)
    registered_names: list[str] = []

    for cfg in configs:
        if not cfg.enabled:
            log.info("mcp_federation: alias=%s disabled, skip", cfg.alias)
            continue
        if cfg.transport != "http":
            log.warning(
                "mcp_federation: alias=%s transport=%s — reserved for Phase 2",
                cfg.alias, cfg.transport,
            )
            continue
        if cfg.alias in _clients:
            log.warning(
                "mcp_federation: alias=%s already registered, skip duplicate",
                cfg.alias,
            )
            continue

        try:
            tools = asyncio.run(_fetch_remote_tools(cfg))
        except UpstreamConfigError as e:
            log.warning(
                "mcp_federation: alias=%s preflight failed [%s] — %s",
                cfg.alias, e.code, e,
            )
            _clients[cfg.alias] = _RegisteredUpstream(
                config=cfg,
                last_health_status=e.code.lower(),
                last_tool_count=0,
            )
            continue
        except Exception as e:  # pragma: no cover — 안전망
            log.warning(
                "mcp_federation: alias=%s unexpected error — %s", cfg.alias, e
            )
            continue

        reg = _RegisteredUpstream(
            config=cfg, last_health_status="ok", last_tool_count=len(tools)
        )

        for t in tools:
            raw_name = t["name"]
            try:
                exposed = check_tool_conflict(cfg.alias, raw_name, existing)
            except UpstreamConfigError as e:
                log.warning(
                    "mcp_federation: alias=%s tool=%s conflict — %s",
                    cfg.alias, raw_name, e,
                )
                continue

            wrapper = _make_wrapper(cfg.alias, raw_name, t.get("input_schema") or {})
            desc = (cfg.description_prefix or "") + (t.get("description") or "")
            try:
                if mcp is not None:
                    mcp.add_tool(
                        wrapper,
                        name=exposed,
                        title=exposed,
                        description=desc or f"[federation:{cfg.alias}] {raw_name}",
                    )
            except Exception as e:  # pragma: no cover
                log.warning(
                    "mcp_federation: add_tool failed %s — %s", exposed, e
                )
                continue

            reg.exposed_tools.append(exposed)
            reg.raw_to_exposed[raw_name] = exposed
            existing.add(exposed)
            registered_names.append(exposed)

        _clients[cfg.alias] = reg
        log.info(
            "mcp_federation: alias=%s registered %d tool(s): %s",
            cfg.alias, len(reg.exposed_tools), ", ".join(reg.exposed_tools),
        )

    return registered_names


async def _load_configs_from_db_or_empty() -> list[UpstreamConfig]:
    """DB 가 닿지 않거나 테이블이 없으면 빈 리스트 (silent)."""
    try:
        from ..db.base import SessionLocal
        async with SessionLocal() as s:
            return await _load_config_from_db(s)
    except Exception as e:
        log.debug("mcp_federation: db config skip — %s", e)
        return []


# ---------------------------------------------------------------------------
# 외부 인트로스펙션 — admin route 가 status 조회에 사용
# ---------------------------------------------------------------------------
def get_registered_aliases() -> list[str]:
    """현재 등록된 alias 목록."""
    return sorted(_clients.keys())


def get_upstream_status(alias: str) -> dict[str, Any] | None:
    """단일 upstream 상태 — admin /api/mcp/upstreams 가 사용."""
    reg = _clients.get(alias)
    if reg is None:
        return None
    return {
        "alias": alias,
        "transport": reg.config.transport,
        "url": reg.config.url,
        "enabled": reg.config.enabled,
        "tls_verify": reg.config.tls_verify,
        "exposed_tools": list(reg.exposed_tools),
        "last_health_status": reg.last_health_status,
        "last_tool_count": reg.last_tool_count,
    }


def reset_clients_for_test() -> None:
    """테스트 간 상태 격리."""
    _clients.clear()


__all__ = [
    "UpstreamConfig",
    "UpstreamConfigError",
    "validate_upstream_config",
    "validate_no_alias_collision",
    "check_tool_conflict",
    "_namespace",
    "_load_config_from_yaml",
    "_load_config_from_db",
    "_audit_log_call",
    "_make_wrapper",
    "dispatch_call",
    "register_all_upstreams",
    "get_registered_aliases",
    "get_upstream_status",
    "reset_clients_for_test",
    # error codes
    "ERR_UPSTREAM_UNREACHABLE",
    "ERR_UPSTREAM_AUTH_FAILED",
    "ERR_UPSTREAM_TIMEOUT",
    "ERR_UPSTREAM_PROTOCOL",
    "ERR_TOOL_NOT_EXPOSED",
    "ERR_INVALID_ALIAS",
    "ERR_ALIAS_TAKEN",
    "ERR_TOOL_CONFLICT",
]
