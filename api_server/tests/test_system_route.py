"""``/api/system/health`` — 풍부한 헬스체크 검증."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path

import pytest

# 배포 스크립트와 그 설정 안내 — 아래 층 계약 시험들이 경로·한도를 여기서 꺼낸다.
# 리포 루트는 이 파일 위치에서 유도한다.
_APPT_DIR = Path(__file__).resolve().parents[2] / "deploy" / "apptainer"
_CURL_READ = re.compile(
    r'--max-time\s+(\S+)[\s\\]+"http://127\.0\.0\.1:\$\{API_PORT\}(/[\w/]+)"'
)


def _watchdog_script() -> str:
    path = _APPT_DIR / "watchdog.sh"
    if not path.is_file():
        # api_server 만 떼어 낸 배포본에는 이 스크립트가 없다 — 대조할 탐침이 없다.
        pytest.skip("deploy/apptainer/watchdog.sh 가 없다")
    return path.read_text(encoding="utf-8")


def _watchdog_reads(part: str, script: str) -> list[tuple[str, float]]:
    """``part`` 에서 API 를 읽는 curl 마다 (경로, ``--max-time`` 기본값)을 순서대로 꺼낸다."""
    reads = []
    for limit, path in _CURL_READ.findall(part):
        limit = limit.strip('"')
        if limit.startswith("$"):
            # 한도가 손잡이면 그 기본값을 따라간다 — --max-time "$X" ← X="${AIDH_…:-5}"
            name = re.escape(limit.strip("${}"))
            limit = re.search(rf'^{name}="\$\{{\w+:-([\d.]+)\}}"', script, re.M).group(1)
        reads.append((path, float(limit)))
    return reads


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
    """층 계약 — 게이지 한도(2초)는 이 응답을 읽는 탐침의 한도(watchdog 5초)보다 작아야 한다.

    탐침 한도는 watchdog.sh 에서 꺼낸다 — 5 를 여기에 적어 두면 스크립트가 바뀌어도 초록이다.
    """
    from api.config import settings

    fresh = type(settings)(_env_file=None)               # 박스 .env 가 아니라 코드 기본값
    assert fresh.aidh_health_gauge_timeout_s == 2.0
    script = _watchdog_script()
    reads = _watchdog_reads(script, script)
    limits = [limit for path, limit in reads if path == "/api/system/health"]
    assert limits and fresh.aidh_health_gauge_timeout_s < min(limits)


def test_deploy_env_example_names_the_endpoint_the_watchdog_restarts_on() -> None:
    """⚠ 회귀 방지 — 안내는 'watchdog 도 /health 를 찌른다' 였는데 스크립트는 이 응답을 쟀다.

    그 문장을 믿고 게이지 한도를 0 이나 5 이상으로 두면, DB 가 느릴 때 살아 있는 API 가 매분
    재기동되고 진행 중이던 검색이 전부 끊긴다(2026-10-08 검토에서 재현). 안내가 말하는 경로를
    스크립트의 재기동 판정('2. API' 절)에서 꺼낸 경로와 맞춘다 — 스크립트가 탐침을 옮기면
    안내도 같이 옮겨야 초록이다.
    """
    script = _watchdog_script()
    liveness = script.partition("# ── 2. API")[2].partition("# ── 3. ")[0]
    probed = {path for path, _ in _watchdog_reads(liveness, script)}
    assert len(probed) == 1, f"'2. API' 절의 탐침 경로를 하나로 읽지 못했다 — {probed}"

    example = (_APPT_DIR / ".env.example").read_text(encoding="utf-8")
    block = example.partition("health 게이지 한도")[2]
    block = block.partition("AIDH_HEALTH_GAUGE_TIMEOUT_S=")[0]
    named = {
        path
        for line in block.splitlines()
        if "watchdog" in line
        for path in re.findall(r"/api/system/health|/health", line)
    }
    assert named == probed
