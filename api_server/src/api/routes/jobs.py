"""``/api/jobs`` — 비동기 잡 큐 (인-메모리, 라이트 버전).

엔드포인트:
    - ``POST /api/jobs/embed``  — 임베딩 backfill 잡 시작.
    - ``GET  /api/jobs/{id}``   — 잡 상태/진행률/결과 조회.
    - ``GET  /api/jobs``        — 잡 목록 (필터: kind=).

자세한 설계는 ``api.services.jobs`` 참조.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..services import jobs as job_svc

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class EmbedJobRequest(BaseModel):
    """``POST /api/jobs/embed`` 요청 본문."""

    record_id: str | None = Field(
        default=None,
        description="단일 레코드 ID. 비어있고 record_ids 도 없으면 전체 미임베딩 섹션이 대상.",
    )
    record_ids: list[str] | None = Field(default=None)


@router.post(
    "/embed",
    summary="임베딩 backfill 잡 시작",
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_embed_job(req: EmbedJobRequest) -> JSONResponse:
    """임베딩 backfill 을 background task 로 등록하고 ``job_id`` 반환."""
    payload: dict[str, Any] = {}
    if req.record_id:
        payload["record_id"] = req.record_id
    if req.record_ids:
        payload["record_ids"] = list(req.record_ids)

    job = job_svc.register("embed", job_svc.embed_handler, payload=payload)
    return JSONResponse(
        content=job.to_dict(),
        status_code=status.HTTP_202_ACCEPTED,
    )


@router.get(
    "/embed",
    summary="(안내) embed 잡 시작은 POST — GET 은 메서드 착오 안내",
    status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
    include_in_schema=False,
)
async def get_embed_hint() -> JSONResponse:
    """GET /api/jobs/embed 는 아래 job_id 라우트에 잡혀 'job not found: embed'
    404 가 나왔다 — 메서드 착오를 진단할 수 없는 응답 (2026-06-09 운영 점검에서
    실제 발생). 명시적 405 + 사용법 안내로 교체."""
    return JSONResponse(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        content={
            "detail": "use POST /api/jobs/embed",
            "example": "curl -X POST .../api/jobs/embed -H 'Content-Type: application/json' -d '{}'",
        },
    )


@router.get(
    "/{job_id}",
    summary="잡 상태/결과 조회",
)
async def get_job(job_id: str) -> JSONResponse:
    job = job_svc.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    return JSONResponse(content=job.to_dict())


@router.get("", include_in_schema=False)
@router.get(
    "/",
    summary="잡 목록 조회",
)
async def list_jobs(
    kind: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=500),
) -> JSONResponse:
    items = job_svc.list_jobs(kind=kind, limit=limit)
    return JSONResponse(content={"jobs": [j.to_dict() for j in items]})


__all__ = ["router"]
