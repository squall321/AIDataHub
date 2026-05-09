"""Request ID + 구조화 액세스 로깅 미들웨어.

- 요청마다 ``uuid4`` 기반 ``request_id`` 발급, ``request.state.request_id`` 에 부착.
- 응답 헤더 ``X-Request-ID`` 로 반향. 클라이언트가 보낸 동일 헤더가 있으면 재사용.
- 응답 후 한 줄 JSON 액세스 로그 (logger ``api.access``). 핸들러는 logging_config 가 설치.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

ACCESS_LOGGER = "api.access"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """request_id + duration_ms 구조화 로그."""

    def __init__(self, app, *, logger_name: str = ACCESS_LOGGER) -> None:
        super().__init__(app)
        self._logger = logging.getLogger(logger_name)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        rid = (request.headers.get("X-Request-ID") or uuid.uuid4().hex)
        request.state.request_id = rid

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000.0
            self._emit(request, status_code, duration_ms)
            raise

        duration_ms = (time.perf_counter() - start) * 1000.0
        # 헤더 반향
        response.headers.setdefault("X-Request-ID", rid)
        self._emit(request, status_code, duration_ms)
        return response

    def _emit(
        self, request: Request, status_code: int, duration_ms: float
    ) -> None:
        principal = getattr(request.state, "principal", None)
        user = getattr(principal, "name", "anonymous") if principal else "anonymous"
        try:
            self._logger.info(
                "request",
                extra={
                    "request_id": getattr(request.state, "request_id", None),
                    "method": request.method,
                    "path": request.url.path,
                    "status": status_code,
                    "duration_ms": round(duration_ms, 3),
                    "user": user,
                    "client": request.client.host if request.client else None,
                },
            )
        except Exception:  # pragma: no cover — logging must never break a request
            pass


__all__ = ["ACCESS_LOGGER", "RequestLoggingMiddleware"]
