"""``/api/auth/keys/verify`` — API 키 유효성 검증.

extension_integration_plan.md §3 — 확장이 발급된 키의 유효성만 확인하기 위한
경량 엔드포인트. 부트스트랩 키 미요구.
"""
from __future__ import annotations

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def issued_key(test_session_maker) -> tuple[str, str]:
    """활성 API 키 1개 발급. ``(plaintext, key_name)`` 반환."""
    from api.auth import create_api_key

    async with test_session_maker() as session:
        row, plaintext = await create_api_key(
            session,
            name="vscode-extension-tester",
            agent_scopes=["iga-analyst", "cae-reporter"],
            department="HE-CAE",
        )
        return plaintext, row.name


@pytest_asyncio.fixture
async def revoked_key(test_session_maker) -> str:
    """발급 후 즉시 폐기된 키 (plaintext)."""
    from api.auth import create_api_key, revoke_api_key

    async with test_session_maker() as session:
        row, plaintext = await create_api_key(
            session,
            name="revoked-key",
            agent_scopes=[],
        )
        ok = await revoke_api_key(session, row.id)
        assert ok
        return plaintext


@pytest.mark.asyncio
async def test_verify_valid_key_200(db_client, issued_key) -> None:
    """발급된 활성 키 → 200 + 키 정보."""
    plaintext, name = issued_key
    resp = await db_client.post(
        "/api/auth/keys/verify",
        headers={"X-API-Key": plaintext},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["key_name"] == name
    assert "iga-analyst" in body["agent_scopes"]
    assert "cae-reporter" in body["agent_scopes"]


@pytest.mark.asyncio
async def test_verify_no_key_401(db_client) -> None:
    """헤더 없음 → 401 + envelope."""
    resp = await db_client.post("/api/auth/keys/verify")
    assert resp.status_code == 401
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == "AUTHENTICATION_ERROR"


@pytest.mark.asyncio
async def test_verify_revoked_key_401(db_client, revoked_key) -> None:
    """폐기된 키 → 401."""
    resp = await db_client.post(
        "/api/auth/keys/verify",
        headers={"X-API-Key": revoked_key},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "AUTHENTICATION_ERROR"


@pytest.mark.asyncio
async def test_verify_invalid_key_401(db_client) -> None:
    """존재하지 않는 키 → 401."""
    resp = await db_client.post(
        "/api/auth/keys/verify",
        headers={"X-API-Key": "sk_definitely_not_a_real_key"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTHENTICATION_ERROR"


@pytest.mark.asyncio
async def test_verify_does_not_require_bootstrap(db_client, issued_key) -> None:
    """부트스트랩 키 없이도 호출 가능 — 일반 발급 키만으로 200."""
    from api.config import settings

    # 부트스트랩 키가 설정되어 있어도 verify 엔드포인트는 영향 없다.
    original = settings.bootstrap_api_key
    try:
        settings.bootstrap_api_key = "some-bootstrap-secret-not-used"
        plaintext, _ = issued_key
        resp = await db_client.post(
            "/api/auth/keys/verify",
            headers={"X-API-Key": plaintext},
        )
        assert resp.status_code == 200
    finally:
        settings.bootstrap_api_key = original
