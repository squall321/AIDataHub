"""Ingestion 단위/통합 테스트.

DB 의존 테스트는 SQLite (aiosqlite) 로 실행한다. Agent 1 의 ``api.db.models`` 가
PostgreSQL 전용 타입(JSONB/ARRAY/TIMESTAMP)을 사용하므로, 본 테스트는
**동일 컬럼 구조의 SQLite 호환 미러 모델**을 ``tests.mirror_models`` 에 정의해
ingestion 동작만 검증한다.

ingestion 로직(``api.ingest.db_writer``)은 모델 클래스를 인자로 받지 않고 함수
내부에서 ``api.db.models`` 를 import 하므로, 미러 모델을 ``sys.modules`` 에 주입한 뒤
재로드하는 방식으로 격리한다.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from api.ingest.normalizer import (
    canonical_json,
    compute_content_hash,
    detect_variant,
    normalize,
)

EXAMPLE_DOC_PATH = Path("d:/Personal/AI_data/examples/HE-CAE-2026-000001.json")


# ===========================================================================
# 정규화 (DB 무관)
# ===========================================================================
class TestNormalizeVariantDetection:
    def test_doc(self) -> None:
        raw = {
            "schema_version": "1.0",
            "meta": {"doc_id": "DOC-HE-CAE-2026-000001"},
            "sections": [],
        }
        assert detect_variant(raw) == "DOC"

    def test_data(self) -> None:
        raw = {"headers": ["a"], "rows": [[1]]}
        assert detect_variant(raw) == "DATA"

    def test_sim(self) -> None:
        raw = {"solver": "LS-DYNA", "inputs": {}}
        assert detect_variant(raw) == "SIM"

    def test_cad(self) -> None:
        raw = {"cad_type": "MCAD"}
        assert detect_variant(raw) == "CAD"

    def test_other(self) -> None:
        assert detect_variant({"random": "blob"}) == "OTHER"

    def test_explicit_data_type_wins(self) -> None:
        raw = {"data_type": "LOG", "headers": [], "rows": []}
        assert detect_variant(raw) == "LOG"


@pytest.mark.skipif(
    not EXAMPLE_DOC_PATH.exists(),
    reason="example DOC fixture missing",
)
class TestNormalizeDocument:
    def test_normalize_real_example(self) -> None:
        raw = json.loads(EXAMPLE_DOC_PATH.read_text(encoding="utf-8-sig"))
        record = normalize(raw)
        # ID 가 레거시 → DOC- 프리픽스 추가됨
        assert record.id == "DOC-HE-CAE-2026-000001"
        assert record.data_type == "DOC"
        assert record.title == "iga_guide_test"
        assert record.department == "HE-CAE"
        assert record.author == "python-docx"
        assert "meta" in record.content
        assert "sections" in record.content
        assert isinstance(record.content["sections"], list)
        assert len(record.content["sections"]) > 0

    def test_content_hash_stable(self) -> None:
        raw = json.loads(EXAMPLE_DOC_PATH.read_text(encoding="utf-8-sig"))
        r1 = normalize(raw)
        r2 = normalize(raw)
        assert compute_content_hash(r1.content) == compute_content_hash(r2.content)
        # 키 순서를 바꿔도 동일.
        s = canonical_json(r1.content)
        reordered = json.loads(s)
        assert compute_content_hash(reordered) == compute_content_hash(r1.content)


class TestNormalizeData:
    def test_basic(self) -> None:
        raw = {
            "id": "DATA-HE-CAE-2026-000010",
            "data_type": "DATA",
            "title": "metric props",
            "headers": ["name", "value"],
            "rows": [["a", 1], ["b", 2]],
            "units": {"value": "MPa"},
            "tags": ["material"],
        }
        rec = normalize(raw)
        assert rec.data_type == "DATA"
        assert rec.id == "DATA-HE-CAE-2026-000010"
        assert rec.content["headers"] == ["name", "value"]
        assert rec.content["rows"] == [["a", 1], ["b", 2]]
        assert rec.tags == ["material"]


class TestNormalizeSim:
    def test_basic(self) -> None:
        raw = {
            "id": "SIM-HE-CAE-2026-000005",
            "solver": "LS-DYNA",
            "inputs": {"k_file": "x.k"},
            "outputs": {"d3plot": "/x"},
        }
        rec = normalize(raw)
        assert rec.data_type == "SIM"
        assert rec.content["solver"] == "LS-DYNA"


class TestNormalizeCad:
    def test_basic(self) -> None:
        raw = {
            "id": "CAD-HE-CAE-2026-000020",
            "cad_type": "MCAD",
            "file_format": "STEP",
            "file_metadata": {"path": "/x.step", "size_bytes": 100},
        }
        rec = normalize(raw)
        assert rec.data_type == "CAD"
        assert rec.content["cad_type"] == "MCAD"


class TestNormalizeOther:
    def test_other_preserves_raw(self) -> None:
        raw = {
            "id": "OTHER-HE-CAE-2026-000099",
            "title": "free-form",
            "stuff": {"x": 1},
        }
        rec = normalize(raw)
        assert rec.data_type == "OTHER"
        assert rec.content["stuff"] == {"x": 1}


class TestLegacyIdHandling:
    def test_doc_legacy_prefixed(self) -> None:
        raw = {
            "schema_version": "1.0",
            "meta": {"doc_id": "HE-CAE-2026-000001", "title": "t"},
            "sections": [],
        }
        rec = normalize(raw)
        assert rec.id == "DOC-HE-CAE-2026-000001"

    def test_data_legacy_prefixed(self) -> None:
        raw = {
            "id": "HE-CAE-2026-000002",
            "headers": ["a"],
            "rows": [[1]],
        }
        rec = normalize(raw)
        assert rec.id == "DATA-HE-CAE-2026-000002"
        assert rec.data_type == "DATA"


class TestCanonicalJson:
    def test_keys_sorted(self) -> None:
        assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'

    def test_unicode_preserved(self) -> None:
        s = canonical_json({"k": "한글"})
        assert "한글" in s

    def test_nan_rejected(self) -> None:
        with pytest.raises(ValueError):
            canonical_json({"x": float("nan")})


# ===========================================================================
# DB 의존 테스트 — SQLite 미러 모델 사용
# ===========================================================================
@pytest.fixture
async def db_session():
    """SQLite 인메모리 + 미러 모델 + AsyncSession 픽스처.

    매 테스트마다 새 엔진/스키마/세션을 만들어 격리한다.
    Agent 1 의 ``api.db.models`` 가 PG 전용 타입을 쓰므로 ``tests.mirror_models``
    를 ``sys.modules['api.db.models']`` 로 alias 하고 ``api.ingest.db_writer`` 를
    reload 해 함수 내부 import 가 미러를 바라보게 한다.
    """
    aiosqlite = pytest.importorskip("aiosqlite")
    _ = aiosqlite  # silence unused
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # mirror_models 는 한 번만 import 되어야 한다 (DeclarativeBase 충돌 방지).
    if "tests.mirror_models" in sys.modules:
        mirror = sys.modules["tests.mirror_models"]
    else:
        mirror = importlib.import_module("tests.mirror_models")

    # api.db.models 자리에 미러 alias. (Agent 1 의 진짜 모듈이 이미 로드되어 있을
    # 수 있으므로 백업 후 교체.)
    real_models = sys.modules.get("api.db.models")
    sys.modules["api.db.models"] = mirror

    # api.ingest.db_writer 가 이미 import 되어 있다면 reload 해 미러 모델을 바인딩.
    if "api.ingest.db_writer" in sys.modules:
        importlib.reload(sys.modules["api.ingest.db_writer"])

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(mirror.Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            yield session, mirror
            await session.rollback()
    finally:
        await engine.dispose()
        # 정리: 원본 모듈 복구.
        if real_models is not None:
            sys.modules["api.db.models"] = real_models
        else:
            sys.modules.pop("api.db.models", None)
        if "api.ingest.db_writer" in sys.modules:
            importlib.reload(sys.modules["api.ingest.db_writer"])


@pytest.mark.asyncio
async def test_db_write_creates_record(db_session) -> None:
    session, models = db_session
    from api.ingest.db_writer import write_record

    raw = {
        "id": "DATA-HE-CAE-2026-000010",
        "data_type": "DATA",
        "title": "test data",
        "headers": ["a", "b"],
        "rows": [[1, 2]],
        "agents": ["cline_sr"],
    }
    rec = normalize(raw)
    result = await write_record(session, rec)
    await session.commit()

    assert result.action == "inserted"
    fetched = await session.get(models.Record, "DATA-HE-CAE-2026-000010")
    assert fetched is not None
    assert fetched.title == "test data"
    assert fetched.data_type == "DATA"
    assert fetched.division == "HE"
    assert fetched.team == "CAE"
    assert fetched.year == 2026
    assert fetched.seq == 10
    assert fetched.content_hash is not None
    assert fetched.agents == ["cline_sr"]


@pytest.mark.asyncio
async def test_db_write_idempotent(db_session) -> None:
    session, models = db_session
    from api.ingest.db_writer import write_record

    raw = {
        "id": "DATA-HE-CAE-2026-000011",
        "data_type": "DATA",
        "title": "v1",
        "headers": ["a"],
        "rows": [[1]],
    }
    rec = normalize(raw)
    r1 = await write_record(session, rec)
    await session.commit()
    assert r1.action == "inserted"

    # 동일 입력 → skipped
    r2 = await write_record(session, rec)
    await session.commit()
    assert r2.action == "skipped"

    # 명시적 caption 을 그대로 두고 무관 필드(top-level summary)를 바꿔도 content
    # 해시가 동일하므로 skipped 유지. (DATA 변종에서 caption 은 raw.title 로부터
    # 유도되므로, top-level title 변경은 caption 변경으로 이어져 content 가 달라진다.)
    raw2 = dict(raw)
    raw2["caption"] = "v1"  # 명시 caption 으로 고정
    raw2["summary"] = "summary-only-change"  # 공통 메타만 변경
    rec2 = normalize(raw2)
    r3 = await write_record(session, rec2)
    await session.commit()
    assert r3.action == "skipped"

    # content 변경 → updated
    raw3 = dict(raw)
    raw3["rows"] = [[1], [2]]
    rec3 = normalize(raw3)
    r4 = await write_record(session, rec3)
    await session.commit()
    assert r4.action == "updated"


@pytest.mark.skipif(
    not EXAMPLE_DOC_PATH.exists(),
    reason="example DOC fixture missing",
)
@pytest.mark.asyncio
async def test_db_write_creates_sections(db_session) -> None:
    session, models = db_session
    from sqlalchemy import select

    from api.ingest.db_writer import write_record

    raw = json.loads(EXAMPLE_DOC_PATH.read_text(encoding="utf-8-sig"))
    rec = normalize(raw)
    result = await write_record(session, rec)
    await session.commit()

    assert result.action == "inserted"
    assert result.sections_written > 0

    rows = (
        await session.execute(
            select(models.RecordSection).where(
                models.RecordSection.record_id == rec.id
            )
        )
    ).scalars().all()
    assert len(rows) > 0
    # level 1~3 만 포함되어야 함.
    assert all(1 <= r.level <= 3 for r in rows)
    # 섹션 id 는 unique.
    sids = [r.section_id for r in rows]
    assert len(sids) == len(set(sids))


@pytest.mark.asyncio
async def test_db_write_agent_junction(db_session) -> None:
    session, models = db_session
    from sqlalchemy import select

    from api.ingest.db_writer import write_record

    raw = {
        "id": "DATA-HE-CAE-2026-000050",
        "data_type": "DATA",
        "title": "agents test",
        "headers": ["a"],
        "rows": [[1]],
        "agents": ["cline_sr", "rag_general"],
    }
    rec = normalize(raw)
    await write_record(session, rec)
    await session.commit()

    junction = (
        await session.execute(
            select(models.AgentRecord).where(
                models.AgentRecord.record_id == rec.id
            )
        )
    ).scalars().all()
    assert {a.agent_type for a in junction} == {"cline_sr", "rag_general"}
    # priority 가 1, 2 로 부여됨.
    pri = sorted(a.priority for a in junction)
    assert pri == [1, 2]
