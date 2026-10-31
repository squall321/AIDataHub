# 실제 조회 길 둘(GET /api/records/{id} · /api/data?agent=)이 폐기용 PostgreSQL(트리거째)에서 updated_at 을 그대로 두는지
"""조회는 read_count · last_accessed_at 만 올린다 — updated_at 은 그대로다(HWAX 2026-10-31 사용자 결정).

예전에는 두 길 모두 조회 수 갱신(api/routes/records.py _bump_usage_in_session)이 updated_at 도 조회 시각으로 올렸다 — 열의
onupdate(now())와 BEFORE UPDATE 트리거(set_updated_at, 이행 0002 · 0032)가 함께. 그래서 박스 사이 병합(merge-from-drive.sh)은
조회만 된 판을 더 새 편집으로 보고 다른 박스의 편집을 건너뛰거나 덮었다. 이 파일은 앱의 실제 라우트를 진짜 PostgreSQL 에 대고 돌린다.

opt-in: ``AIDH_TEST_PG_BIN`` — initdb · pg_ctl · psql 이 있는 폴더(PostgreSQL 14 이상, 그 서버에 pgvector 0.7 이상).
없으면 skip 이다.

    AIDH_TEST_PG_BIN=/usr/lib/postgresql/16/bin .venv/bin/python -m pytest tests/test_view_keeps_updated_at_pg.py

- 클러스터는 모듈마다 새로 띄운다(initdb, pytest 임시 폴더). 앱 드라이버(asyncpg)는 리눅스 추상 소켓을 못 쓰므로 듣는 곳은 짧은 전용 폴더
  (0700 — 이 OS 사용자만 들어간다)의 유닉스 소켓 하나다(TCP 없음 · peer 인증). 끝나면 내리고 폴더를 지운다.
- 스키마는 모델에서 뽑는다(test_merge_samples_pg 와 같은 출처) + 이행 0032 판 트리거(set_updated_at — 이행 파일에서 그대로 읽는다).
- 앱의 세션은 이 클러스터로만 간다 — get_session 은 의존성 덮어쓰기로, 백그라운드 갱신(_bump_usage)이 쓰는 SessionLocal 은
  monkeypatch 로 바꾼다. 실 DB 에는 닿지 않는다.
"""
from __future__ import annotations

import importlib.util
import os
import pwd
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

_PORT = "5432"  # 소켓 이름에만 쓰인다 — 소켓 폴더가 클러스터마다 새것이라 겹치지 않는다
_MIGRATION_0032 = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0032_embedding_m3_slot.py"
_SEEN_BEFORE = datetime(2026, 10, 10, tzinfo=UTC)   # 씨앗의 updated_at — 조회가 이것을 움직이면 안 된다
_DOC = "DOC-HE-CAE-2026-0000000001"    # GET /api/records/{id} 로 본다
_DATA = "DATA-HE-CAE-2026-0000000002"  # /api/data?agent= 로 본다(응답 뒤 백그라운드 갱신)
_AGENT = "view-probe"

_SEED = f"""
INSERT INTO agents (agent_type, name) VALUES ('{_AGENT}', 'V');
INSERT INTO records (id, data_type, team, "group", year, seq, title, content, content_hash, agents, updated_at) VALUES
 ('{_DOC}', 'DOC', 'HE', 'CAE', 2026, 1, 'doc', '{{}}'::jsonb, 'h-doc', '{{}}', '{_SEEN_BEFORE.isoformat()}'),
 ('{_DATA}', 'DATA', 'HE', 'CAE', 2026, 2, 'data', '{{}}'::jsonb, 'h-data', ARRAY['{_AGENT}'], '{_SEEN_BEFORE.isoformat()}');
"""


def _trigger_fn() -> str:
    """이행 0032 의 set_updated_at() — 사본을 두지 않고 이행 파일에서 읽는다."""
    spec = importlib.util.spec_from_file_location("aidh_migration_0032", _MIGRATION_0032)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._TRIGGER_FN_KEEP


@pytest.fixture(scope="module")
def pg_url(tmp_path_factory):
    bindir = os.environ.get("AIDH_TEST_PG_BIN")
    if not bindir:
        pytest.skip("AIDH_TEST_PG_BIN 미설정 — 폐기용 PostgreSQL 조회 시험 skip")
    bindir = Path(bindir)
    if not all((bindir / p).is_file() for p in ("initdb", "pg_ctl", "psql")):
        pytest.skip(f"{bindir} 에 initdb · pg_ctl · psql 이 없다")
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable

    import api.db.models  # noqa: F401  (메타데이터 등록)
    from api.db.base import Base

    dialect = postgresql.dialect()
    ddl = ["CREATE EXTENSION IF NOT EXISTS vector;"]
    for table in Base.metadata.sorted_tables:
        ddl.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        ddl += [str(CreateIndex(ix).compile(dialect=dialect)).strip() + ";" for ix in table.indexes]
    ddl.append(_trigger_fn())
    ddl.append("CREATE TRIGGER trg_records_updated_at BEFORE UPDATE ON records FOR EACH ROW EXECUTE FUNCTION set_updated_at();")

    data = tmp_path_factory.mktemp("pgview") / "data"
    # 소켓 경로는 108바이트 한도다 — pytest 임시 폴더는 길 수 있어 짧은 전용 폴더를 따로 만든다(mkdtemp 는 0700).
    sockdir = Path(tempfile.mkdtemp(prefix="aidh-pgv-"))
    user = pwd.getpwuid(os.getuid()).pw_name
    run = lambda *a: subprocess.run([str(x) for x in a], capture_output=True, text=True, timeout=120)  # noqa: E731
    started = False
    try:
        if len(f"{sockdir}/.s.PGSQL.{_PORT}") > 107:
            pytest.skip(f"소켓 경로가 너무 길다({sockdir}) — TMPDIR 를 짧게")
        r = run(bindir / "initdb", "-D", data, "-U", user, "--auth-local=peer", "--auth-host=reject", "-E", "UTF8", "--locale=C")
        if r.returncode:
            pytest.skip(f"initdb 실패: {r.stderr[-300:]}")
        opts = f"-c listen_addresses='' -c unix_socket_directories='{sockdir}' -p {_PORT}"
        r = run(bindir / "pg_ctl", "-D", data, "-l", data.parent / "server.log", "-w", "-o", opts, "start")
        if r.returncode:
            pytest.skip(f"pg_ctl start 실패: {r.stderr[-300:]}")
        started = True
        psql = lambda db, sql: run(bindir / "psql", "-h", sockdir, "-p", _PORT, "-U", user, "-d", db,  # noqa: E731
                                   "-v", "ON_ERROR_STOP=1", "-qtA", "-c", sql)
        r = psql("postgres", "CREATE DATABASE aidh;")
        assert r.returncode == 0, r.stderr
        r = psql("aidh", "CREATE EXTENSION vector;")
        if r.returncode:
            pytest.skip(f"pgvector 를 못 올린다: {r.stderr[-300:]}")
        r = psql("aidh", "\n".join(ddl))
        assert r.returncode == 0, r.stderr
        yield f"postgresql+asyncpg://{user}@/aidh?host={sockdir}&port={_PORT}"
    finally:
        if started:
            run(bindir / "pg_ctl", "-D", data, "-m", "fast", "-w", "stop")
        shutil.rmtree(sockdir, ignore_errors=True)


@pytest.fixture()
async def app_on_pg(pg_url, monkeypatch):
    """씨앗을 새로 깐 이 클러스터에 앱을 붙인다 — (httpx 클라이언트, 세션메이커)."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import api.routes.records as records_route
    from api.db.base import get_session
    from api.main import app

    engine = create_async_engine(pg_url)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE records, agents CASCADE"))
        for stmt in filter(str.strip, _SEED.split(";")):
            await conn.execute(text(stmt))

    async def _session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    monkeypatch.setattr(records_route, "SessionLocal", maker)  # /api/data 의 백그라운드 갱신(_bump_usage)이 여는 세션
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, maker
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


async def _row(maker, rid: str):
    from sqlalchemy import select

    from api.db.models import Record

    async with maker() as s:
        return (await s.execute(select(Record.read_count, Record.last_accessed_at, Record.updated_at)
                                .where(Record.id == rid))).one()


async def test_두_조회_길은_조회_수와_시각만_올리고_updated_at_은_그대로다(app_on_pg):
    client, maker = app_on_pg
    t0 = datetime.now(UTC)
    for _ in range(2):
        r = await client.get(f"/api/records/{_DOC}")
        assert r.status_code == 200, r.text
        r = await client.get("/api/data", params={"agent": _AGENT})
        assert r.status_code == 200, r.text
        assert _DATA in {x.get("record_id") for x in r.json().get("results") or []}, r.text
    for rid in (_DOC, _DATA):
        read_count, seen_at, updated_at = await _row(maker, rid)
        assert read_count == 2, (rid, read_count)                   # 백그라운드 갱신까지 끝났다(ASGI 호출이 그것까지 기다린다)
        assert seen_at is not None and seen_at >= t0, (rid, seen_at)
        assert updated_at == _SEEN_BEFORE, (rid, updated_at)        # 예전에는 조회 시각(오늘)이었다


async def test_조회_뒤_같은_트랜잭션의_편집은_updated_at_을_올린다(app_on_pg):
    """조회 갱신이 켠 aidh.keep_updated_at 은 그 UPDATE 하나에만 든다 — 같은 트랜잭션에서 뒤따른 편집까지 updated_at 을 잃지 않는다.
    편집은 SQL 문장으로 한다 — ORM 의 UPDATE 는 열의 onupdate 가 now() 를 스스로 붙여 설정이 남아도 오르므로, 트리거만 믿는 쓰기에서
    드러난다."""
    from sqlalchemy import text

    from api.routes.records import _bump_usage_in_session

    _, maker = app_on_pg
    async with maker() as s:
        await _bump_usage_in_session(s, _DOC)
        await s.execute(text("UPDATE records SET title = '고친 제목' WHERE id = :rid"), {"rid": _DOC})
        await s.commit()
    read_count, _, updated_at = await _row(maker, _DOC)
    assert read_count == 1 and updated_at > _SEEN_BEFORE, (read_count, updated_at)
