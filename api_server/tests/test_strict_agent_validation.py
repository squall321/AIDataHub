# 적재가 등록 안 된 전문가 키를 422 로 거절하는지(strict_agent_validation) — 빈 전문가 자동 생성의 회귀 시험
"""records.agents 에 agents 테이블에 없는 키가 있으면 예전에는 경고 한 줄만 남기고 진행했고,
_resync_agents 가 이름=키·설명 0자의 빈 행을 만들었다. 그 행이 운영 전문가 목록에 두세 달 떠 있었다
(dynaforge-report·kooremapper-modelmeta, 2026-10-14 전수 점검). 팀 검증(strict_team_validation)과
같은 모양으로 막는다 — 스위치는 기본 켬, 되돌리기는 STRICT_AGENT_VALIDATION=false.
"""
from __future__ import annotations

import pytest

_RID = "DOC-HE-CAE-2026-0000000301"


async def _seed_team(session) -> None:
    from api.db.models import OrgTeam

    session.add(OrgTeam(code="HE", name="HE"))
    await session.flush()


def _record_in(agents: list[str]):
    from api.ingest.normalizer import normalize

    return normalize({
        "id": _RID, "data_type": "DOC", "schema_version": "1.0", "title": "strict", "agents": agents,
        "meta": {"doc_id": "HE-CAE-2026-0000000301", "title": "strict"},
        "sections": [{"id": "1", "level": 1, "title": "본문",
                      "blocks": [{"type": "paragraph", "text": "본문"}]}],
    })


@pytest.mark.asyncio
async def test_unregistered_agent_is_rejected_with_422(test_session):
    from fastapi import HTTPException

    from api.db.models import Agent, Record
    from api.ingest.db_writer import write_record

    await _seed_team(test_session)
    with pytest.raises(HTTPException) as ei:
        await write_record(test_session, _record_in(["no-such-expert"]))
    assert ei.value.status_code == 422
    assert "no-such-expert" in str(ei.value.detail) and _RID in str(ei.value.detail)
    # 거절이면 아무것도 남기지 않는다 — 빈 전문가 행도, 레코드도.
    assert await test_session.get(Agent, "no-such-expert") is None
    assert await test_session.get(Record, _RID) is None


@pytest.mark.asyncio
async def test_registered_agent_passes(test_session):
    from sqlalchemy import select

    from api.db.models import Agent, AgentRecord
    from api.ingest.db_writer import write_record

    await _seed_team(test_session)
    test_session.add(Agent(agent_type="sim-drop-impact", name="낙하", description="세트 낙하",
                           common_tags=[], data_types=["DOC"]))
    await test_session.flush()
    result = await write_record(test_session, _record_in(["sim-drop-impact"]))
    assert result.action == "inserted"
    links = (await test_session.execute(
        select(AgentRecord.agent_type).where(AgentRecord.record_id == _RID))).scalars().all()
    assert links == ["sim-drop-impact"]


@pytest.mark.asyncio
async def test_switch_off_keeps_the_old_stub_behaviour(test_session, monkeypatch):
    """되돌리면(STRICT_AGENT_VALIDATION=false) 예전처럼 경고만 남기고 빈 행을 만든다."""
    from api.config import settings
    from api.db.models import Agent
    from api.ingest.db_writer import write_record

    monkeypatch.setattr(settings, "strict_agent_validation", False)
    await _seed_team(test_session)
    result = await write_record(test_session, _record_in(["no-such-expert"]))
    assert result.action == "inserted"
    stub = await test_session.get(Agent, "no-such-expert")
    assert stub is not None and stub.name == "no-such-expert" and stub.description == ""


def test_switch_defaults_on_and_env_turns_it_off(monkeypatch):
    from api.config import Settings

    assert Settings.model_fields["strict_agent_validation"].default is True
    monkeypatch.setenv("STRICT_AGENT_VALIDATION", "false")
    assert Settings(_env_file=None).strict_agent_validation is False
