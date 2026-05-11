"""Additional unit tests for ``api.services.seq.next_seq``.

Complements ``tests/test_auto_seq.py`` by validating case-normalization
(team/group uppercased) and that consecutive calls without inserts
return the same value (since no row was added).
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_next_seq_normalizes_division_team_case(test_session):
    """``team="he"`` 와 ``"HE"`` 는 같은 카운터로 취급되어야 한다."""
    from api.db.models import Record
    from api.services.seq import next_seq

    test_session.add(
        Record(
            id="DOC-HE-CAE-2026-0000000003",
            data_type="DOC",
            team="HE",
            group="CAE",
            year=2026,
            seq=3,
            title="seed",
            summary="",
            tags=[],
            agents=[],
            content={},
        )
    )
    await test_session.flush()

    val_lower = await next_seq(
        test_session, data_type="DOC", team="he", group="cae", year=2026
    )
    val_upper = await next_seq(
        test_session, data_type="DOC", team="HE", group="CAE", year=2026
    )
    assert val_lower == val_upper == 4


@pytest.mark.asyncio
async def test_next_seq_repeated_calls_no_inserts(test_session):
    """레코드 추가 없이 두 번 호출하면 동일한 ``MAX(seq)+1`` 을 반환한다."""
    from api.services.seq import next_seq

    a = await next_seq(test_session, data_type="DOC", team="HE", group="CAE", year=2026)
    b = await next_seq(test_session, data_type="DOC", team="HE", group="CAE", year=2026)
    assert a == b == 1
