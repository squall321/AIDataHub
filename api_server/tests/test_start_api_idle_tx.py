# start_api.sh — 이행 전에 쉬는 트랜잭션의 pid · 시간을 찍고, 조회가 안 되면 말없이 넘어가는지(10-17 cae00 리뷰 H2)
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

START_API = Path(__file__).resolve().parents[2] / "deploy" / "apptainer" / "start_api.sh"
_HEAD = "# 이행 전에 쉬는 트랜잭션을 찍는다"
_ALEMBIC = 'echo "→ alembic upgrade head"'

pytestmark = pytest.mark.skipif(not (shutil.which("bash") and START_API.is_file()), reason="bash 와 start_api.sh 가 있어야 돈다")

# 대역 apptainer — psql 이 -tA -F ' · ' 로 내는 꼴 그대로 FAKE_ROWS 를 내거나, FAKE_FAIL 이면 접속 실패처럼 죽는다.
_FAKE = """
apptainer() {
  printf '%s\\n' "$*" >> "$CALLS"
  [ -n "${FAKE_FAIL:-}" ] && { echo 'psql: error: connection refused' >&2; return 2; }
  [ -n "${FAKE_ROWS:-}" ] && printf '%s\\n' "$FAKE_ROWS"
  return 0
}
INST_POSTGRES=aidh_postgres POSTGRES_PORT=1 POSTGRES_USER=u POSTGRES_DB=aidh
"""


def _block() -> str:
    text = START_API.read_text(encoding="utf-8")
    start, end = text.index(_HEAD), text.index(_ALEMBIC)
    assert start < end, "쉬는 트랜잭션 확인이 이행 뒤로 갔다"
    return text[start:end]


def _run(tmp_path, **env):
    calls = tmp_path / "calls"
    script = "set -euo pipefail\n" + _FAKE + _block() + "\necho '[다음 단계]'\n"
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                       env={"PATH": "/usr/bin:/bin", "CALLS": str(calls), **env})
    return r, calls.read_text() if calls.exists() else ""


def test_쉬는_트랜잭션이_있으면_pid_와_시간을_찍는다(tmp_path):
    rows = ("pid 4242 · 트랜잭션 02:13:05 · 쉼 01:59:00 · uvicorn · SELECT records.id FROM records\n"
            "pid 4343 · 트랜잭션 00:31:00 · 쉼 00:30:59 · - · UPDATE record_sections SET embedding_m3=$1")
    r, calls = _run(tmp_path, FAKE_ROWS=rows)
    assert r.returncode == 0 and "[다음 단계]" in r.stdout, r.stdout + r.stderr
    assert "⚠ 쉬는 트랜잭션 2개" in r.stdout
    assert "pid 4242 · 트랜잭션 02:13:05" in r.stdout and "pid 4343" in r.stdout
    assert "pg_terminate_backend" in r.stdout
    assert "idle in transaction" in calls and "-d aidh" in calls, calls


def test_없으면_아무것도_찍지_않는다(tmp_path):
    r, _ = _run(tmp_path)
    assert r.returncode == 0 and r.stdout == "[다음 단계]\n", r.stdout + r.stderr


def test_조회가_안_되면_말없이_이행으로_넘어간다(tmp_path):
    """공유 PG 처럼 인스턴스로 못 닿는 박스에서 이 확인이 기동을 막으면 안 된다(set -euo pipefail 아래)."""
    r, _ = _run(tmp_path, FAKE_FAIL="1")
    assert r.returncode == 0 and r.stdout == "[다음 단계]\n" and r.stderr == "", r.stdout + r.stderr
