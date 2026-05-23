"""``/api/mcp/upstreams`` — Wave-6 P1 MCP federation 관리 API.

엔드포인트:
    GET    /api/mcp/upstreams              — 전체 upstream + 현재 상태 + 노출 도구
    POST   /api/mcp/upstreams              — 추가 (admin, pre-flight 통과 시 INSERT)
    PATCH  /api/mcp/upstreams/{alias}      — enabled toggle / 메타 수정
    DELETE /api/mcp/upstreams/{alias}      — 제거 (DB row 삭제 + 메모리 풀 정리)
    POST   /api/mcp/upstreams/{alias}/ping — 즉시 health check (tools/list 1회)
    GET    /api/mcp/upstreams/metrics/calls?alias=&limit=
                                            — 호출 로그 tail (mcp_proxy_calls)

본 라우터는 admin 권한 게이트가 없는 P1 상태다 (RBAC 은 wave-6 P4).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import get_session
from ..db.models import MCPProxyCall, MCPUpstream
from ..services import mcp_federation as fed

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp/upstreams", tags=["wave-6"])


# ---------------------------------------------------------------------------
# 응답 / 요청 모델
# ---------------------------------------------------------------------------
class AuthSpec(BaseModel):
    """auth 메타 — 실토큰은 ``env_var`` 에서만 읽음."""

    type: str = Field(..., description="bearer | none")
    env_var: str | None = Field(None, description="실토큰을 보관한 환경변수 이름")


class UpstreamCreateRequest(BaseModel):
    alias: str = Field(..., description="snake_case, ^[a-z][a-z0-9_]{2,30}$")
    transport: str = Field("http", description="http | stdio")
    url: str | None = None
    command: str | None = None
    command_args: list[str] = Field(default_factory=list)
    auth: AuthSpec | None = None
    description_prefix: str = ""
    tls_verify: bool = True
    enabled: bool = True
    rate_limit_per_min: int = Field(100, ge=1, le=10000)


class UpstreamPatchRequest(BaseModel):
    enabled: bool | None = None
    description_prefix: str | None = None
    rate_limit_per_min: int | None = Field(None, ge=1, le=10000)
    tls_verify: bool | None = None


class UpstreamInfo(BaseModel):
    alias: str
    transport: str
    url: str | None
    enabled: bool
    tls_verify: bool
    rate_limit_per_min: int
    description_prefix: str
    last_health_status: str | None
    last_tool_count: int | None
    exposed_tools: list[str]


class CallLogEntry(BaseModel):
    id: int
    ts: str
    caller: str | None
    upstream_alias: str
    raw_tool_name: str
    exposed_tool_name: str
    latency_ms: int
    status: str
    error_code: str | None


# ---------------------------------------------------------------------------
# 헬퍼 — DB row + 메모리 상태 결합
# ---------------------------------------------------------------------------
def _row_to_info(row: MCPUpstream) -> UpstreamInfo:
    status = fed.get_upstream_status(row.alias) or {}
    return UpstreamInfo(
        alias=row.alias,
        transport=row.transport,
        url=row.url,
        enabled=row.enabled,
        tls_verify=row.tls_verify,
        rate_limit_per_min=row.rate_limit_per_min,
        description_prefix=row.description_prefix or "",
        last_health_status=row.last_health_status
        or status.get("last_health_status"),
        last_tool_count=row.last_tool_count or status.get("last_tool_count"),
        exposed_tools=list(status.get("exposed_tools") or []),
    )


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------
@router.get("", response_model=list[UpstreamInfo])
async def list_upstreams(
    session: AsyncSession = Depends(get_session),
) -> list[UpstreamInfo]:
    """전체 upstream + 현재 상태 + 노출 도구."""
    rows = (
        await session.execute(select(MCPUpstream).order_by(MCPUpstream.alias))
    ).scalars().all()
    return [_row_to_info(r) for r in rows]


@router.post("", response_model=UpstreamInfo, status_code=201)
async def create_upstream(
    payload: UpstreamCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> UpstreamInfo:
    """upstream 추가 — pre-flight 통과 시 INSERT.

    INSERT 자체는 동기 통과 (FastMCP 동적 등록은 다음 부팅 또는 별도 ping 시점).
    P2 에서 즉시 등록 trigger 를 추가할 예정.
    """
    # pre-flight — alias regex + transport 필드 무결성
    raw = payload.model_dump()
    try:
        cfg = fed.validate_upstream_config(raw)
    except fed.UpstreamConfigError as e:
        raise HTTPException(
            status_code=400,
            detail={"error_code": e.code, "message": str(e)},
        ) from e

    # alias 중복 검사 (DB)
    existing = await session.get(MCPUpstream, cfg.alias)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": fed.ERR_ALIAS_TAKEN,
                "message": f"alias {cfg.alias!r} already exists",
            },
        )

    row = MCPUpstream(
        alias=cfg.alias,
        transport=cfg.transport,
        url=cfg.url,
        command=cfg.command,
        command_args=cfg.command_args or None,
        auth=cfg.auth,
        description_prefix=cfg.description_prefix,
        tls_verify=cfg.tls_verify,
        enabled=cfg.enabled,
        rate_limit_per_min=cfg.rate_limit_per_min,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _row_to_info(row)


@router.patch("/{alias}", response_model=UpstreamInfo)
async def patch_upstream(
    alias: str,
    payload: UpstreamPatchRequest,
    session: AsyncSession = Depends(get_session),
) -> UpstreamInfo:
    row = await session.get(MCPUpstream, alias)
    if row is None:
        raise HTTPException(status_code=404, detail=f"alias {alias!r} not found")

    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.description_prefix is not None:
        row.description_prefix = payload.description_prefix
    if payload.rate_limit_per_min is not None:
        row.rate_limit_per_min = payload.rate_limit_per_min
    if payload.tls_verify is not None:
        row.tls_verify = payload.tls_verify

    await session.commit()
    await session.refresh(row)
    return _row_to_info(row)


@router.delete("/{alias}", status_code=204)
async def delete_upstream(
    alias: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await session.get(MCPUpstream, alias)
    if row is None:
        raise HTTPException(status_code=404, detail=f"alias {alias!r} not found")
    await session.delete(row)
    await session.commit()
    # 메모리 풀에서도 제거 (다음 부팅까지 즉시 효과)
    fed._clients.pop(alias, None)
    return None


@router.post("/{alias}/ping", response_model=UpstreamInfo)
async def ping_upstream(
    alias: str,
    session: AsyncSession = Depends(get_session),
) -> UpstreamInfo:
    """즉시 health check — tools/list 호출 후 row 의 last_health_* 갱신."""
    row = await session.get(MCPUpstream, alias)
    if row is None:
        raise HTTPException(status_code=404, detail=f"alias {alias!r} not found")

    # row → UpstreamConfig 로 변환 후 fetch_remote_tools 호출
    cfg = fed.UpstreamConfig(
        alias=row.alias,
        transport=row.transport,
        url=row.url,
        command=row.command,
        command_args=row.command_args or [],
        auth=row.auth,
        description_prefix=row.description_prefix or "",
        tls_verify=row.tls_verify,
        enabled=row.enabled,
        rate_limit_per_min=row.rate_limit_per_min,
    )

    from datetime import datetime, timezone

    try:
        tools = await fed._fetch_remote_tools(cfg)
        row.last_health_status = "ok"
        row.last_tool_count = len(tools)
    except fed.UpstreamConfigError as e:
        row.last_health_status = e.code.lower()
        row.last_tool_count = 0
    except Exception as e:  # pragma: no cover
        log.warning("mcp_federation ping: alias=%s unexpected — %s", alias, e)
        row.last_health_status = "error"
        row.last_tool_count = 0

    row.last_health_check_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(row)
    return _row_to_info(row)


@router.get("/metrics/calls", response_model=list[CallLogEntry])
async def list_call_metrics(
    alias: str | None = Query(None, description="필터: upstream alias"),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[CallLogEntry]:
    """``mcp_proxy_calls`` tail — 대시보드용 호출 감사 로그."""
    stmt = select(MCPProxyCall).order_by(MCPProxyCall.id.desc()).limit(limit)
    if alias:
        stmt = stmt.where(MCPProxyCall.upstream_alias == alias)
    rows = (await session.execute(stmt)).scalars().all()

    def _iso(v: Any) -> str:
        try:
            return v.isoformat()
        except Exception:
            return str(v)

    return [
        CallLogEntry(
            id=r.id,
            ts=_iso(r.ts),
            caller=r.caller,
            upstream_alias=r.upstream_alias,
            raw_tool_name=r.raw_tool_name,
            exposed_tool_name=r.exposed_tool_name,
            latency_ms=r.latency_ms,
            status=r.status,
            error_code=r.error_code,
        )
        for r in rows
    ]


__all__ = ["router"]
