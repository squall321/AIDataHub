# 본문이 같아도 제목·요약이 바뀌면 갱신하는지 — 요약 폴백이 영영 반영되지 않던 것의 회귀 시험
"""write_record 는 본문(content) 해시만 보고 같으면 skipped 로 끝냈다. 그래서 동기화가 요약 폴백
(sync_svc summary_from_body)을 더해도 본문이 그대로인 카드(MX 백서 namu 위키 171장 등)는 빈 요약
그대로 남았다. 제목·요약까지 같을 때만 건너뛴다.
"""
from __future__ import annotations

import pytest

_RID = "DOC-HE-CAE-2026-0000000401"


async def _seed_team(session) -> None:
    from api.db.models import OrgTeam

    session.add(OrgTeam(code="HE", name="HE"))
    await session.flush()


def _rec(*, title: str = "제목", summary: str = ""):
    from api.schemas import RecordIn

    sec = {"section_id": "1", "level": 1, "title": "본문", "content_text": "같은 본문"}
    return RecordIn(id=_RID, data_type="DOC", title=title, summary=summary,
                    content={"sections": [sec]})


@pytest.mark.asyncio
async def test_summary_only_change_updates(test_session):
    from api.db.models import Record
    from api.ingest.db_writer import write_record

    await _seed_team(test_session)
    assert (await write_record(test_session, _rec())).action == "inserted"
    r = await write_record(test_session, _rec(summary="본문 앞부분으로 채운 요약"))
    assert r.action == "updated"
    assert (await test_session.get(Record, _RID)).summary == "본문 앞부분으로 채운 요약"
    # 같은 입력을 다시 넣으면 그대로 건너뛴다.
    again = await write_record(test_session, _rec(summary="본문 앞부분으로 채운 요약"))
    assert again.action == "skipped"


@pytest.mark.asyncio
async def test_title_only_change_updates(test_session):
    from api.db.models import Record
    from api.ingest.db_writer import write_record

    await _seed_team(test_session)
    await write_record(test_session, _rec())
    r = await write_record(test_session, _rec(title="바뀐 제목"))
    assert r.action == "updated"
    assert (await test_session.get(Record, _RID)).title == "바뀐 제목"

