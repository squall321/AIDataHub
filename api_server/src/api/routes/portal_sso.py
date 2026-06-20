"""HWAX 포털 SSO 콜백 — 기존 X-API-Key/localStorage 인증으로 브릿지.

흐름: 사용자가 포털에 로그인한 상태로 AI Data Hub 타일을 클릭하면, 포털이
짧은 수명의 RS256 "launch" JWT(aud = 이 서비스)를 발급해 이 엔드포인트로
auto-POST 한다. 우리는:
  1. 포털 JWKS 를 받아(캐시) 토큰을 검증한다(RS256, aud, exp, scope=launch),
  2. 이메일 기준으로 User 를 upsert(첫 로그인 시 JIT 생성),
  3. 그 사용자용 SSO ApiKey(name='sso:<email>')를 새로 발급(이전 SSO 키는 폐기),
  4. plaintext 를 1회용 핸드오프 쿠키(aidh_sso_key, Path=/)에 담아 대시보드로 303 리다이렉트.

대시보드 JS 가 이 쿠키를 읽어 localStorage['aidh.api_key'] 로 옮긴 뒤 즉시 만료시킨다.
이후 기존 apiFetch 가 X-API-Key 헤더로 그대로 동작한다.

``portal_jwks_url`` 이 비어 있으면 404(비활성) — standalone 배포는 영향 없음.
로컬 X-API-Key / PAT / 부트스트랩 키 경로는 전혀 건드리지 않는다.

注: 이 저장소의 어떤 라우트도 agent_scopes 로 인가하지 않으므로(저장/반환만 함)
발급 키의 scope 는 빈 리스트로 둔다. scope gate 가 추가될 때 재검토.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx
import jwt
from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import create_api_key
from ..config import settings
from ..db.base import get_session
from ..db.models import ApiKey, User
from ..errors import AuthenticationError, NotFoundError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["portal-sso"])

# 단일 uvicorn 프로세스 기준 in-process JWKS 캐시 + replay 가드.
# 멀티 레플리카에서는 Redis 로 백업 (다른 부분과 동일한 seam).
_jwks_cache: dict[str, Any] = {"keys": None, "fetched": 0.0}
_seen_jti: dict[str, float] = {}


async def _portal_jwks() -> list[dict[str, Any]]:
    now = time.time()
    if _jwks_cache["keys"] is not None and now - _jwks_cache["fetched"] < 300:
        return _jwks_cache["keys"]
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(settings.portal_jwks_url)
        r.raise_for_status()
        keys = r.json().get("keys", [])
    _jwks_cache["keys"] = keys
    _jwks_cache["fetched"] = now
    return keys


def _gc_jti(now: float) -> None:
    for k, exp in list(_seen_jti.items()):
        if exp < now:
            del _seen_jti[k]


async def _verify_portal_token(token: str) -> dict[str, Any]:
    keys = await _portal_jwks()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise AuthenticationError("malformed launch token") from e
    key = next((k for k in keys if k.get("kid") == header.get("kid")), None) or (
        keys[0] if keys else None
    )
    if key is None:
        raise AuthenticationError("portal JWKS has no usable key")
    try:
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=settings.portal_audience,
            options={"require": ["exp", "aud", "sub", "jti"]},
        )
    except jwt.PyJWTError as e:
        raise AuthenticationError("launch token rejected") from e
    if claims.get("scope") != "launch":
        raise AuthenticationError("not a launch token")
    now = time.time()
    _gc_jti(now)
    jti = claims["jti"]
    if jti in _seen_jti:
        raise AuthenticationError("launch token already used")
    _seen_jti[jti] = float(claims["exp"])
    return claims


async def _upsert_user(
    session: AsyncSession, *, email: str, name: str, sub: str
) -> User:
    """이메일 대소문자 무시 조회. 없으면 JIT 생성."""
    row = (
        await session.execute(
            select(User).where(func.lower(User.email) == email.lower())
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    user = User(
        id=str(uuid4()),
        email=email,
        name=name or email.split("@")[0],
        sso_subject=sub,
    )
    session.add(user)
    await session.flush()
    return user


async def _mint_sso_key(session: AsyncSession, *, email: str) -> str:
    """이전 SSO 키 폐기 후 새 키 발급. plaintext 반환.

    create_api_key 가 내부 commit 하므로, 그 전에 폐기를 commit 해 둔다.
    """
    await session.execute(
        update(ApiKey)
        .where(ApiKey.name == f"sso:{email}", ApiKey.revoked.is_(False))
        .values(revoked=True)
    )
    await session.commit()

    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.portal_sso_key_ttl_days
    )
    _row, plaintext = await create_api_key(
        session,
        name=f"sso:{email}",
        agent_scopes=[],  # 이 저장소는 scope 로 인가하지 않음 (저장/반환만).
        department=None,
        expires_at=expires_at,
    )
    return plaintext


@router.post("/portal-callback")
async def portal_callback(
    token: str = Form(...),
    session: AsyncSession = Depends(get_session),
) -> Response:
    if not settings.portal_jwks_url:
        raise NotFoundError("portal SSO not enabled")

    claims = await _verify_portal_token(token)
    email = claims.get("email")
    if not email:
        raise AuthenticationError("launch token missing email")

    user = await _upsert_user(
        session,
        email=email,
        name=claims.get("name") or "",
        sub=claims["sub"],
    )
    await session.commit()

    plaintext = await _mint_sso_key(session, email=user.email)

    log.info("portal_sso.callback email=%s user_id=%s", user.email, user.id)

    resp = RedirectResponse(url=settings.portal_sso_landing, status_code=303)
    # Path=/ 가 핵심: 쿠키는 /ai-data-hub/api/auth/... 에서 설정되지만
    # /ai-data-hub/dashboard/ 요청에서 읽혀야 한다 (Path=/ 가 두 경로 모두의 prefix).
    resp.set_cookie(
        "aidh_sso_key",
        plaintext,
        max_age=120,
        httponly=False,  # 대시보드 credential 은 JS-side(localStorage) — 읽혀야 함.
        secure=settings.portal_cookie_secure,
        samesite="lax",
        path="/",
    )
    return resp


__all__ = ["router"]
