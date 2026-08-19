"""``/api/auth/keys`` — API 키 관리. BOOTSTRAP_API_KEY 헤더로 보호.

부트스트랩 키 보유자만 신규 키 발급 / 리스트 / 폐기 가능. 첫 키 발급 후
운영자는 발급된 plaintext 를 저장하고, 일반 호출자는 ``X-API-Key`` 로 사용한다.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    Principal,
    create_api_key,
    list_api_keys,
    require_api_key,
    require_bootstrap,
    revoke_api_key,
)
from ..db.base import get_session
from ..errors import NotFoundError
from ..schemas.auth import ApiKeyCreated, ApiKeyIn, ApiKeyOut

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/keys", tags=["auth"])


@router.post(
    "",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
)
async def issue_key(
    payload: ApiKeyIn,
    _bootstrap: Principal = Depends(require_bootstrap),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyCreated:
    row, plaintext = await create_api_key(
        session,
        name=payload.name,
        agent_scopes=payload.agent_scopes,
        department=payload.department,
        expires_at=payload.expires_at,
    )
    out = ApiKeyOut.model_validate(row)
    log.info(
        "api_key.issue id=%s name=%s scopes=%s",
        row.id,
        row.name,
        row.agent_scopes,
    )
    return ApiKeyCreated(**out.model_dump(), key=plaintext)


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(
    _bootstrap: Principal = Depends(require_bootstrap),
    session: AsyncSession = Depends(get_session),
) -> list[ApiKeyOut]:
    rows = await list_api_keys(session)
    return [ApiKeyOut.model_validate(r) for r in rows]


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: int,
    _bootstrap: Principal = Depends(require_bootstrap),
    session: AsyncSession = Depends(get_session),
) -> None:
    ok = await revoke_api_key(session, key_id)
    if not ok:
        raise NotFoundError(f"api key not found: id={key_id}")


@router.post("/self-revoke", status_code=status.HTTP_204_NO_CONTENT)
async def self_revoke(
    principal: Principal = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> None:
    """자기 자신의 SSO 키를 폐기한다 — 포털 로그아웃이 부른다.

    왜 필요한가. 포털에서 로그아웃해도 이 서버의 접근 권한은 회수되지 않았다.
    포털 타일을 누르면 portal_sso 가 name="sso:<email>" 인 ApiKey 행을 발급하고
    (기본 TTL 30일) 그 평문이 브라우저 localStorage 에 남는데, 포털 로그아웃은
    그 브라우저 사본만 지웠다. 서버의 행은 revoked=False 로 살아 있어, 키 사본을
    가진 쪽은 최대 30일 그대로 들어온다. 재로그인 때는 _mint_sso_key 가 이전 키를
    폐기하지만, 로그아웃만으로는 아무것도 폐기되지 않았다.

    인가는 키 자신이다 — 제시한 키의 행만 지운다. 남의 키는 건드릴 수 없다.
    bootstrap 키는 거부한다(그걸 지우면 운영자가 스스로를 잠근다).
    sso: 로 시작하지 않는 키는 아무것도 하지 않고 204 로 끝낸다 — 사람이 직접 만든
    장기 키가 로그아웃 한 번에 사라지면 곤란하고, 로그아웃은 어떤 경우에도 막히면 안 된다.
    """
    if principal.is_bootstrap or principal.key_id is None:
        log.info("self-revoke 거부 — bootstrap 키")
        return
    if not (principal.name or "").startswith("sso:"):
        log.info("self-revoke 생략 — SSO 키가 아님(name=%s)", principal.name)
        return
    await revoke_api_key(session, principal.key_id)
    log.info("self-revoke 완료 — %s (id=%s)", principal.name, principal.key_id)


@router.post("/verify", status_code=status.HTTP_200_OK)
async def verify_key(
    principal: Principal = Depends(require_api_key),
) -> dict:
    """현재 ``X-API-Key`` 헤더의 유효성만 검증.

    부트스트랩 키 불필요. 발급된 일반 키도 호출 가능. 만료/폐기 시 ``require_api_key``
    가 401 (``AUTHENTICATION_ERROR``) 을 던진다.

    Response:
        ``{"ok": true, "key_name": str, "agent_scopes": list[str]}``
    """
    return {
        "ok": True,
        "key_name": principal.name,
        "agent_scopes": list(principal.agent_scopes or []),
    }


__all__ = ["router"]
