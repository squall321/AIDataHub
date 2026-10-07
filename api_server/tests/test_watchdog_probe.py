# watchdog 가 '안 듣는다' 와 '듣는데 답이 늦다' 를 갈라 판정하는지 — 바쁜 API 를 죽은 것으로 보고 재기동하지 않게
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

WATCHDOG = Path(__file__).resolve().parents[2] / "deploy" / "apptainer" / "watchdog.sh"

pytestmark = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("curl") and WATCHDOG.is_file()),
    reason="bash·curl 과 deploy/apptainer/watchdog.sh 가 있어야 돈다",
)

# 실제 _common.sh 대신 읽히는 대역. watchdog.sh 는 자기 위치에서 모든 경로를 유도하므로, 임시 폴더에
# 사본과 대역을 나란히 두면 실 .env·apptainer·start_api.sh 어디에도 닿지 않는다.
# sleep 은 재검증 사이의 5초를 시험이 기다리지 않게 비운다.
_COMMON_STUB = """\
APPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$APPT_DIR/logs"
load_env() { :; }
rotate_log() { :; }
sleep() { :; }
_AIDH_APPT="$APPT_DIR/apptainer-stub"
INST_POSTGRES=stub
POSTGRES_PORT=1
POSTGRES_USER=stub
API_PORT="$TEST_API_PORT"
"""

_OK = b'{"status":"ok","sync_stale_sources":0}'


class _FakeApi:
    """모드대로 구는 가짜 API. 받은 요청 경로를 적어 둔다.

    ok = 곧바로 200 · hang = 받기만 하고 답하지 않는다(듣고는 있는데 바쁜 프로세스) · error = 500.
    close() 하면 포트가 닫혀 연결이 거부된다(죽은 프로세스).
    """

    def __init__(self) -> None:
        self.mode = "ok"
        self.paths: list[str] = []
        self._held: list[socket.socket] = []
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(64)
        self.port = self._sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            try:
                conn.settimeout(2)
                head = conn.recv(4096).decode("latin-1").split(" ")
                self.paths.append(head[1] if len(head) > 1 else "")
                if self.mode == "hang":
                    self._held.append(conn)
                    continue
                status, body = (b"200 OK", _OK) if self.mode == "ok" else (b"500 Oops", b"{}")
                conn.sendall(
                    b"HTTP/1.1 " + status + b"\r\nContent-Type: application/json\r\n"
                    b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body
                )
            except OSError:
                pass
            conn.close()

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)       # accept 에 걸린 스레드를 깨운다
        except OSError:
            pass
        self._sock.close()
        for conn in self._held:
            conn.close()


class _Box:
    """watchdog.sh 사본 하나와 그 주변 대역(start_api.sh·start_postgres.sh·apptainer)."""

    def __init__(self, root: Path, api: _FakeApi) -> None:
        self.dir = root / "apptainer"
        self.dir.mkdir()
        self.api = api
        shutil.copy(WATCHDOG, self.dir / "watchdog.sh")
        (self.dir / "_common.sh").write_text(_COMMON_STUB)
        for name in ("start_api.sh", "start_postgres.sh", "apptainer-stub"):
            stub = self.dir / name
            stub.write_text(f'#!/bin/sh\necho run >> "$(dirname "$0")/{name}.calls"\n')
            stub.chmod(0o755)

    def tick(self, *args: str, **env: str) -> float:
        """cron 한 번. 걸린 초를 돌려준다.

        탐침 한도는 답이 와야 하는 모드에서는 넉넉히(2초), 답이 안 오는 모드에서는 짧게(0.3초) 준다.
        """
        child = {
            "PATH": os.environ["PATH"],
            "TEST_API_PORT": str(self.api.port),
            "AIDH_WATCHDOG_PROBE_TIMEOUT_S": "0.3" if self.api.mode == "hang" else "2",
            **env,
        }
        started = time.monotonic()
        proc = subprocess.run(
            ["bash", str(self.dir / "watchdog.sh"), *args],
            env=child, capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        return time.monotonic() - started

    def calls(self, name: str) -> int:
        marker = self.dir / f"{name}.calls"
        return len(marker.read_text().splitlines()) if marker.exists() else 0

    @property
    def log(self) -> str:
        path = self.dir / "logs" / "watchdog.log"
        return path.read_text() if path.exists() else ""

    @property
    def strikes(self) -> str | None:
        path = self.dir / "logs" / "watchdog.api-timeout-strikes"
        return path.read_text().strip() if path.exists() else None


@pytest.fixture
def api():
    fake = _FakeApi()
    yield fake
    fake.close()


@pytest.fixture
def box(tmp_path, api):
    return _Box(tmp_path, api)


def test_liveness_is_probed_on_the_db_free_endpoint(box, api):
    """/api/system/health 는 응답 전에 DB 게이지를 잰다 — 그걸로 생사를 재면 프로세스가 아니라 DB 를 재게 된다."""
    box.tick()
    assert api.paths == ["/health", "/api/system/health"]     # 생사는 /health, 게이지는 예전 그대로
    assert box.calls("start_api.sh") == 0
    assert box.log == ""
    # 대역이 실제로 불렸다 — 이 시험이 실 apptainer·실 서비스에 닿지 않았다는 증거다.
    assert box.calls("apptainer-stub") >= 1
    assert box.calls("start_postgres.sh") == 0


def test_a_dead_api_is_recovered_in_the_same_tick_and_the_log_says_why(box, api):
    """연결 거부 = 아무도 듣지 않는다. 기다릴 이유가 없다."""
    api.close()
    box.tick()
    assert box.calls("start_api.sh") == 1
    assert "api refused — recovering via start_api.sh" in box.log
    assert "api recovery OK" in box.log


def test_a_busy_api_is_restarted_only_after_consecutive_timeouts(box, api):
    """⚠ 회귀 방지 — 듣고는 있는데 한도 안에 못 답한 API 를 첫 판정에 재기동했다.

    dev 에서 그렇게 난 재기동이 330건(하루 18~58건)이고 그때마다 진행 중이던 검색이 전부 끊겼다.
    연속으로 이어질 때만 살리고, 중간에 한 번이라도 답하면 처음부터 다시 센다.
    """
    api.mode = "hang"
    box.tick()
    box.tick()
    assert box.calls("start_api.sh") == 0
    assert box.strikes == "2"
    assert "api timeout 1/3" in box.log and "api timeout 2/3" in box.log
    assert "AIDH_WATCHDOG_PROBE_TIMEOUT_S" in box.log        # 무엇이 판정했는지

    api.mode = "ok"                                          # 스스로 돌아왔다
    box.tick()
    assert box.strikes is None
    assert "api answered again" in box.log

    api.mode = "hang"
    box.tick()
    box.tick()
    assert box.calls("start_api.sh") == 0                    # 2회 + 2회지만 연속은 2회다
    box.tick()
    assert box.calls("start_api.sh") == 1
    assert "api timeout 3/3 — recovering(AIDH_WATCHDOG_TIMEOUT_STRIKES) via start_api.sh" in box.log
    assert box.strikes is None                               # 복구했으면 다시 0 부터


def test_an_old_strike_count_is_not_continued(box, api):
    """'연속' 은 이어진 cron 실행이다 — 오래전에 적힌 횟수를 이으면 오늘의 첫 시간 초과가 재기동이 된다.

    cron 이 꺼져 있었거나 이 스크립트를 되돌렸다 다시 넣으면 횟수 파일이 지워지지 않은 채 남는다.
    """
    api.mode = "hang"
    box.tick()
    box.tick()
    assert box.strikes == "2"
    stale = time.time() - 600
    os.utime(box.dir / "logs" / "watchdog.api-timeout-strikes", (stale, stale))

    box.tick()
    assert box.calls("start_api.sh") == 0                    # 이었다면 3/3 으로 재기동했다
    assert box.strikes == "1"


def test_strike_count_and_probe_timeout_follow_their_knobs(box, api):
    api.mode = "hang"
    elapsed = box.tick(AIDH_WATCHDOG_TIMEOUT_STRIKES="1")
    assert box.calls("start_api.sh") == 1
    assert "api timeout 1/1 — recovering(AIDH_WATCHDOG_TIMEOUT_STRIKES)" in box.log
    # 탐침 한도 0.3초가 먹었다 — 기본 5초였다면 탐침 둘과 게이지 조회 하나로 15초가 걸린다.
    assert elapsed < 8


def test_an_error_status_is_still_recovered(box, api):
    """200 이 아닌 응답은 예전처럼 복구한다. 사유에 상태 코드가 남는다."""
    api.mode = "error"
    box.tick()
    assert box.calls("start_api.sh") == 1
    assert "api bad(http=500 curl=0) — recovering via start_api.sh" in box.log


def test_dry_run_changes_nothing(box, api):
    api.mode = "hang"
    box.tick("--dry-run")
    assert box.calls("start_api.sh") == 0 and box.strikes is None
    assert "DRY: api timeout 1/3" in box.log

    api.close()
    box.tick("--dry-run")
    assert box.calls("start_api.sh") == 0
    assert "DRY: api refused — would run start_api.sh" in box.log


def test_a_bad_knob_falls_back_and_says_so(box, api):
    """손잡이를 잘못 적으면 조용히 무시되지 않는다 — 기본값으로 돌고 로그에 남는다."""
    box.tick(AIDH_WATCHDOG_PROBE_TIMEOUT_S="0")              # curl 에 0 은 '한도 없음' 이다
    assert "AIDH_WATCHDOG_PROBE_TIMEOUT_S='0' 는 양수가 아니다 — 5 로 본다" in box.log

    api.mode = "hang"
    box.tick(AIDH_WATCHDOG_TIMEOUT_STRIKES="many")
    assert "AIDH_WATCHDOG_TIMEOUT_STRIKES='many' 는 1 이상의 정수가 아니다 — 3 으로 본다" in box.log
    assert "api timeout 1/3" in box.log
    assert box.calls("start_api.sh") == 0
