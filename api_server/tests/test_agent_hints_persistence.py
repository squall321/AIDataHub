"""Migration 0007 — record agent_hints / related_record_ids / query_examples /
access_pattern 라운드트립 검증.

raw normalize → write_record → DB → RecordOut 사이클을 확인한다.
"""
from __future__ import annotations

import pytest

from api.ingest.db_writer import write_record
from api.ingest.normalizer import normalize
from api.schemas import RecordIn, RecordOut


def _base_data(**overrides) -> dict:
    base = {
        "id": "DOC-HE-CAE-2026-000077",
        "data_type": "DOC",
        "schema_version": "1.0",
        "title": "Hints test doc",
        "summary": "test",
        "tags": ["test"],
        "agents": ["iga-analyst"],
        "meta": {"doc_id": "HE-CAE-2026-000077", "title": "Hints test doc"},
        "sections": [],
        "toc": [],
        "figures": [],
        "tables": [],
        "sources": [],
    }
    base.update(overrides)
    return base


def test_record_in_accepts_new_fields() -> None:
    rec = RecordIn(
        id="DOC-HE-CAE-2026-000010",
        data_type="DOC",
        title="x",
        agent_hints="Use this for IGA workflow only.",
        related_record_ids=["DOC-HE-CAE-2026-000011"],
        query_examples=["IGA 가이드", "offset 계산"],
        access_pattern="frequent",
    )
    assert rec.agent_hints == "Use this for IGA workflow only."
    assert rec.related_record_ids == ["DOC-HE-CAE-2026-000011"]
    assert rec.query_examples == ["IGA 가이드", "offset 계산"]
    assert rec.access_pattern == "frequent"


def test_record_in_defaults() -> None:
    """신규 필드는 기본값을 가진다."""
    rec = RecordIn(id="DOC-HE-CAE-2026-000010", data_type="DOC", title="x")
    assert rec.agent_hints is None
    assert rec.related_record_ids == []
    assert rec.query_examples == []
    assert rec.access_pattern == "occasional"


def test_record_in_invalid_access_pattern() -> None:
    """``access_pattern`` 은 enum 외 값을 거부."""
    with pytest.raises(Exception):
        RecordIn(
            id="DOC-HE-CAE-2026-000010",
            data_type="DOC",
            title="x",
            access_pattern="never",  # type: ignore[arg-type]
        )


def test_normalizer_picks_up_meta_agent_hints() -> None:
    raw = _base_data()
    raw["meta"]["agent_hints"] = "From meta only"
    raw["meta"]["query_examples"] = ["q1", "q2"]
    raw["meta"]["related_record_ids"] = ["DOC-HE-CAE-2026-000099"]
    raw["meta"]["access_pattern"] = "rare"

    rec = normalize(raw)
    assert rec.agent_hints == "From meta only"
    assert rec.query_examples == ["q1", "q2"]
    assert rec.related_record_ids == ["DOC-HE-CAE-2026-000099"]
    assert rec.access_pattern == "rare"


def test_normalizer_picks_up_top_level_hints() -> None:
    """raw top-level agent_hints 는 DOC 의 meta 보다 fallback."""
    raw = _base_data(agent_hints="From top-level", access_pattern="frequent")
    rec = normalize(raw)
    # DOC: meta 우선 → meta 없으면 raw top-level. 여기선 meta 가 안 채워짐.
    assert rec.agent_hints == "From top-level"
    assert rec.access_pattern == "frequent"


@pytest.mark.asyncio
async def test_db_writer_persists_new_fields(test_session) -> None:
    from sqlalchemy import select

    from api.db.models import Record

    rec_in = RecordIn(
        id="DOC-HE-CAE-2026-000088",
        data_type="DOC",
        title="t",
        agents=["iga-analyst"],
        content={"sections": []},
        agent_hints="Persist me",
        related_record_ids=["DOC-HE-CAE-2026-000077"],
        query_examples=["sample query"],
        access_pattern="frequent",
    )
    result = await write_record(test_session, rec_in)
    await test_session.commit()

    rec = result.record
    assert rec.agent_hints == "Persist me"
    assert list(rec.related_record_ids) == ["DOC-HE-CAE-2026-000077"]
    assert list(rec.query_examples) == ["sample query"]
    assert rec.access_pattern == "frequent"

    # 다시 읽어서 RecordOut 라운드트립 (lazy 관계 회피).
    fresh = (
        await test_session.execute(select(Record).where(Record.id == rec.id))
    ).scalar_one()
    out = RecordOut.model_validate(fresh)
    assert out.agent_hints == "Persist me"
    assert out.access_pattern == "frequent"
    assert out.related_record_ids == ["DOC-HE-CAE-2026-000077"]
    assert out.query_examples == ["sample query"]
