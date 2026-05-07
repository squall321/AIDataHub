"""pytest 공통 픽스처.

Agent 1/2/3 산출물에 의존한다. 의존 모듈이 아직 없으면 모듈 단위 skip을 적용한다.

제공 픽스처:
    - test_engine        : SQLite in-memory async 엔진
    - test_session_maker : async_sessionmaker(AsyncSession) (테이블 자동 생성/삭제)
    - test_session       : 테스트용 단일 AsyncSession
    - test_client        : httpx.AsyncClient(ASGITransport, app=api.main:app)
    - sample_*_record_dict : DOC/DATA/SIM/CAD 표본 입력 dict (정규화 직전)
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# sys.path 보강 (pyproject pythonpath 미적용 환경 대비)
# ---------------------------------------------------------------------------
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ---------------------------------------------------------------------------
# PostgreSQL 전용 타입을 SQLite 가 처리할 수 있도록 어댑터 등록.
# (테스트 모듈 import 시점에 1회 등록)
#
# 1) DDL: ARRAY/JSONB/TIMESTAMP -> JSON/DATETIME 으로 컴파일
# 2) bind_processor: 파이썬 list/dict -> JSON 문자열 직렬화 (SQLite 바인딩용)
# 3) result_processor: JSON 문자열 -> list/dict 역직렬화
# ---------------------------------------------------------------------------
try:
    import json as _json

    from sqlalchemy.dialects.postgresql import ARRAY as _PG_ARRAY
    from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
    from sqlalchemy.dialects.postgresql import TIMESTAMP as _PG_TIMESTAMP
    from sqlalchemy.ext.compiler import compiles as _sa_compiles

    @_sa_compiles(_PG_ARRAY, "sqlite")
    def _compile_array_sqlite(_t, _c, **_kw):  # noqa: D401
        return "JSON"

    @_sa_compiles(_PG_JSONB, "sqlite")
    def _compile_jsonb_sqlite(_t, _c, **_kw):
        return "JSON"

    @_sa_compiles(_PG_TIMESTAMP, "sqlite")
    def _compile_timestamp_sqlite(_t, _c, **_kw):
        return "DATETIME"

    # BigInteger PK 가 SQLite 에서 autoincrement 되지 않는 문제 해결
    # (SQLite 는 INTEGER PRIMARY KEY 만 ROWID alias 로 자동 증가시킨다)
    from sqlalchemy import BigInteger as _SA_BIGINT

    @_sa_compiles(_SA_BIGINT, "sqlite")
    def _compile_bigint_sqlite(_t, _c, **_kw):
        return "INTEGER"

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
            # PG ARRAY 컬럼의 server_default="{}" 가 SQLite 에 그대로
            # 저장되면 결과가 dict({}) 또는 string("{}") 으로 돌아온다.
            # ARRAY 는 항상 list 여야 하므로 비-list 결과는 [] 로 강제한다.
            if isinstance(value, str):
                if value in ("{}", ""):
                    return []
                try:
                    decoded = _json.loads(value)
                except (TypeError, ValueError):
                    return []
                if isinstance(decoded, list):
                    return decoded
                return []
            # 그 외 dict 등은 빈 리스트로 정규화.
            return []

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

    _ARRAY_ORIG_BIND = _PG_ARRAY.bind_processor
    _ARRAY_ORIG_RESULT = _PG_ARRAY.result_processor
    _JSONB_ORIG_BIND = _PG_JSONB.bind_processor
    _JSONB_ORIG_RESULT = _PG_JSONB.result_processor

    _PG_ARRAY.bind_processor = _array_bind
    _PG_ARRAY.result_processor = _array_result
    _PG_JSONB.bind_processor = _jsonb_bind
    _PG_JSONB.result_processor = _jsonb_result

except Exception:  # pragma: no cover
    pass


# ---------------------------------------------------------------------------
# Async engine / session (SQLite in-memory)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """세션 스코프 SQLite in-memory 비동기 엔진.

    PostgreSQL 전용 컬럼(ARRAY/JSONB 등)을 SQLite에서 사용하면 깨지므로,
    Agent 1 모델이 아직 PG 전용이면 이 픽스처는 그대로 못 쓸 수 있다.
    이런 경우 호출 측에서 직접 skip 하면 된다.
    """
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
    except ImportError:  # pragma: no cover
        pytest.skip("sqlalchemy[asyncio] not available", allow_module_level=False)

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        future=True,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session_maker(test_engine):
    """함수 스코프: Base.metadata 기반 테이블 생성/세션메이커 반환."""
    try:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from api.db.base import Base  # noqa: F401  (Agent 1)
        # 모델 import → metadata에 테이블 등록
        import api.db.models  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"Agent 1 DB modules missing: {exc}")

    # 테이블 생성 (PG 전용 타입이면 여기서 OperationalError 발생 → skip)
    try:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:
        pytest.skip(f"DB schema create_all failed on SQLite (PG-only types?): {exc}")

    maker = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    yield maker

    # 정리
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def test_session(test_session_maker) -> AsyncGenerator[Any, None]:
    """단일 비동기 세션 (자동 롤백/닫기)."""
    async with test_session_maker() as session:
        try:
            yield session
        finally:
            await session.rollback()


# ---------------------------------------------------------------------------
# HTTP test client (ASGI transport)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def test_client() -> AsyncGenerator[Any, None]:
    """httpx.AsyncClient with ASGITransport against api.main:app."""
    try:
        from httpx import ASGITransport, AsyncClient

        from api.main import app
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"FastAPI app not importable: {exc}")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


# ---------------------------------------------------------------------------
# DB-backed client (Agent 3 라우터 테스트용)
# `test_session_maker` + `get_session` 의존성 오버라이드를 결합한 클라이언트.
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def db_client(test_session_maker) -> AsyncGenerator[Any, None]:
    """SQLite 세션을 의존성 주입한 httpx 클라이언트."""
    try:
        from httpx import ASGITransport, AsyncClient

        from api.db.base import get_session
        from api.main import app
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"app/db not importable: {exc}")

    async def _override():
        async with test_session_maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.pop(get_session, None)


@pytest_asyncio.fixture
async def seed_records(test_session_maker):
    """라우터 테스트용 표본 시드. 반환값은 record id 매핑."""
    from api.db.models import Agent, AgentRecord, Record, RecordSection

    async with test_session_maker() as session:
        agent_iga = Agent(
            agent_type="iga-analyst",
            name="IGA Analyst",
            description="IGA tasks",
            common_tags=["IGA", "FEM"],
            data_types=["DOC", "DATA"],
        )
        agent_oga = Agent(
            agent_type="oga-analyst",
            name="OGA Analyst",
            description="OGA tasks",
            common_tags=["OGA"],
            data_types=["DOC"],
        )
        session.add_all([agent_iga, agent_oga])

        rec1 = Record(
            id="DOC-HE-CAE-2026-000001",
            data_type="DOC",
            division="HE",
            team="CAE",
            year=2026,
            seq=1,
            title="IGA offset 분석 보고서",
            summary="IGA offset 패턴에 대한 종합 분석",
            tags=["IGA", "offset", "FEM"],
            agents=["iga-analyst"],
            content={"raw": "..."},
        )
        rec2 = Record(
            id="DATA-HE-CAE-2026-000002",
            data_type="DATA",
            division="HE",
            team="CAE",
            year=2026,
            seq=2,
            title="배터리 셀 측정 데이터",
            summary="셀 측정 raw values",
            tags=["battery", "IGA"],
            agents=["iga-analyst", "oga-analyst"],
            content={"rows": []},
        )
        rec3 = Record(
            id="DOC-HE-CAE-2025-000003",
            data_type="DOC",
            division="HE",
            team="CAE",
            year=2025,
            seq=3,
            title="OGA 정책 문서",
            summary="OGA 운용 가이드",
            tags=["OGA", "policy"],
            agents=["oga-analyst"],
            content={},
        )
        session.add_all([rec1, rec2, rec3])
        await session.flush()

        session.add_all(
            [
                RecordSection(
                    record_id=rec1.id,
                    section_id="4.2",
                    level=2,
                    title="Offset 계산",
                    content_text=(
                        "이 섹션은 offset 계산에 대해 설명한다. "
                        "offset 은 0.1mm 단위로 측정된다."
                    ),
                ),
                RecordSection(
                    record_id=rec1.id,
                    section_id="4.3",
                    level=2,
                    title="결과 해석",
                    content_text="offset 결과는 다음 표와 같다.",
                ),
                RecordSection(
                    record_id=rec3.id,
                    section_id="1.1",
                    level=1,
                    title="개요",
                    content_text="OGA 운용에 대한 일반 정책.",
                ),
            ]
        )
        session.add_all(
            [
                AgentRecord(agent_type="iga-analyst", record_id=rec1.id, priority=5),
                AgentRecord(agent_type="iga-analyst", record_id=rec2.id, priority=3),
                AgentRecord(agent_type="oga-analyst", record_id=rec2.id, priority=2),
                AgentRecord(agent_type="oga-analyst", record_id=rec3.id, priority=4),
            ]
        )
        await session.commit()

    return {
        "rec1": "DOC-HE-CAE-2026-000001",
        "rec2": "DATA-HE-CAE-2026-000002",
        "rec3": "DOC-HE-CAE-2025-000003",
    }


# ---------------------------------------------------------------------------
# Sample record dicts (Agent 2 normalizer 입력 형태)
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_doc_record_dict() -> dict[str, Any]:
    """DOC variant: 변환기 산출 JSON 형태 (top-level meta/sections, normalize() 입력용)."""
    return {
        "id": "DOC-HE-CAE-2026-000001",
        "data_type": "DOC",
        "schema_version": "1.0",
        "tags": ["iga", "lsdyna", "guide"],
        "agents": ["iga-analyst"],
        "author": "qa-bot",
        "department": "HE-CAE",
        "project": "ai-data-hub",
        "version": "1.0",
        "source_file": "iga_guide_test.docx",
        "meta": {
            "doc_id": "HE-CAE-2026-000001",
            "title": "IGA 가이드 (테스트)",
            "summary": "Isogeometric analysis 워크플로 가이드.",
            "author": "qa-bot",
            "department": "HE-CAE",
        },
        "toc": [
            {"id": "1", "level": 1, "title": "개요"},
            {"id": "1.1", "level": 2, "title": "Offset 처리"},
        ],
        "sections": [
            {
                "id": "1",
                "level": 1,
                "title": "개요",
                "blocks": [{"type": "paragraph", "text": "IGA 가이드 본문."}],
                "figure_refs": [],
                "table_refs": [],
                "children": [
                    {
                        "id": "1.1",
                        "level": 2,
                        "title": "Offset 처리",
                        "blocks": [
                            {
                                "type": "paragraph",
                                "text": "offset 값은 0.5mm로 설정한다.",
                            }
                        ],
                        "figure_refs": [],
                        "table_refs": [],
                        "children": [],
                    }
                ],
            }
        ],
        "figures": [],
        "tables": [],
        "sources": [],
    }


@pytest.fixture
def sample_data_record_dict() -> dict[str, Any]:
    """DATA variant: headers/rows top-level + agent 메타."""
    return {
        "id": "DATA-HE-CAE-2026-000002",
        "data_type": "DATA",
        "title": "배터리 펀치 시험 결과",
        "summary": "노일 관통 시험 변형률 측정.",
        "tags": ["battery", "test", "nail"],
        "agents": ["cae-reporter"],
        "author": "qa-bot",
        "department": "HE-CAE",
        "version": "1.0",
        "headers": ["time", "force", "strain"],
        "rows": [
            [0.0, 0.0, 0.0],
            [0.1, 12.5, 0.02],
        ],
        "units": {"time": "ms", "force": "N", "strain": "%"},
        "notes": "노일 관통 시험 변형률 측정.",
    }


@pytest.fixture
def sample_sim_record_dict() -> dict[str, Any]:
    """SIM variant: solver + inputs/outputs."""
    return {
        "id": "SIM-HE-CAE-2026-000003",
        "data_type": "SIM",
        "title": "Battery Side Crash 시뮬레이션",
        "summary": "측면 충돌 LS-DYNA explicit 해석.",
        "tags": ["lsdyna", "crash", "battery"],
        "agents": ["cae-reporter"],
        "author": "qa-bot",
        "department": "HE-CAE",
        "version": "1.0",
        "solver": "LS-DYNA",
        "solver_version": "R14",
        "inputs": {"k_file": "battery_side_test.k"},
        "outputs": {"d3plot": "/results/d3plot"},
        "runtime": {"wall_seconds": 3600},
    }


@pytest.fixture
def sample_cad_record_dict() -> dict[str, Any]:
    """CAD variant: cad_type + file_format + file_metadata."""
    return {
        "id": "CAD-HE-CAE-2026-000004",
        "data_type": "CAD",
        "title": "배터리 모듈 CAD 모델",
        "summary": "STEP 파일 + 메타.",
        "tags": ["battery", "cad", "step"],
        "agents": ["cad-curator"],
        "author": "qa-bot",
        "department": "HE-CAE",
        "version": "1.0",
        "cad_type": "MCAD",
        "file_format": "STEP",
        "file_metadata": {
            "path": "battery_module.stp",
            "size_bytes": 1048576,
            "bbox_mm": [320.0, 180.0, 95.0],
            "mass_kg": 12.6,
        },
        "components": [],
    }


# ---------------------------------------------------------------------------
# Asyncio 정책 (Windows ProactorEventLoop 이슈 회피)
# ---------------------------------------------------------------------------
if sys.platform.startswith("win") and os.environ.get("PYTEST_WIN_SELECTOR_LOOP", "1") == "1":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:  # pragma: no cover
        pass
