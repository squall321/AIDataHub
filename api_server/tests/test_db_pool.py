# DB 엔진 인자(풀·연결 한도)가 설정값대로 실리는지 — 기본값이면 심의가 겹칠 때 풀이 마르고, 죽은 PG 를 60초 기다린다
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"

# 엔진은 api.db.base 를 import 하는 순간 한 번 만들어진다. 같은 프로세스에서 모듈을 다시 읽으면
# Base 가 새로 생겨 다른 시험의 메타데이터가 끊기므로, **새 프로세스**를 실제 환경변수로 띄워 본다.
_PROBE_POOL = (
    "from api.db.base import engine; p = engine.pool; "
    "print(type(p).__name__, p.size(), p._max_overflow, p.timeout())"
)
_PROBE_KIND = "from api.db.base import engine; print(type(engine.pool).__name__)"


def _probe(tmp_path, code: str = _PROBE_POOL, **env: str) -> str:
    """새 프로세스에서 엔진을 만들고 풀 상태를 읽는다.

    연결은 하지 않는다(엔진은 첫 사용 때 붙는다). cwd 를 빈 폴더로 두어 api_server/.env 의
    실 DB 주소가 섞이지 않게 하고, 개발자 셸에 남은 DB_*·AIDH_DB_* 도 걷어낸다.
    """
    child = {k: v for k, v in os.environ.items() if not k.startswith(("DB_", "AIDH_DB_"))}
    existing = child.get("PYTHONPATH", "")
    child.update(
        PYTHONPATH=str(_SRC) + (os.pathsep + existing if existing else ""),
        PYTHONDONTWRITEBYTECODE="1",
        DATABASE_URL="postgresql+asyncpg://localhost/none",
    )
    child.update(env)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path, env=child, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout.strip()


def test_pool_defaults_are_not_sqlalchemy_defaults(tmp_path):
    """⚠ 회귀 방지 — 풀 인자를 안 주면 SQLAlchemy 기본값(5 + 10, 대기 30초)이 걸린다.

    심의 한 건이 agent_search 를 17~20개 한꺼번에 쏘는데 풀은 15개에서 막혔다. 로그의
    'QueuePool limit of size 5 overflow 10 reached' 가 그 기본값 그대로다
    (S26U 심사 실사용 피드백, 2026-10-07).
    """
    assert _probe(tmp_path) == "KnobNamingQueuePool 12 8 60.0"


def test_pool_follows_env(tmp_path):
    """박스마다 PG 여유가 달라 손잡이가 실제로 먹어야 한다 — 이름만 있고 안 실리면 없는 것이다."""
    out = _probe(tmp_path, DB_POOL_SIZE="3", DB_MAX_OVERFLOW="1", DB_POOL_TIMEOUT="7.5")
    assert out == "KnobNamingQueuePool 3 1 7.5"


def test_sqlite_memory_engine_still_builds(tmp_path):
    """SQLite :memory: 는 StaticPool 이라 풀 인자를 TypeError 로 거절한다 — 그쪽엔 주지 않는다."""
    pytest.importorskip("aiosqlite")
    out = _probe(tmp_path, _PROBE_KIND, DATABASE_URL="sqlite+aiosqlite:///:memory:")
    assert out == "StaticPool"


# ---------------------------------------------------------------------------
# 풀 대기 만료 문구
# ---------------------------------------------------------------------------
class _FakeDbapiConn:
    """풀이 반납·정리 때 부르는 것만 가진 가짜 연결. PG 없이 풀의 대기 만료를 실제로 낸다."""

    def rollback(self) -> None: ...

    def close(self) -> None: ...


async def test_pool_wait_expiry_names_the_knobs():
    """풀이 찬 채 대기 한도를 넘기면 문구가 무엇을 올려야 하는지 말한다.

    SQLAlchemy 의 원문('QueuePool limit of size … reached, connection timed out')은 내부 표현이라
    부른 쪽(심의 엔진·게이트웨이)이 어느 설정을 봐야 하는지 알 수 없었다. 원문은 뒤에 그대로 남긴다 —
    로그 검색이 그 문자열로 이뤄진다.
    """
    from sqlalchemy import exc as sa_exc
    from sqlalchemy.pool import QueuePool
    from sqlalchemy.util import greenlet_spawn

    from api.db.base import engine

    pool_cls = type(engine.sync_engine.pool)
    if not issubclass(pool_cls, QueuePool):
        pytest.skip("이 환경의 엔진은 QueuePool 이 아니다(SQLite)")

    pool = pool_cls(_FakeDbapiConn, pool_size=1, max_overflow=0, timeout=0.05)
    held = await greenlet_spawn(pool.connect)          # 하나뿐인 자리를 쥔다
    try:
        with pytest.raises(sa_exc.TimeoutError) as err:
            await greenlet_spawn(pool.connect)
    finally:
        await greenlet_spawn(held.close)
    msg = str(err.value)
    assert "커넥션 풀이 0.05초 동안 차 있었다(DB_POOL_TIMEOUT, DB_POOL_SIZE)" in msg
    assert "QueuePool limit of size 1 overflow 0 reached" in msg
    # 자리가 나면 다시 빌려 준다 — 문구만 바꿨지 풀의 동작은 그대로다.
    again = await greenlet_spawn(pool.connect)
    await greenlet_spawn(again.close)


# ---------------------------------------------------------------------------
# 연결 한도 (AIDH_DB_CONNECT_TIMEOUT_S)
# ---------------------------------------------------------------------------
# asyncpg.connect 를 바꿔 끼워 넘어오는 인자만 엿본다 — 어디에도 붙지 않는다.
_PROBE_CONNECT_ARG = """
import asyncio
import asyncpg
from api.db.base import engine

async def _spy(*args, **kwargs):
    print(kwargs.get("timeout"))
    raise RuntimeError("stop")

asyncpg.connect = _spy

async def main():
    try:
        async with engine.connect():
            pass
    except RuntimeError:
        pass

asyncio.run(main())
"""

# 실제로 붙어 본다. 바깥 8초는 한도가 안 걸린 코드가 시험을 60초 붙들지 않게 하는 안전판이다.
_PROBE_CONNECT = """
import asyncio, time
from api.db.base import engine

async def main():
    t = time.monotonic()
    try:
        async with engine.connect():
            pass
    except Exception as e:
        print(type(e).__name__, "|", e, "|", time.monotonic() - t < 5)

asyncio.run(asyncio.wait_for(main(), 8))
"""


def test_connect_timeout_default_reaches_asyncpg(tmp_path):
    """⚠ 회귀 방지 — connect_args 를 안 주면 asyncpg 기본값 60초가 걸린다.

    풀 대기(DB_POOL_TIMEOUT 60초)와 같은 시각에 터져 'PG 가 죽었다' 와 '풀이 찼다' 가
    구별되지 않았다. 죽은 상대를 재는 값이라 10초다.
    """
    assert _probe(tmp_path, _PROBE_CONNECT_ARG) == "10.0"


def test_connect_timeout_follows_env(tmp_path):
    assert _probe(tmp_path, _PROBE_CONNECT_ARG, AIDH_DB_CONNECT_TIMEOUT_S="3") == "3.0"


def test_connect_timeout_expires_and_names_the_knob(tmp_path):
    """받기만 하고 답하지 않는 PG 에 붙으면 한도에서 끊기고, 문구가 손잡이 이름을 말한다.

    asyncpg 의 시간 초과는 메시지가 빈 TimeoutError 라, 그대로 두면 도구 오류가
    'Error executing tool agent_search: ' 로 끝나 무엇이 판정했는지 알 수 없다.
    """
    with socket.socket() as blackhole:      # 핸드셰이크는 커널이 받아 주고, 아무도 읽지 않는다
        blackhole.bind(("127.0.0.1", 0))
        blackhole.listen(4)
        port = blackhole.getsockname()[1]
        out = _probe(
            tmp_path, _PROBE_CONNECT,
            DATABASE_URL=f"postgresql+asyncpg://u:p@127.0.0.1:{port}/none",
            AIDH_DB_CONNECT_TIMEOUT_S="0.5",
        )
    assert out == (
        "TimeoutError | PG 에 0.5초 안에 연결하지 못했다(AIDH_DB_CONNECT_TIMEOUT_S) | True"
    )
