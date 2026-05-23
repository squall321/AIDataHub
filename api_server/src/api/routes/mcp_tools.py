"""``/api/mcp_tools`` — Wave-5 P1 CLI binary 업로드 + 도구 관리 라우터.

엔드포인트:
    - ``POST /upload``         : bundle.zip multipart 업로드 → 동기 처리 후 결과.
    - ``GET  /jobs/{job_id}``  : MVP 는 동기라 항상 completed/failed.
    - ``GET  /``               : 등록된 도구 목록 (mcp_uploads SELECT).
    - ``GET  /{name}``         : 단일 도구 상세.
    - ``DELETE /{name}``       : deprecate (sif 보존 + deprecated_at 갱신).

MVP 정책:
    - 큐/워커 분리 없음. multipart 받자마자 process_upload 동기 실행.
    - 따라서 ``POST /upload`` 의 응답은 즉시 완료/실패 정보 포함.
    - ``job_id`` 는 응답에 포함하되 ``GET /jobs/{job_id}`` 는 같은 결과 메모리에서 반환.
"""
from __future__ import annotations

import logging
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import get_session
from api.db.models import MCPUpload, MCPUploadHistory
from api.services import mcp_upload_svc

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp_tools", tags=["wave-5"])


# ---------------------------------------------------------------------------
# Pydantic 응답 모델
# ---------------------------------------------------------------------------
class UploadResponse(BaseModel):
    """업로드 결과 — 동기 처리 후 즉시 반환."""

    job_id: str
    status: str = Field(..., description="completed|failed")
    name: str | None = None
    sha: str | None = None
    version: int | None = None
    sif_path: str | None = None
    smoke: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, Any] | None = None


class ToolInfo(BaseModel):
    """단일 도구 상세."""

    name: str
    current_sha: str
    current_version: int
    manifest: dict[str, Any]
    capabilities: dict[str, Any]
    archived_versions: list[dict[str, Any]] = Field(default_factory=list)
    registered_at: datetime | None = None
    registered_by: str | None = None
    deprecated_at: datetime | None = None


class JobStatus(BaseModel):
    job_id: str
    status: str
    step: str | None = None
    error: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# In-memory job stash (MVP — 동기 처리 결과를 잠시 메모리에 보관)
# ---------------------------------------------------------------------------
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_MAX = 200


def _stash_job(job_id: str, payload: dict[str, Any]) -> None:
    """가장 오래된 job 부터 LRU evict."""
    if len(_JOBS) >= _JOBS_MAX:
        # 가장 오래된 1건 제거 (insertion order).
        oldest = next(iter(_JOBS))
        _JOBS.pop(oldest, None)
    _JOBS[job_id] = payload


# ---------------------------------------------------------------------------
# 라우트
# ---------------------------------------------------------------------------
@router.post("/upload", response_model=UploadResponse, summary="도구 zip 업로드 (동기)")
async def upload_tool(
    bundle: UploadFile = File(..., description="도구 zip — manifest.yaml + samples/ 포함"),
    uploader: str = Form(..., description="업로더 식별자 (감사 추적)"),
    dry_run: bool = Form(False, description="true 면 DB INSERT skip"),
    session: AsyncSession = Depends(get_session),
) -> UploadResponse:
    """multipart upload → ``process_upload`` 동기 호출 → DB INSERT (dry_run=false)."""
    job_id = uuid.uuid4().hex

    # ── 1. tempfile 로 zip 저장 ──
    suffix = ".zip"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = Path(tmp.name)
        data = await bundle.read()
        tmp.write(data)

    try:
        # ── 2. 파이프라인 실행 ──
        try:
            result = mcp_upload_svc.process_upload(tmp_path, uploader=uploader)
        except mcp_upload_svc.UploadError as e:
            payload = {
                "job_id": job_id,
                "status": "failed",
                "error": {
                    "code": e.code,
                    "message_ko": e.message_ko,
                    "suggested_action": e.suggested_action,
                },
            }
            _stash_job(job_id, payload)
            return UploadResponse(**payload)
        except NotImplementedError as e:
            payload = {
                "job_id": job_id,
                "status": "failed",
                "error": {
                    "code": "UNSUPPORTED_RUNTIME",
                    "message_ko": str(e),
                    "suggested_action": "Python runtime 도구로 재구성해 업로드.",
                },
            }
            _stash_job(job_id, payload)
            return UploadResponse(**payload)
        except Exception as e:  # pragma: no cover — defensive
            log.exception("upload pipeline failed")
            payload = {
                "job_id": job_id,
                "status": "failed",
                "error": {
                    "code": "INTERNAL",
                    "message_ko": f"내부 오류: {e}",
                    "suggested_action": "운영자에게 build_log 와 함께 문의.",
                },
            }
            _stash_job(job_id, payload)
            return UploadResponse(**payload)

        # ── 3. smoke 실패 → DB 비저장, history 만 ──
        if not result.get("ok"):
            payload = {
                "job_id": job_id,
                "status": "failed",
                "name": result.get("name"),
                "sha": result.get("sha"),
                "smoke": result.get("smoke", []),
                "error": {
                    "code": "SMOKE_EXIT_MISMATCH",
                    "message_ko": "smoke 실패 — sample 확인 또는 도구 로직 점검.",
                    "suggested_action": "smoke[].matched_exit / matched_contains 확인.",
                },
            }
            if not dry_run:
                await _insert_history(session, result, uploader=uploader, registered=False)
            _stash_job(job_id, payload)
            return UploadResponse(**payload)

        # ── 4. version bump / archive (mcp_uploads UPSERT) ──
        version_assigned = 1
        if not dry_run:
            version_assigned = await _upsert_upload(session, result, uploader=uploader)
            await _insert_history(
                session, result, uploader=uploader, registered=True,
                version=version_assigned,
            )
            await session.commit()

        payload = {
            "job_id": job_id,
            "status": "completed",
            "name": result["name"],
            "sha": result["sha"],
            "version": version_assigned,
            "sif_path": result["sif_path"],
            "smoke": result["smoke"],
        }
        _stash_job(job_id, payload)
        return UploadResponse(**payload)
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass


@router.get("/jobs/{job_id}", response_model=JobStatus, summary="job 상태 (MVP 즉시 완료)")
async def get_job(job_id: str) -> JobStatus:
    payload = _JOBS.get(job_id)
    if payload is None:
        raise HTTPException(404, detail=f"job not found: {job_id}")
    return JobStatus(
        job_id=job_id,
        status=payload.get("status", "unknown"),
        step=None,
        error=payload.get("error"),
    )


@router.get("/", summary="등록된 도구 목록")
async def list_tools(
    session: AsyncSession = Depends(get_session),
) -> list[ToolInfo]:
    rows = (await session.execute(select(MCPUpload))).scalars().all()
    return [
        ToolInfo(
            name=r.name,
            current_sha=r.current_sha,
            current_version=r.current_version,
            manifest=dict(r.manifest or {}),
            capabilities=dict(r.capabilities or {}),
            archived_versions=list(r.archived_versions or []),
            registered_at=r.registered_at,
            registered_by=r.registered_by,
            deprecated_at=r.deprecated_at,
        )
        for r in rows
    ]


@router.get("/{name}", response_model=ToolInfo, summary="단일 도구 상세")
async def get_tool(
    name: str,
    session: AsyncSession = Depends(get_session),
) -> ToolInfo:
    row = (
        await session.execute(select(MCPUpload).where(MCPUpload.name == name))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail=f"tool not found: {name}")
    return ToolInfo(
        name=row.name,
        current_sha=row.current_sha,
        current_version=row.current_version,
        manifest=dict(row.manifest or {}),
        capabilities=dict(row.capabilities or {}),
        archived_versions=list(row.archived_versions or []),
        registered_at=row.registered_at,
        registered_by=row.registered_by,
        deprecated_at=row.deprecated_at,
    )


@router.delete("/{name}", status_code=204, summary="도구 deprecate (sif 보존)")
async def deprecate_tool(
    name: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    row = (
        await session.execute(select(MCPUpload).where(MCPUpload.name == name))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail=f"tool not found: {name}")
    await session.execute(
        update(MCPUpload)
        .where(MCPUpload.name == name)
        .values(deprecated_at=datetime.now(timezone.utc))
    )
    await session.commit()


# ---------------------------------------------------------------------------
# 내부 — DB UPSERT helpers
# ---------------------------------------------------------------------------
async def _upsert_upload(
    session: AsyncSession,
    result: dict[str, Any],
    *,
    uploader: str,
) -> int:
    """mcp_uploads INSERT or version bump.

    Returns: 할당된 current_version.
    """
    name = result["name"]
    sha = result["sha"]
    manifest_dict = result["manifest"]

    existing = (
        await session.execute(select(MCPUpload).where(MCPUpload.name == name))
    ).scalar_one_or_none()

    if existing is None:
        row = MCPUpload(
            name=name,
            current_sha=sha,
            current_version=1,
            manifest=manifest_dict,
            capabilities={"persist_output": bool(
                manifest_dict.get("persist_output", {}).get("enabled")
            )},
            archived_versions=[],
            registered_by=uploader,
        )
        session.add(row)
        await session.flush()
        return 1

    if existing.current_sha == sha:
        # 동일 sha 재업로드 — version bump 없음 (idempotent).
        return existing.current_version

    # 다른 sha → version bump + 이전 archive
    new_version = existing.current_version + 1
    archived = list(existing.archived_versions or [])
    archived.append({
        "version": existing.current_version,
        "sha": existing.current_sha,
        "archived_at": datetime.now(timezone.utc).isoformat(),
    })
    existing.current_sha = sha
    existing.current_version = new_version
    existing.manifest = manifest_dict
    existing.archived_versions = archived
    existing.registered_by = uploader
    existing.registered_at = datetime.now(timezone.utc)
    existing.deprecated_at = None
    await session.flush()
    return new_version


async def _insert_history(
    session: AsyncSession,
    result: dict[str, Any],
    *,
    uploader: str,
    registered: bool,
    version: int | None = None,
) -> None:
    history = MCPUploadHistory(
        name=result.get("name") or "?",
        sha=result.get("sha") or "0" * 64,
        version=version or 1,
        uploaded_by=uploader,
        smoke_result={"results": result.get("smoke") or []},
        sif_path=result.get("sif_path"),
        registered=registered,
    )
    session.add(history)
    await session.flush()


__all__ = ["router"]
