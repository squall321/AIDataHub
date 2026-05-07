"""API key 인증 패키지.

- ``keys``        : 키 생성/해시/검증 헬퍼.
- ``dependencies``: FastAPI 의존성 (``require_api_key``, ``get_principal``).
"""
from __future__ import annotations

from .dependencies import (
    Principal,
    get_principal,
    require_api_key,
    require_bootstrap,
)
from .keys import (
    create_api_key,
    generate_key,
    hash_key,
    list_api_keys,
    lookup_active_key,
    revoke_api_key,
    touch_last_used,
)

__all__ = [
    "Principal",
    "create_api_key",
    "generate_key",
    "get_principal",
    "hash_key",
    "list_api_keys",
    "lookup_active_key",
    "require_api_key",
    "require_bootstrap",
    "revoke_api_key",
    "touch_last_used",
]
