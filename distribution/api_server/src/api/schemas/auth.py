"""Auth(API key) Pydantic schemas.

발급된 plaintext 키는 ``ApiKeyCreated.key`` 로 한 번만 반환된다.
이후 조회/리스트(``ApiKeyOut``)에서는 절대 노출되지 않는다.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyIn(BaseModel):
    """API 키 발급 요청 본문."""

    name: str = Field(min_length=1, max_length=100)
    agent_scopes: list[str] = Field(default_factory=list)
    department: str | None = Field(default=None, max_length=100)
    expires_at: datetime | None = None


class ApiKeyOut(BaseModel):
    """API 키 메타 (plaintext 미포함)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    agent_scopes: list[str]
    department: str | None = None
    created_at: datetime
    expires_at: datetime | None = None
    revoked: bool
    last_used_at: datetime | None = None


class ApiKeyCreated(ApiKeyOut):
    """발급 직후 응답: plaintext ``key`` 1회 노출."""

    key: str = Field(description="plaintext API key — store securely; never returned again")


__all__ = ["ApiKeyCreated", "ApiKeyIn", "ApiKeyOut"]
