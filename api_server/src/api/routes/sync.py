"""``/api/sync/*`` — 외부 데이터 소스 정기 pull 동기화 관리.

엔드포인트:
    GET    /api/sync/sources               — 등록된 source 목록
    POST   /api/sync/sources               — 새 source 등록
    GET    /api/sync/sources/{id}          — 단일 source 조회
    PATCH  /api/sync/sources/{id}          — 설정 변경
    DELETE /api/sync/sources/{id}          — 삭제
    POST   /api/sync/sources/{id}/run      — 수동 실행 (또는 cron 트리거)
    POST   /api/sync/sources/{id}/verify   — dry-run (매핑 검증만)
    GET    /api/sync/sources/{id}/runs     — 실행 이력
    GET    /api/sync/runs/{run_id}         — 단일 run 상세 (dead_letter 포함)

운영:
    실 worker 는 외부 cron 이 ``curl -X POST .../run`` 으로 트리거. AX Hub
    서버는 인프라 의존성 없음.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import Principal, require_api_key
from api.db.base import get_session
from api.db.models import SyncRun, SyncSource
from api.services import sync_svc
from api.services.url_safety import validate_external_url

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


# ===========================================================================
# Pydantic 모델
# ===========================================================================
class SyncSourceIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=40)
    description: str = ""
    base_url: str
    api_key: str | None = None
    auth_header: str = "X-API-Key"
    list_endpoint: str
    list_method: str = "GET"
    detail_endpoint: str | None = None

    cursor_param: str = "cursor"
    since_param: str = "since"
    limit_param: str = "limit"
    page_size: int = 200

    max_rps: float = 2.0
    retry_max: int = 3
    retry_backoff_sec: float = 2.0
    trust_pii_masked: bool = False

    mapping_rules: dict[str, Any] = Field(default_factory=dict)

    schedule_cron: str | None = None
    enabled: bool = True


class SyncSourcePatch(BaseModel):
    description: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    auth_header: str | None = None
    list_endpoint: str | None = None
    list_method: str | None = None
    detail_endpoint: str | None = None
    cursor_param: str | None = None
    since_param: str | None = None
    limit_param: str | None = None
    page_size: int | None = None
    max_rps: float | None = None
    retry_max: int | None = None
    retry_backoff_sec: float | None = None
    trust_pii_masked: bool | None = None
    mapping_rules: dict[str, Any] | None = None
    schedule_cron: str | None = None
    enabled: bool | None = None
    # 운영자가 강제로 cursor / last_sync_at 리셋할 때 사용
    cursor: str | None = None
    reset_cursor: bool | None = None


class SyncSourceOut(BaseModel):
    id: int
    name: str
    description: str
    base_url: str
    has_api_key: bool
    list_endpoint: str
    page_size: int
    max_rps: float
    trust_pii_masked: bool
    mapping_rules: dict[str, Any]
    cursor: str | None
    last_sync_at: str | None
    last_status: str
    last_error: str | None
    last_fetched_count: int
    last_imported_count: int
    schedule_cron: str | None
    enabled: bool


def _to_out(s: SyncSource) -> SyncSourceOut:
    return SyncSourceOut(
        id=s.id,
        name=s.name,
        description=s.description,
        base_url=s.base_url,
        has_api_key=bool(s.api_key),
        list_endpoint=s.list_endpoint,
        page_size=s.page_size,
        max_rps=s.max_rps,
        trust_pii_masked=s.trust_pii_masked,
        mapping_rules=dict(s.mapping_rules or {}),
        cursor=s.cursor,
        last_sync_at=s.last_sync_at.isoformat() if s.last_sync_at else None,
        last_status=s.last_status,
        last_error=s.last_error,
        last_fetched_count=s.last_fetched_count,
        last_imported_count=s.last_imported_count,
        schedule_cron=s.schedule_cron,
        enabled=s.enabled,
    )


# ===========================================================================
# CRUD
# ===========================================================================
@router.get("/sources", response_model=list[SyncSourceOut])
async def list_sources(
    enabled: bool | None = Query(None),
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_api_key),
) -> list[SyncSourceOut]:
    stmt = select(SyncSource)
    if enabled is not None:
        stmt = stmt.where(SyncSource.enabled == enabled)
    rows = (await session.execute(stmt.order_by(SyncSource.id))).scalars().all()
    return [_to_out(s) for s in rows]


@router.post(
    "/sources",
    response_model=SyncSourceOut,
    status_code=201,
)
async def create_source(
    payload: SyncSourceIn,
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_api_key),
) -> SyncSourceOut:
    # SSRF 방지 — base_url 안전성 검증
    ok, reason = validate_external_url(payload.base_url)
    if not ok:
        raise HTTPException(status_code=400, detail=f"base_url rejected: {reason}")

    # 중복 name 검증
    existing = (
        await session.execute(select(SyncSource).where(SyncSource.name == payload.name))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"source name already exists: {payload.name}")

    s = SyncSource(**payload.model_dump())
    session.add(s)
    await session.commit()
    await session.refresh(s)
    log.info(
        "sync_source created by actor=%s: name=%s base_url=%s has_api_key=%s",
        _principal.name, s.name, s.base_url, bool(s.api_key),
    )
    return _to_out(s)


@router.get("/sources/{source_id}", response_model=SyncSourceOut)
async def get_source(
    source_id: int,
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_api_key),
) -> SyncSourceOut:
    s = (
        await session.execute(select(SyncSource).where(SyncSource.id == source_id))
    ).scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail=f"sync_source not found: id={source_id}")
    return _to_out(s)


@router.patch("/sources/{source_id}", response_model=SyncSourceOut)
async def patch_source(
    source_id: int,
    patch: SyncSourcePatch,
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_api_key),
) -> SyncSourceOut:
    s = (
        await session.execute(select(SyncSource).where(SyncSource.id == source_id))
    ).scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail=f"sync_source not found: id={source_id}")

    data = patch.model_dump(exclude_unset=True)
    if data.pop("reset_cursor", False):
        s.cursor = None
        s.last_sync_at = None
    # SSRF 방지 — base_url 변경 시 재검증
    if "base_url" in data and data["base_url"]:
        ok, reason = validate_external_url(data["base_url"])
        if not ok:
            raise HTTPException(status_code=400, detail=f"base_url rejected: {reason}")
    # 민감 변경 audit (api_key, base_url, mapping_rules, enabled)
    sensitive_keys = {"api_key", "base_url", "mapping_rules", "enabled"}
    audited_changes = [k for k in data.keys() if k in sensitive_keys]
    for k, v in data.items():
        if v is None:
            continue
        setattr(s, k, v)
    await session.commit()
    if audited_changes:
        log.info(
            "sync_source %s patched by actor=%s — sensitive fields: %s",
            s.name, _principal.name, ", ".join(audited_changes),
        )
    await session.refresh(s)
    return _to_out(s)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(
    source_id: int,
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_api_key),
) -> None:
    s = (
        await session.execute(select(SyncSource).where(SyncSource.id == source_id))
    ).scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail=f"sync_source not found: id={source_id}")
    await session.delete(s)
    await session.commit()


# ===========================================================================
# 실행 (manual or cron trigger)
# ===========================================================================
@router.post("/sources/{source_id}/run", summary="동기화 실행 (cron 또는 수동)")
async def run_source(
    source_id: int,
    trigger: str = Query("manual", description="manual | cron | webhook"),
    dry_run: bool = Query(False),
    max_pages: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_api_key),
) -> dict[str, Any]:
    """1 회 동기화 실행. 결과를 ``sync_runs`` 에 남긴다."""
    try:
        result = await sync_svc.run_sync(
            session, source_id,
            trigger=trigger,
            dry_run=dry_run,
            max_pages=max_pages,
        )
    except ValueError as exc:
        # 'busy' 응답은 409 Conflict 가 정확
        msg = str(exc)
        status = 409 if "busy" in msg or "lock" in msg.lower() else 404
        raise HTTPException(status_code=status, detail=msg) from exc
    except Exception as exc:
        log.exception("sync_run unexpected failure: source_id=%s", source_id)
        raise HTTPException(
            status_code=500,
            detail=f"sync_run failed: {type(exc).__name__}: {exc}",
        ) from exc
    return result


@router.post("/sources/{source_id}/verify", summary="매핑 검증 (dry-run)")
async def verify_source(
    source_id: int,
    max_pages: int = Query(1, ge=1, le=5),
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_api_key),
) -> dict[str, Any]:
    """첫 페이지만 가져와 매핑 결과 검증 — 저장은 안 함."""
    try:
        return await sync_svc.run_sync(
            session, source_id,
            trigger="manual",
            dry_run=True,
            max_pages=max_pages,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ===========================================================================
# 실행 이력 조회
# ===========================================================================
@router.get("/sources/{source_id}/runs")
async def list_runs(
    source_id: int,
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_api_key),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(SyncRun)
            .where(SyncRun.source_id == source_id)
            .order_by(desc(SyncRun.started_at))
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "status": r.status,
            "trigger": r.trigger,
            "fetched": r.fetched_count,
            "imported": r.imported_count,
            "updated": r.updated_count,
            "failed": r.failed_count,
            "tombstoned": r.tombstoned_count,
            "cursor_before": r.cursor_before,
            "cursor_after": r.cursor_after,
            "error": r.error,
            "dead_letter_count": len(r.dead_letter or []),
        }
        for r in rows
    ]


@router.get("/runs/{run_id}")
async def get_run(
    run_id: int,
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_api_key),
) -> dict[str, Any]:
    r = (
        await session.execute(select(SyncRun).where(SyncRun.id == run_id))
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail=f"sync_run not found: id={run_id}")
    return {
        "id": r.id,
        "source_id": r.source_id,
        "started_at": r.started_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "status": r.status,
        "trigger": r.trigger,
        "fetched": r.fetched_count,
        "imported": r.imported_count,
        "updated": r.updated_count,
        "failed": r.failed_count,
        "tombstoned": r.tombstoned_count,
        "cursor_before": r.cursor_before,
        "cursor_after": r.cursor_after,
        "error": r.error,
        "dead_letter": r.dead_letter or [],
    }


__all__ = ["router"]
