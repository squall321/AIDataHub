"""Record 계보 (lineage) 엔드포인트 테스트 (Migration 0008 / G3)."""
from __future__ import annotations

import pytest


async def _seed_chain(session, mk):
    """A → B → C → D 의 4-세대 체인을 생성한다."""
    from api.db.models import Record

    parents = [None, "DOC-HE-CAE-2026-000401",
               "DOC-HE-CAE-2026-000402",
               "DOC-HE-CAE-2026-000403"]
    ids = [
        "DOC-HE-CAE-2026-000401",
        "DOC-HE-CAE-2026-000402",
        "DOC-HE-CAE-2026-000403",
        "DOC-HE-CAE-2026-000404",
    ]
    for idx, (rid, parent) in enumerate(zip(ids, parents)):
        session.add(
            Record(
                id=rid,
                data_type="DOC",
                division="HE",
                team="CAE",
                year=2026,
                seq=401 + idx,
                title=f"Generation {idx}",
                summary="",
                tags=[],
                agents=[],
                content={"gen": idx},
                parent_record_id=parent,
                derivation="extracted" if parent else "original",
                version=f"{idx + 1}.0",
            )
        )
    await session.commit()
    return ids


@pytest.mark.asyncio
async def test_lineage_returns_ancestors(
    db_client, test_session_maker
) -> None:
    """체인의 끝(D)에서 lineage 호출 시 조상 3개가 차례대로 잡혀야 한다."""
    async with test_session_maker() as session:
        ids = await _seed_chain(session, test_session_maker)

    resp = await db_client.get(f"/api/records/{ids[3]}/lineage")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record_id"] == ids[3]
    ancestor_ids = [n["id"] for n in body["ancestors"]]
    # parent 부터 root 순서.
    assert ancestor_ids == [ids[2], ids[1], ids[0]]
    assert body["ancestor_count"] == 3
    assert body["descendant_count"] == 0


@pytest.mark.asyncio
async def test_lineage_returns_descendants(
    db_client, test_session_maker
) -> None:
    """체인의 시작(A)에서 lineage 호출 시 자손 3개가 모두 잡혀야 한다."""
    async with test_session_maker() as session:
        ids = await _seed_chain(session, test_session_maker)

    resp = await db_client.get(f"/api/records/{ids[0]}/lineage")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    desc_ids = {n["id"] for n in body["descendants"]}
    assert desc_ids == {ids[1], ids[2], ids[3]}
    assert body["ancestor_count"] == 0
    assert body["descendant_count"] == 3


@pytest.mark.asyncio
async def test_lineage_404_for_missing(db_client) -> None:
    resp = await db_client.get("/api/records/DOES-NOT-EXIST/lineage")
    assert resp.status_code == 404
