"""End-to-end smoke test against a temporary SQLite DB.

목적:
    - Docker / PostgreSQL 가 없는 개발 환경에서 백엔드의 핵심 경로를
      한 번에 검증한다.
    - 다음 단계를 차례로 수행한다:
        1) 임시 SQLite 파일 DB 생성
        2) 모든 ORM 테이블 create_all
           (alembic 0001 은 PG 전용 GIN 인덱스 포함이므로 본 스크립트는
            ``Base.metadata.create_all`` 로 대체. PG 환경에서는 ``alembic
            upgrade head`` 가 동등한 작업을 수행한다.)
        3) ``api.seed`` 표준 에이전트 5종 적재
        4) ``AI_data/examples/HE-CAE-2026-000001.json`` 로드 → ingest
        5) ``AgentRecord`` 매핑 (record1 → iga-analyst)
        6) FastAPI ASGI 앱을 ``httpx.AsyncClient`` 로 in-process 부팅
        7) 주요 GET 엔드포인트 호출 후 200 + payload 검증

스크립트는 idempotent: 매 실행마다 새 임시 SQLite 파일을 만들고 종료 시 삭제.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path 보정 — 프로젝트 ``src`` 를 import 경로에 등록.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Windows ProactorEventLoop 회피 (psycopg/asyncpg 의 SQLite 호환성과 무관하지만 안정성).
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# PG 전용 컬럼 → SQLite 호환 어댑터 (tests/conftest.py 와 동일 전략).
# ---------------------------------------------------------------------------
def _install_sqlite_adapters() -> None:
    import json as _json

    from sqlalchemy import BigInteger as _SA_BIGINT
    from sqlalchemy.dialects.postgresql import ARRAY as _PG_ARRAY
    from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
    from sqlalchemy.dialects.postgresql import TIMESTAMP as _PG_TIMESTAMP
    from sqlalchemy.ext.compiler import compiles as _sa_compiles

    @_sa_compiles(_PG_ARRAY, "sqlite")
    def _compile_array_sqlite(_t, _c, **_kw):
        return "JSON"

    @_sa_compiles(_PG_JSONB, "sqlite")
    def _compile_jsonb_sqlite(_t, _c, **_kw):
        return "JSON"

    @_sa_compiles(_PG_TIMESTAMP, "sqlite")
    def _compile_timestamp_sqlite(_t, _c, **_kw):
        return "DATETIME"

    @_sa_compiles(_SA_BIGINT, "sqlite")
    def _compile_bigint_sqlite(_t, _c, **_kw):
        return "INTEGER"

    _ARRAY_ORIG_BIND = _PG_ARRAY.bind_processor
    _ARRAY_ORIG_RESULT = _PG_ARRAY.result_processor
    _JSONB_ORIG_BIND = _PG_JSONB.bind_processor
    _JSONB_ORIG_RESULT = _PG_JSONB.result_processor

    def _array_bind(self, dialect):
        if dialect.name != "sqlite":
            return _ARRAY_ORIG_BIND(self, dialect)

        def process(value):
            if value is None:
                return None
            return _json.dumps(list(value))

        return process

    def _array_result(self, dialect, coltype):
        if dialect.name != "sqlite":
            return _ARRAY_ORIG_RESULT(self, dialect, coltype)

        def process(value):
            if value is None:
                return None
            if isinstance(value, list):
                return value
            try:
                return _json.loads(value)
            except (TypeError, ValueError):
                return value

        return process

    def _jsonb_bind(self, dialect):
        if dialect.name != "sqlite":
            return _JSONB_ORIG_BIND(self, dialect)

        def process(value):
            if value is None:
                return None
            return _json.dumps(value)

        return process

    def _jsonb_result(self, dialect, coltype):
        if dialect.name != "sqlite":
            return _JSONB_ORIG_RESULT(self, dialect, coltype)

        def process(value):
            if value is None:
                return None
            if isinstance(value, (dict, list)):
                return value
            try:
                return _json.loads(value)
            except (TypeError, ValueError):
                return value

        return process

    _PG_ARRAY.bind_processor = _array_bind
    _PG_ARRAY.result_processor = _array_result
    _PG_JSONB.bind_processor = _jsonb_bind
    _PG_JSONB.result_processor = _jsonb_result


# ---------------------------------------------------------------------------
# 헬퍼: pretty-print 한 줄.
# ---------------------------------------------------------------------------
def step(idx: int, label: str) -> None:
    print(f"\n[step {idx}] {label}")


def ok(msg: str) -> None:
    print(f"  OK    {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


# ---------------------------------------------------------------------------
# 메인 시퀀스.
# ---------------------------------------------------------------------------
async def run() -> int:
    _install_sqlite_adapters()

    tmp_dir = Path(tempfile.mkdtemp(prefix="ai_data_smoke_"))
    db_file = tmp_dir / "smoke.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    print(f"[smoke] tmp DB = {db_file}")

    # `api.config` 를 import 하기 전에 환경변수로 DB URL 강제.
    os.environ["DATABASE_URL"] = db_url
    # api.db.base 모듈이 import 되면 settings 가 캡처되므로 주의: 환경변수가 먼저.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from api.db.base import Base  # noqa: F401  (메타데이터 등록 트리거 전에 import)
    from api.db import models  # noqa: F401  (Record 등 테이블 등록)

    # ---- (1) 엔진 생성 ------------------------------------------------------
    step(1, "create temporary SQLite engine + tables")
    engine = create_async_engine(db_url, echo=False, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ok(f"tables created at {db_url}")

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    # ---- (2) 표준 에이전트 시드 --------------------------------------------
    step(2, "seed standard agents (api.seed)")
    from api.seed.cli import seed_agents

    async with sessionmaker() as session:
        counters = await seed_agents(session)
    ok(f"counters={counters}")
    if counters["inserted"] != 5:
        fail(f"expected 5 inserted, got {counters}")
        return 1

    # ---- (3) 샘플 레코드 ingest --------------------------------------------
    step(3, "ingest sample HE-CAE-2026-000001.json")
    sample_path = ROOT.parent / "examples" / "HE-CAE-2026-000001.json"
    if not sample_path.is_file():
        fail(f"sample not found at {sample_path}")
        return 1
    ok(f"sample = {sample_path}")

    from api.ingest.db_writer import write_record
    from api.ingest.loader import load_and_normalize

    record_in = load_and_normalize(sample_path)
    async with sessionmaker() as session:
        result = await write_record(session, record_in)
        await session.commit()
    ok(f"action={result.action} id={record_in.id} sections={result.sections_written}")

    # ---- (4) AgentRecord 매핑 (iga-analyst → record) -----------------------
    step(4, "ensure AgentRecord(iga-analyst, <id>)")
    from sqlalchemy import select

    from api.db.models import Agent, AgentRecord, Record

    async with sessionmaker() as session:
        rec = (
            await session.execute(select(Record).where(Record.id == record_in.id))
        ).scalar_one_or_none()
        if rec is None:
            fail("record not found in DB after ingest")
            return 1

        # 레코드의 agents 배열에 iga-analyst 가 포함되도록 보정 + junction 보강.
        if "iga-analyst" not in (rec.agents or []):
            rec.agents = list(rec.agents or []) + ["iga-analyst"]

        existing_link = await session.get(
            AgentRecord, {"agent_type": "iga-analyst", "record_id": rec.id}
        )
        if existing_link is None:
            session.add(
                AgentRecord(
                    agent_type="iga-analyst",
                    record_id=rec.id,
                    priority=5,
                )
            )
        await session.commit()
    ok("AgentRecord linked iga-analyst")

    # ---- (5) FastAPI app boot via ASGITransport ----------------------------
    step(5, "boot FastAPI ASGI app + httpx client")
    from httpx import ASGITransport, AsyncClient

    from api.db.base import get_session
    from api.main import app

    async def _override() -> AsyncIterator:
        async with sessionmaker() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)

    failures = 0
    async with AsyncClient(transport=transport, base_url="http://smoke") as client:
        # 6.1 GET /api/records -------------------------------------------------
        step(6, "GET /api/records")
        r = await client.get("/api/records", params={"limit": 5})
        if r.status_code != 200:
            fail(f"/api/records → {r.status_code}: {r.text[:200]}")
            failures += 1
        else:
            data = r.json()
            assert isinstance(data, dict) and "items" in data
            ok(f"GET /api/records → 200, total={data.get('total')}")

        # 6.2 GET /api/records/{id} -------------------------------------------
        step(7, f"GET /api/records/{record_in.id}")
        r = await client.get(f"/api/records/{record_in.id}")
        if r.status_code != 200:
            fail(f"/api/records/{record_in.id} → {r.status_code}: {r.text[:200]}")
            failures += 1
        else:
            data = r.json()
            ok(f"GET /api/records/{record_in.id} → 200, title={data.get('title')!r}")

        # 6.3 GET /api/data?agent=iga-analyst ---------------------------------
        step(8, "GET /api/data?agent=iga-analyst")
        r = await client.get("/api/data", params={"agent": "iga-analyst"})
        if r.status_code != 200:
            fail(f"/api/data?agent=iga-analyst → {r.status_code}: {r.text[:200]}")
            failures += 1
        else:
            data = r.json()
            assert "results" in data and "agent" in data
            ok(
                f"GET /api/data?agent=iga-analyst → 200, "
                f"total_matched={data.get('total_matched')}"
            )

        # 6.4 GET /api/agents -------------------------------------------------
        step(9, "GET /api/agents")
        r = await client.get("/api/agents")
        if r.status_code != 200:
            fail(f"/api/agents → {r.status_code}: {r.text[:200]}")
            failures += 1
        else:
            data = r.json()
            agent_types = {a["agent_type"] for a in data}
            assert "iga-analyst" in agent_types, agent_types
            ok(f"GET /api/agents → 200, count={len(data)}")

        # 6.5 GET /api/analytics/distribution ---------------------------------
        step(10, "GET /api/analytics/distribution")
        r = await client.get("/api/analytics/distribution")
        if r.status_code != 200:
            fail(f"/api/analytics/distribution → {r.status_code}: {r.text[:200]}")
            failures += 1
        else:
            data = r.json()
            ok(
                "GET /api/analytics/distribution → 200, "
                f"keys={sorted(data.keys()) if isinstance(data, dict) else type(data).__name__}"
            )

    # ---- 정리 --------------------------------------------------------------
    app.dependency_overrides.pop(get_session, None)
    await engine.dispose()
    try:
        if db_file.exists():
            db_file.unlink()
        tmp_dir.rmdir()
    except OSError:
        pass

    print()
    if failures:
        print(f"SMOKE FAILED — {failures} endpoint(s) returned non-200")
        return 1
    print("SMOKE PASSED — all endpoints returned 200")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(run()))
