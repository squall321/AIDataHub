"""``/api/system/health`` — 풍부한 헬스체크 검증."""
from __future__ import annotations

import asyncio
import logging
import time

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


class _StuckSession:
    """게이지 쿼리가 돌아오지 않는 DB — 풀이 차 줄을 서 있거나(60초까지) 쿼리가 느린 상황."""

    async def scalar(self, *args, **kwargs):
        await asyncio.sleep(5)

    async def execute(self, *args, **kwargs):
        await asyncio.sleep(5)


class _SlowSession:
    """느리지만 답은 하는 DB. 미임베딩 7건, 동기화 소스 없음."""

    async def scalar(self, *args, **kwargs):
        await asyncio.sleep(0.4)
        return 7

    async def execute(self, *args, **kwargs):
        class _Rows:
            def scalars(self):
                return self

            def all(self):
                return []

        return _Rows()


async def _health_with(session, test_client):
    """get_session 을 바꿔 끼우고 /api/system/health 를 부른다. (응답, 걸린 초)"""
    from api.db.base import get_session
    from api.main import app

    async def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    try:
        started = time.monotonic()
        resp = await test_client.get("/api/system/health")
        return resp, time.monotonic() - started
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_system_health_gives_up_on_slow_gauges(test_client, monkeypatch, caplog) -> None:
    """⚠ 회귀 방지 — 게이지 쿼리에 한도가 없어 health 가 DB 만큼 느렸다.

    풀이 차면 60초까지 매달렸고, 이 응답을 5초 한도로 재던 watchdog 은 살아 있는 API 를
    '죽었다' 로 판정해 재기동했다(dev 에서 330건). 게이지는 참고값이라 못 재면 비운다.
    """
    from api.config import settings

    monkeypatch.setattr(settings, "aidh_health_gauge_timeout_s", 0.2)
    with caplog.at_level(logging.WARNING, logger="api.routes.system"):
        resp, elapsed = await _health_with(_StuckSession(), test_client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["embed_backlog"] is None and body["sync_stale_sources"] is None
    assert elapsed < 2                                   # 가짜 DB 는 5초를 끈다
    assert "AIDH_HEALTH_GAUGE_TIMEOUT_S" in caplog.text    # 왜 비었는지, 무엇을 돌리면 되는지


@pytest.mark.asyncio
async def test_system_health_does_not_blame_the_gauge_limit_for_other_timeouts(
    test_client, caplog
) -> None:
    """안에서 난 다른 시간 초과(PG 연결 한도)를 이 손잡이 탓으로 적으면 엉뚱한 값을 돌리게 된다."""

    class _ConnectTimesOut:
        async def scalar(self, *args, **kwargs):
            raise TimeoutError("PG 에 10초 안에 연결하지 못했다(AIDH_DB_CONNECT_TIMEOUT_S)")

    with caplog.at_level(logging.DEBUG, logger="api.routes.system"):
        resp, _ = await _health_with(_ConnectTimesOut(), test_client)

    assert resp.status_code == 200
    assert resp.json()["embed_backlog"] is None
    assert "AIDH_DB_CONNECT_TIMEOUT_S" in caplog.text
    assert "AIDH_HEALTH_GAUGE_TIMEOUT_S" not in caplog.text


@pytest.mark.asyncio
async def test_system_health_gauge_limit_zero_means_wait(test_client, monkeypatch) -> None:
    """0 은 끔이다 — 한도(0.2초)보다 느린 DB 도 끝까지 기다려 게이지를 채운다."""
    from api.config import settings

    monkeypatch.setattr(settings, "aidh_health_gauge_timeout_s", 0)
    resp, _ = await _health_with(_SlowSession(), test_client)
    body = resp.json()
    assert body["embed_backlog"] == 7 and body["sync_stale_sources"] == 0


def test_health_gauge_limit_default_sits_inside_the_probe() -> None:
    """층 계약 — 게이지 한도(2초)는 이 응답을 읽는 탐침의 한도(watchdog 5초)보다 작아야 한다."""
    from api.config import settings

    fresh = type(settings)(_env_file=None)               # 박스 .env 가 아니라 코드 기본값
    assert fresh.aidh_health_gauge_timeout_s == 2.0 < 5
