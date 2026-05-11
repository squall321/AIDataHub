"""Record diff 엔드포인트 테스트 (Migration 0008 / G4)."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_diff_meta_changes(db_client, test_session_maker) -> None:
    """제목/요약/태그 변경이 ``meta_changes`` 에 dict 로 잡혀야 한다."""
    from api.db.models import Record

    async with test_session_maker() as session:
        rec_a = Record(
            id="DOC-HE-CAE-2026-0000000201",
            data_type="DOC",
            team="HE",
            group="CAE",
            year=2026,
            seq=201,
            title="원본 제목",
            summary="원본 요약",
            tags=["a"],
            agents=[],
            content={},
        )
        rec_b = Record(
            id="DOC-HE-CAE-2026-0000000202",
            data_type="DOC",
            team="HE",
            group="CAE",
            year=2026,
            seq=202,
            title="변경 제목",
            summary="변경 요약",
            tags=["a", "b"],
            agents=[],
            content={},
        )
        session.add_all([rec_a, rec_b])
        await session.commit()

    resp = await db_client.get(
        "/api/records/DOC-HE-CAE-2026-0000000202/diff",
        params={"from": "DOC-HE-CAE-2026-0000000201"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["from"] == "DOC-HE-CAE-2026-0000000201"
    assert body["to"] == "DOC-HE-CAE-2026-0000000202"
    meta = body["meta_changes"]
    assert "title" in meta
    assert meta["title"] == ["원본 제목", "변경 제목"]
    assert "tags" in meta


@pytest.mark.asyncio
async def test_diff_section_changes(db_client, test_session_maker) -> None:
    """섹션 ID 매칭으로 added/removed/modified 가 분류돼야 한다."""
    from api.db.models import Record, RecordSection

    async with test_session_maker() as session:
        rec_a = Record(
            id="DOC-HE-CAE-2026-0000000301",
            data_type="DOC",
            team="HE",
            group="CAE",
            year=2026,
            seq=301,
            title="A",
            summary="",
            tags=[],
            agents=[],
            content={},
        )
        rec_b = Record(
            id="DOC-HE-CAE-2026-0000000302",
            data_type="DOC",
            team="HE",
            group="CAE",
            year=2026,
            seq=302,
            title="B",
            summary="",
            tags=[],
            agents=[],
            content={},
        )
        session.add_all([rec_a, rec_b])
        await session.flush()
        session.add_all(
            [
                RecordSection(
                    record_id=rec_a.id,
                    section_id="1.1",
                    level=2,
                    title="원본",
                    content_text="alpha\nbeta\n",
                ),
                RecordSection(
                    record_id=rec_a.id,
                    section_id="1.2",
                    level=2,
                    title="삭제될 섹션",
                    content_text="gamma\n",
                ),
                RecordSection(
                    record_id=rec_b.id,
                    section_id="1.1",
                    level=2,
                    title="원본",
                    content_text="alpha\nBETA-CHANGED\n",
                ),
                RecordSection(
                    record_id=rec_b.id,
                    section_id="2.1",
                    level=2,
                    title="새 섹션",
                    content_text="delta\n",
                ),
            ]
        )
        await session.commit()

    resp = await db_client.get(
        "/api/records/DOC-HE-CAE-2026-0000000302/diff",
        params={"from": "DOC-HE-CAE-2026-0000000301"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_kind: dict[str, set[str]] = {"added": set(), "removed": set(), "modified": set()}
    for ch in body["section_changes"]:
        by_kind[ch["kind"]].add(ch["section_id"])
    assert "2.1" in by_kind["added"]
    assert "1.2" in by_kind["removed"]
    assert "1.1" in by_kind["modified"]


@pytest.mark.asyncio
async def test_diff_self_returns_400(db_client, seed_records) -> None:
    """동일 record_id 비교는 400 응답."""
    rid = seed_records["rec1"]
    resp = await db_client.get(f"/api/records/{rid}/diff", params={"from": rid})
    assert resp.status_code == 400
