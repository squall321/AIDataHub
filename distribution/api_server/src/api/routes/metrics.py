"""``/metrics`` — Prometheus 텍스트 포맷.

ENABLE_METRICS=false 면 라우터를 등록하지 않는다 (register 측에서 처리).
"""
from __future__ import annotations

from fastapi import APIRouter
from starlette.responses import Response

from ..middleware.metrics import render_metrics

router = APIRouter(tags=["system"])


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


__all__ = ["router"]
