"""``/api/system/health`` — 풍부한 헬스체크 검증."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_system_health_happy_path(test_client) -> None:
    resp = await test_client.get("/api/system/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str) and body["version"]
    assert isinstance(body["auth_required"], bool)
    assert isinstance(body["build"], str) and body["build"]


@pytest.mark.asyncio
async def test_system_health_reports_auth_required_flag(
    test_client, monkeypatch
) -> None:
    """``settings.auth_required`` 플래그가 응답에 노출되는지 검증."""
    from api.config import settings

    monkeypatch.setattr(settings, "auth_required", True, raising=False)
    resp = await test_client.get("/api/system/health")
    assert resp.status_code == 200
    assert resp.json()["auth_required"] is True

    monkeypatch.setattr(settings, "auth_required", False, raising=False)
    resp2 = await test_client.get("/api/system/health")
    assert resp2.status_code == 200
    assert resp2.json()["auth_required"] is False


@pytest.mark.asyncio
async def test_legacy_health_unaffected(test_client) -> None:
    """기존 ``/health`` 는 변경 없이 minimal 형태 유지."""
    resp = await test_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
