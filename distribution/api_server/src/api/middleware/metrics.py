"""Prometheus 메트릭 미들웨어.

- ``http_requests_total{method,path,status}`` Counter
- ``http_request_duration_seconds{method,path}`` Histogram

label cardinality 폭증을 방지하기 위해 ``path`` 는 ``request.scope['route'].path``
(라우트 템플릿 — 예: ``/api/records/{record_id}``) 로 정규화한다. 매칭되는
라우트가 없으면 ``"<unmatched>"`` 로 표기한다.

크로스플랫폼: ``prometheus-client`` 는 순수 Python.
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# 기본 레지스트리에 동일 이름 메트릭이 이미 있으면 재사용 (테스트 시 모듈 reload 안전)
def _get_or_create_counter(
    name: str, doc: str, labels: list[str], registry: CollectorRegistry
) -> Counter:
    existing = registry._names_to_collectors.get(name)  # type: ignore[attr-defined]
    if existing is not None:
        return existing  # type: ignore[return-value]
    return Counter(name, doc, labels, registry=registry)


def _get_or_create_histogram(
    name: str, doc: str, labels: list[str], registry: CollectorRegistry
) -> Histogram:
    existing = registry._names_to_collectors.get(name)  # type: ignore[attr-defined]
    if existing is not None:
        return existing  # type: ignore[return-value]
    return Histogram(name, doc, labels, registry=registry)


REQUEST_COUNT = _get_or_create_counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
    REGISTRY,
)
REQUEST_LATENCY = _get_or_create_histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    REGISTRY,
)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return "<unmatched>"


class MetricsMiddleware(BaseHTTPMiddleware):
    """요청 카운트 / 지속시간 수집."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # /metrics 자체는 메트릭 수집 대상에서 제외 (self-noise 방지)
        if request.url.path == "/metrics":
            return await call_next(request)

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.perf_counter() - start
            template = _route_template(request)
            try:
                REQUEST_COUNT.labels(
                    method=request.method, path=template, status=str(status_code)
                ).inc()
                REQUEST_LATENCY.labels(
                    method=request.method, path=template
                ).observe(duration)
            except Exception:  # pragma: no cover — metric failure must not break request
                pass


def render_metrics() -> tuple[bytes, str]:
    """현재 레지스트리 상태를 Prometheus text format 으로 직렬화."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


__all__ = [
    "REQUEST_COUNT",
    "REQUEST_LATENCY",
    "MetricsMiddleware",
    "render_metrics",
]
