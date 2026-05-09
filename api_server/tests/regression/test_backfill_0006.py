"""``api.admin.backfill_0006`` 회귀/단위 테스트.

목적:
    - 모의 DB 에 default 값으로 채워진 레코드(과거 ``write_record`` 버그 흉내)를
      만든 뒤 ``run_backfill`` 이 ``content.meta`` 의 값을 컬럼으로 끌어올리는지
      확인.
    - 두 번 실행해도 안전(idempotent) 한지 확인.
    - ``--dry-run`` 모드는 실제 컬럼을 변경하지 않는지 확인.
"""
from __future__ import annotations

import pytest

from api.admin.backfill_0006 import (
    DEFAULT_CLASSIFICATION,
    DEFAULT_DERIVATION,
    DEFAULT_LANGUAGE,
    DEFAULT_STATUS,
    compute_backfill,
    run_backfill,
)


# ---------------------------------------------------------------------------
# Helpers — 0006 버그를 흉내내어 default-only 레코드를 만든다.
# ---------------------------------------------------------------------------
def _make_buggy_doc(
    *,
    rid: str,
    classification: str = "confidential",
    status: str = "approved",
    domain: str = "battery",
    language: str = "en",
    derivation: str = "translated",
    subject_keywords: list[str] | None = None,
    quality_score: int | None = 88,
):
    """``content.meta`` 에 풍부한 메타가 있지만, 컬럼은 모두 default 인 record."""
    from api.db.models import Record

    content = {
        "schema_version": "1.0",
        "meta": {
            "doc_id": rid.split("DOC-", 1)[-1] if rid.startswith("DOC-") else rid,
            "title": "Buggy Doc",
            "summary": "메타가 컬럼으로 복사되지 않은 레거시 record.",
            "classification": classification,
            "status": status,
            "domain": domain,
            "language": language,
            "derivation": derivation,
            "subject_keywords": subject_keywords or ["IGA", "battery"],
            "quality_score": quality_score,
            "valid_from": "2026-01-01",
            "valid_until": "2026-12-31",
        },
        "toc": [],
        "sections": [
            {
                "id": "1",
                "level": 1,
                "title": "Intro",
                "blocks": [{"type": "paragraph", "text": "Hello"}],
                "figure_refs": [],
                "table_refs": [],
                "children": [],
            }
        ],
        "figures": [],
        "tables": [],
        "sources": [],
    }

    parts = rid.split("-")
    return Record(
        id=rid,
        data_type="DOC",
        division=parts[1],
        team=parts[2],
        year=int(parts[3]),
        seq=int(parts[4]),
        title="Buggy Doc",
        summary="",
        tags=[],
        agents=[],
        schema_version="1.0",
        content=content,
        content_hash="x" * 64,
        # 핵심: 모든 0006 컬럼이 default 값이다 (구버전 버그 시뮬레이션).
        classification=DEFAULT_CLASSIFICATION,
        status=DEFAULT_STATUS,
        domain=None,
        subject_keywords=[],
        source_system=None,
        language=DEFAULT_LANGUAGE,
        derivation=DEFAULT_DERIVATION,
        capabilities=[],
        quality_score=None,
        valid_from=None,
        valid_until=None,
    )


@pytest.mark.asyncio
async def test_compute_backfill_proposes_meta_values(test_session_maker):
    rec = _make_buggy_doc(rid="DOC-HE-CAE-2026-900001")
    proposals = compute_backfill(rec)
    # meta 의 값들이 모두 후보로 올라야 한다.
    assert proposals.get("classification") == "confidential"
    assert proposals.get("status") == "approved"
    assert proposals.get("domain") == "battery"
    assert proposals.get("language") == "en"
    assert proposals.get("derivation") == "translated"
    assert proposals.get("quality_score") == 88
    assert "subject_keywords" in proposals
    assert proposals["subject_keywords"] == ["IGA", "battery"]
    # capabilities 도 content.sections + blocks 가 있으므로 재계산됨.
    assert "sections" in (proposals.get("capabilities") or [])
    # valid_from / valid_until 은 ISO 문자열 → date.
    from datetime import date as _date

    assert isinstance(proposals.get("valid_from"), _date)
    assert isinstance(proposals.get("valid_until"), _date)


@pytest.mark.asyncio
async def test_run_backfill_writes_columns(test_session_maker):
    """3-5 개의 buggy record 를 만들고 backfill 후 컬럼 값을 검증."""
    from api.db.models import Record

    rids = [
        "DOC-HE-CAE-2026-900101",
        "DOC-HE-CAE-2026-900102",
        "DOC-HE-CAE-2026-900103",
    ]

    async with test_session_maker() as session:
        for rid in rids:
            session.add(_make_buggy_doc(rid=rid))
        await session.commit()

    # ---- dry-run: 컬럼 변경 없음 ---------------------------------------
    async with test_session_maker() as session:
        stats = await run_backfill(session, dry_run=True)
        assert stats.scanned == 3
        assert stats.would_update == 3
        assert stats.updated == 0  # dry-run 이므로 적용 안 됨.

    async with test_session_maker() as session:
        for rid in rids:
            rec = await session.get(Record, rid)
            assert rec is not None
            assert rec.classification == DEFAULT_CLASSIFICATION  # 변경 없음
            assert rec.status == DEFAULT_STATUS

    # ---- live: 컬럼 채워짐 ---------------------------------------------
    async with test_session_maker() as session:
        stats = await run_backfill(session, dry_run=False)
        assert stats.updated == 3

    async with test_session_maker() as session:
        for rid in rids:
            rec = await session.get(Record, rid)
            assert rec is not None
            assert rec.classification == "confidential"
            assert rec.status == "approved"
            assert rec.domain == "battery"
            assert rec.language == "en"
            assert rec.derivation == "translated"
            assert rec.quality_score == 88
            assert rec.subject_keywords == ["IGA", "battery"]
            assert "sections" in (rec.capabilities or [])

    # ---- idempotent: 두 번째 실행에서는 변경할 게 없다 -------------------
    async with test_session_maker() as session:
        stats2 = await run_backfill(session, dry_run=False)
        assert stats2.scanned == 3
        assert stats2.would_update == 0
        assert stats2.updated == 0


@pytest.mark.asyncio
async def test_run_backfill_diagnose_mode(test_session_maker):
    rid = "DOC-HE-CAE-2026-900201"
    async with test_session_maker() as session:
        session.add(_make_buggy_doc(rid=rid))
        await session.commit()

    async with test_session_maker() as session:
        stats = await run_backfill(session, dry_run=False, diagnose=True)
        assert stats.scanned == 1
        assert stats.default_only == 1  # 6 컬럼 모두 default.
        assert stats.would_update == 1
        assert stats.updated == 0  # diagnose → 변경 없음.

    # diagnose 후에도 컬럼은 그대로다.
    from api.db.models import Record

    async with test_session_maker() as session:
        rec = await session.get(Record, rid)
        assert rec is not None
        assert rec.classification == DEFAULT_CLASSIFICATION


@pytest.mark.asyncio
async def test_backfill_skips_already_correct_records(test_session_maker):
    """이미 올바른 컬럼 값을 가진 record 는 변경 후보가 되지 않는다."""
    from api.db.models import Record

    rid = "DOC-HE-CAE-2026-900301"
    async with test_session_maker() as session:
        rec = _make_buggy_doc(rid=rid)
        # 이미 backfill 된 상태로 가정.
        rec.classification = "confidential"
        rec.status = "approved"
        rec.domain = "battery"
        rec.language = "en"
        rec.derivation = "translated"
        rec.quality_score = 88
        rec.subject_keywords = ["IGA", "battery"]
        rec.capabilities = ["sections", "blocks"]
        from datetime import date as _date

        rec.valid_from = _date(2026, 1, 1)
        rec.valid_until = _date(2026, 12, 31)
        session.add(rec)
        await session.commit()

    async with test_session_maker() as session:
        stats = await run_backfill(session, dry_run=False)
        assert stats.would_update == 0
