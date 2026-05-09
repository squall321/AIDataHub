"""S1. auto-seq tests.

Validates ``api.services.seq.next_seq`` behavior under SQLite. The helper
is also indirectly exercised through ``/api/convert/ingest`` when
``seq=0`` is submitted.
"""
from __future__ import annotations

import io
import textwrap

import pytest


def _make_md_bytes() -> bytes:
    return textwrap.dedent(
        """\
        # 자동 seq 테스트

        본문 한 줄.
        """
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# next_seq() — 단위 테스트
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_next_seq_empty_returns_one(test_session) -> None:
    from api.services.seq import next_seq

    val = await next_seq(
        test_session,
        data_type="DOC",
        division="HE",
        team="CAE",
        year=2026,
    )
    assert val == 1


@pytest.mark.asyncio
async def test_next_seq_increments_after_insert(test_session) -> None:
    from api.db.models import Record
    from api.services.seq import next_seq

    rec = Record(
        id="DOC-HE-CAE-2026-000007",
        data_type="DOC",
        division="HE",
        team="CAE",
        year=2026,
        seq=7,
        title="seed",
        summary="",
        tags=[],
        agents=[],
        content={},
    )
    test_session.add(rec)
    await test_session.flush()

    val = await next_seq(
        test_session,
        data_type="DOC",
        division="HE",
        team="CAE",
        year=2026,
    )
    assert val == 8


@pytest.mark.asyncio
async def test_next_seq_scoped_by_natural_key(test_session) -> None:
    """다른 (data_type, division, team, year) 는 카운터를 공유하지 않는다."""
    from api.db.models import Record
    from api.services.seq import next_seq

    test_session.add(
        Record(
            id="DOC-HE-CAE-2026-000005",
            data_type="DOC",
            division="HE",
            team="CAE",
            year=2026,
            seq=5,
            title="A",
            summary="",
            tags=[],
            agents=[],
            content={},
        )
    )
    await test_session.flush()

    # 다른 division → 1.
    v1 = await next_seq(
        test_session, data_type="DOC", division="DA", team="CAE", year=2026
    )
    assert v1 == 1
    # 다른 year → 1.
    v2 = await next_seq(
        test_session, data_type="DOC", division="HE", team="CAE", year=2025
    )
    assert v2 == 1
    # 다른 data_type → 1.
    v3 = await next_seq(
        test_session, data_type="DATA", division="HE", team="CAE", year=2026
    )
    assert v3 == 1
    # 같은 키 → 6.
    v4 = await next_seq(
        test_session, data_type="DOC", division="HE", team="CAE", year=2026
    )
    assert v4 == 6


# ---------------------------------------------------------------------------
# /api/convert/ingest 통합 — seq=0 자동 할당
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ingest_seq_zero_assigns_next(db_client) -> None:
    """seq=0 으로 두 번 업로드하면 1, 2 순서로 자동 할당된다."""
    payload = _make_md_bytes()

    # 파일명을 다르게 하여 idempotency skip 을 피한다 (content_hash 가 다르도록 본문 변경).
    f1_bytes = b"# first\n\nbody-A\n"
    f2_bytes = b"# second\n\nbody-B\n"

    form = {
        "division": "HE",
        "team": "CAE",
        "year": "2026",
        "seq": "0",
    }
    files1 = {"file": ("auto1.md", f1_bytes, "text/markdown")}
    r1 = await db_client.post("/api/convert/ingest", files=files1, data=form)
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["assigned_seq"] >= 1
    seq1 = body1["assigned_seq"]
    assert body1["record"]["seq"] == seq1

    files2 = {"file": ("auto2.md", f2_bytes, "text/markdown")}
    r2 = await db_client.post("/api/convert/ingest", files=files2, data=form)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["assigned_seq"] == seq1 + 1
