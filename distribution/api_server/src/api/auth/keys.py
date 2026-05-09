"""API key 생성/해시/검증 코어 헬퍼.

DB 에는 ``key_hash`` (SHA-256 hex) 만 저장한다. plaintext 는 발급 시 1회만
호출자에게 반환된다. 모든 함수는 비동기 SQLAlchemy 세션을 받는다.

크로스플랫폼: ``secrets`` 와 ``hashlib`` 만 사용 (외부 의존성 없음).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ApiKey

# 키 prefix 는 운영자가 식별 가능하지만 비밀 정보가 아니므로 평문 노출 가능.
KEY_PREFIX = "sk_"


def generate_key() -> str:
    """새 plaintext 키 생성. URL-safe 32 byte (≈43 자) + ``sk_`` 프리픽스."""
    return f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"


def hash_key(plaintext: str) -> str:
    """SHA-256 hex digest. 64 자 고정. 좌우 공백은 strip."""
    return hashlib.sha256(plaintext.strip().encode("utf-8")).hexdigest()


async def create_api_key(
    session: AsyncSession,
    *,
    name: str,
    agent_scopes: list[str] | None = None,
    department: str | None = None,
    expires_at: datetime | None = None,
    plaintext: str | None = None,
) -> tuple[ApiKey, str]:
    """새 API 키 생성. ``(ApiKey, plaintext)`` 튜플 반환.

    Args:
        plaintext: 강제 지정용. 미지정 시 ``generate_key()`` 사용.
    """
    plain = plaintext if plaintext is not None else generate_key()
    row = ApiKey(
        key_hash=hash_key(plain),
        name=name,
        agent_scopes=list(agent_scopes or []),
        department=department,
        expires_at=expires_at,
        revoked=False,
    )
    session.add(row)
    await session.flush()
    await session.commit()
    await session.refresh(row)
    return row, plain


async def lookup_active_key(
    session: AsyncSession, plaintext: str
) -> ApiKey | None:
    """plaintext → 활성 ApiKey 조회. revoked / expired 는 None.

    검증 시점은 호출자 책임 (``datetime.now(tz=UTC)`` 비교).
    """
    if not plaintext:
        return None
    digest = hash_key(plaintext)
    stmt = select(ApiKey).where(ApiKey.key_hash == digest)
    result = await session.execute(stmt)
    key: ApiKey | None = result.scalar_one_or_none()
    if key is None or key.revoked:
        return None
    if key.expires_at is not None:
        now = datetime.now(timezone.utc)
        # naive datetime 도 안전하게 비교
        exp = key.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            return None
    return key


async def touch_last_used(session: AsyncSession, key_id: int) -> None:
    """``last_used_at`` 갱신. 실패해도 인증 흐름을 막지 않는다."""
    try:
        await session.execute(
            update(ApiKey)
            .where(ApiKey.id == key_id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
        await session.commit()
    except Exception:  # noqa: BLE001 — best-effort
        await session.rollback()


async def list_api_keys(session: AsyncSession) -> list[ApiKey]:
    stmt = select(ApiKey).order_by(ApiKey.id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def revoke_api_key(session: AsyncSession, key_id: int) -> bool:
    """ID 로 키 폐기. 존재하지 않으면 False."""
    stmt = (
        update(ApiKey)
        .where(ApiKey.id == key_id, ApiKey.revoked.is_(False))
        .values(revoked=True)
        .returning(ApiKey.id)
    )
    result = await session.execute(stmt)
    row = result.first()
    await session.commit()
    return row is not None


def to_dict(row: ApiKey) -> dict[str, Any]:
    """진단/로깅용 dict."""
    return {
        "id": row.id,
        "name": row.name,
        "agent_scopes": list(row.agent_scopes or []),
        "department": row.department,
        "revoked": row.revoked,
    }


__all__ = [
    "KEY_PREFIX",
    "create_api_key",
    "generate_key",
    "hash_key",
    "list_api_keys",
    "lookup_active_key",
    "revoke_api_key",
    "to_dict",
    "touch_last_used",
]
