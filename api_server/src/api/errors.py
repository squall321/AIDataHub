"""통합 에러 응답 + 예외 계층.

표준 응답 형태:

    {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "...",
            "details": {...},
            "request_id": "..."
        }
    }

핸들러 등록은 ``api.routes.register_routers`` 가 담당한다 (main.py 미수정 정책).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------
class APIError(Exception):
    """모든 API 에러의 베이스. status / code / details 를 캡슐화."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str = "internal error",
        *,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details) if details else {}
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code


class AuthenticationError(APIError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "AUTHENTICATION_ERROR"


class AuthorizationError(APIError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "AUTHORIZATION_ERROR"


class NotFoundError(APIError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class ConflictError(APIError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"


class ValidationError(APIError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "VALIDATION_ERROR"


class RateLimitError(APIError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "RATE_LIMIT"


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------
def _request_id(request: Request) -> str | None:
    rid = getattr(request.state, "request_id", None)
    return rid if isinstance(rid, str) else None


def build_error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """표준 에러 페이로드 + (있으면) X-Request-ID 헤더."""
    rid = _request_id(request)
    payload: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": rid,
        }
    }
    headers = {"X-Request-ID": rid} if rid else None
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, APIError)
    log.info(
        "APIError code=%s status=%s message=%s",
        exc.code,
        exc.status_code,
        exc.message,
    )
    return build_error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


async def http_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    code_map = {
        400: "BAD_REQUEST",
        401: "AUTHENTICATION_ERROR",
        403: "AUTHORIZATION_ERROR",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        413: "PAYLOAD_TOO_LARGE",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMIT",
    }
    code = code_map.get(exc.status_code, "HTTP_ERROR")
    detail = exc.detail
    message = detail if isinstance(detail, str) else "request failed"
    details: dict[str, Any] = {}
    if not isinstance(detail, str):
        details["detail"] = detail
    return build_error_response(
        request,
        status_code=exc.status_code,
        code=code,
        message=message,
        details=details,
    )


async def validation_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return build_error_response(
        request,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="VALIDATION_ERROR",
        message="request payload validation failed",
        details={"errors": exc.errors()},
    )


async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    log.exception("unhandled exception: %s", exc)
    return build_error_response(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        message="internal server error",
        details={"type": type(exc).__name__},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """`routes.register_routers` 에서 호출."""
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(
        RequestValidationError, validation_exception_handler
    )
    app.add_exception_handler(Exception, unhandled_exception_handler)


__all__ = [
    "APIError",
    "AuthenticationError",
    "AuthorizationError",
    "ConflictError",
    "NotFoundError",
    "RateLimitError",
    "ValidationError",
    "build_error_response",
    "register_exception_handlers",
]
