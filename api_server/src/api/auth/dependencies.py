"""FastAPI 인증 의존성.

- ``require_api_key``  : 헤더 ``X-API-Key`` 검증. 설정에 따라 missing 허용/거부.
- ``require_bootstrap``: 부트스트랩 키 (BOOTSTRAP_API_KEY) 매칭.
- ``Principal``        : 요청 식별 컨텍스트 (anonymous 또는 인증된 키).
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.base import get_session
from ..errors import AuthenticationError, AuthorizationError
from .keys import lookup_active_key, touch_last_used


@dataclass
class Principal:
    """요청 호출자 식별 컨텍스트.

    - ``key_id``       : 인증된 ApiKey.id (anonymous 면 None)
    - ``name``         : ApiKey.name 또는 ``"anonymous"``
    - ``agent_scopes`` : 부여된 스코프 (anonymous 면 빈 리스트)
    - ``is_anonymous`` : True 면 인증 없음 (AUTH_REQUIRED=false 환경)
    - ``is_bootstrap`` : 부트스트랩 키로 인증된 경우
    """

    key_id: int | None = None
    name: str = "anonymous"
    agent_scopes: list[str] = field(default_factory=list)
    department: str | None = None
    is_anonymous: bool = True
    is_bootstrap: bool = False


async def resolve_principal(
    session: AsyncSession,
    api_key: str | None,
) -> Principal:
    """``api_key`` 문자열 → ``Principal`` (FastAPI 의존성 주입 없이).

    FastAPI ``get_principal`` 과 MCP 도구가 **같은 인증 로직**을 공유하기 위한
    plain 함수. MCP 도구는 ``Header()`` 주입을 못 받으므로, 도구 인자로 받은
    api_key 문자열을 이 함수에 넘겨 동일하게 검증한다 (단일 진실원천).

    AUTH_REQUIRED=false: 키 없으면 anonymous, 키 있으면 검증(틀리면 raise).
    AUTH_REQUIRED=true : 키 없거나 틀리면 raise.
    """
    bootstrap = (settings.bootstrap_api_key or "").strip()

    if api_key is None or not api_key.strip():
        if settings.auth_required:
            raise AuthenticationError("missing X-API-Key header")
        return Principal()

    candidate = api_key.strip()

    # bootstrap 키 매칭 (constant-time)
    if bootstrap and secrets.compare_digest(candidate, bootstrap):
        return Principal(
            key_id=None,
            name="bootstrap",
            agent_scopes=["*"],
            department=None,
            is_anonymous=False,
            is_bootstrap=True,
        )

    key = await lookup_active_key(session, candidate)
    if key is None:
        raise AuthenticationError("invalid or revoked API key")

    # last_used_at 갱신은 best-effort (실패해도 인증 흐름은 진행)
    await touch_last_used(session, key.id)

    return Principal(
        key_id=key.id,
        name=key.name,
        agent_scopes=list(key.agent_scopes or []),
        department=key.department,
        is_anonymous=False,
        is_bootstrap=False,
    )


async def get_principal(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """현재 호출자의 ``Principal`` 확정 (FastAPI 의존성). 로직은
    :func:`resolve_principal` 에 위임 — MCP 와 공유."""
    principal = await resolve_principal(session, x_api_key)
    request.state.principal = principal
    return principal


async def require_api_key(
    principal: Principal = Depends(get_principal),
) -> Principal:
    """비-anonymous Principal 요구. AUTH_REQUIRED=false 환경에서도 강제 인증."""
    if principal.is_anonymous:
        raise AuthenticationError("authentication required")
    return principal


async def require_bootstrap(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Principal:
    """부트스트랩 키 (BOOTSTRAP_API_KEY) 매칭만 통과시킨다.

    첫 ApiKey 발급/관리용. constant-time 비교.
    """
    bootstrap = (settings.bootstrap_api_key or "").strip()
    if not bootstrap:
        raise AuthorizationError(
            "BOOTSTRAP_API_KEY is not configured on the server"
        )
    if not x_api_key or not secrets.compare_digest(x_api_key.strip(), bootstrap):
        raise AuthenticationError("bootstrap key required for this operation")
    return Principal(
        name="bootstrap",
        agent_scopes=["*"],
        is_anonymous=False,
        is_bootstrap=True,
    )


__all__ = [
    "Principal",
    "get_principal",
    "require_api_key",
    "require_bootstrap",
]
