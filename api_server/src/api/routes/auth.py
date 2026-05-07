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


__all__ = ["router"]
