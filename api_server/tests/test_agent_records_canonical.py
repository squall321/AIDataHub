# 전문가 기록 목록과 추천 · discover 의 기록 수가 같은 저장소를 보는지 — AD-20 회귀 시험
"""카드와 전문가를 잇는 저장소가 둘이다. ``records.agents`` 배열은 적재 · 자동 바인딩 ·
레코드 PATCH · 전문가 삭제가 모두 고치고 추천 · agent_search · discover 가 읽는다.
``agent_records`` 표는 적재만 채우고(자동 바인딩 · PATCH 는 안 채운다) 박스 병합은 그 행을
지우지 않는다. 예전 ``GET /api/agents/{key}/records`` 는 그 표로 목록을 내서 추천이 센 기록과
목록이 어긋났다 — 자동 바인딩 · PATCH 로 묶은 카드는 목록에 없고, 병합이 남긴 옛 짝과 지운
카드는 목록에만 있었다.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api.services import discover_svc

_AT = "seat-a"
_R_INGEST = "DOC-HE-CAE-2026-0000000801"   # 적재(write_record) — 두 저장소 다 쓴다
_R_BIND = "DOC-HE-CAE-2026-0000000802"     # 자동 바인딩(bind-matching) — records.agents 만
_R_STALE = "DOC-HE-CAE-2026-0000000803"    # 병합이 남긴 옛 짝 — agent_records 에만 있다
_R_DELETED = "DOC-HE-CAE-2026-0000000804"  # 지운 카드 — 두 저장소에 남아 있다
_R_PATCH = "DOC-HE-CAE-2026-0000000805"    # PATCH /api/records/{id} {agents} — records.agents 만


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.delenv("AIDH_SEARCH_TOP_K_MAX", raising=False)
    discover_svc.clear_cache()
    yield
    discover_svc.clear_cache()


def _card(rid: str, seq: int, title: str, tags: list[str], agents: list[str], **kw):
    from api.db.models import Record

    return Record(id=rid, data_type="DOC", team="HE", group="CAE", year=2026, seq=seq,
                  title=title, summary="", tags=tags, agents=agents, content={"raw": "x"}, **kw)


async def _seed(maker) -> None:
    from sqlalchemy import select

    from api.db.models import Agent, AgentRecord, OrgTeam, RecordSection
    from api.ingest.db_writer import write_record
    from api.ingest.normalizer import normalize
    from api.services import agent_draft_svc
    from api.services.embedding import get_embedder

    emb = get_embedder()
    async with maker() as s:
        s.add(OrgTeam(code="HE", name="HE"))
        s.add(Agent(agent_type=_AT, name="낙하 전문가", description="세트 낙하 충격",
                    common_tags=["낙하"], data_types=["DOC"]))
        s.add(Agent(agent_type="seat-b", name="열 전문가", description="열 해석",
                    common_tags=["열"], data_types=["DOC"]))
        await s.flush()
        # ① 적재 — write_record 가 records.agents 와 agent_records 를 함께 쓴다.
        await write_record(s, normalize({
            "id": _R_INGEST, "data_type": "DOC", "schema_version": "1.0", "title": "낙하 카드",
            "agents": [_AT], "tags": ["낙하"],
            "meta": {"doc_id": "HE-CAE-2026-0000000801", "title": "낙하 카드"},
            "sections": [{"id": "1", "level": 1, "title": "본문",
                          "blocks": [{"type": "paragraph", "text": "낙하 충격 코너 응력"}]}],
        }))
        # ② 자동 바인딩 대상 · ③ 병합이 남긴 옛 짝 · ④ 지운 카드 · ⑤ PATCH 로 묶을 카드
        s.add(_card(_R_BIND, 802, "낙하 시험 카드", ["낙하"], []))
        s.add(_card(_R_STALE, 803, "열 카드", ["열"], ["seat-b"]))
        s.add(_card(_R_DELETED, 804, "지운 낙하 카드", ["낙하"], [_AT],
                    deleted_at=datetime.now(UTC)))
        s.add(_card(_R_PATCH, 805, "코너 응력 카드", ["기타"], []))
        await s.flush()
        s.add(AgentRecord(agent_type=_AT, record_id=_R_STALE, priority=1))
        s.add(AgentRecord(agent_type=_AT, record_id=_R_DELETED, priority=1))
        for rid in (_R_BIND, _R_STALE, _R_DELETED, _R_PATCH):
            s.add(RecordSection(record_id=rid, section_id="1", level=1, title="본문",
                                content_text="낙하 충격 코너 응력"))
        await s.flush()
        # 추천이 찾을 수 있게 모든 절에 벡터를 단다(적재는 벡터를 뒤에 따로 채운다).
        for sec in (await s.execute(select(RecordSection))).scalars().all():
            sec.embedding = emb.encode(sec.content_text or "")
            sec.embedding_model = emb.name
        await s.commit()
    async with maker() as s:
        out = await agent_draft_svc.bind_matching_records(
            s, agent_type=_AT, required_doc_type=None, required_tags=[],
            common_tags=["낙하"], data_types=["DOC"])
    assert out["bound_record_ids"] == [_R_BIND], out


@pytest.mark.asyncio
async def test_list_and_counts_agree_across_write_paths(db_client, test_session_maker):
    from api.services import recommend_svc

    await _seed(test_session_maker)
    resp = await db_client.patch(f"/api/records/{_R_PATCH}", json={"agents": [_AT]})
    assert resp.status_code == 200, resp.text

    resp = await db_client.get(f"/api/agents/{_AT}/records")
    assert resp.status_code == 200, resp.text
    listed = {r["id"] for r in resp.json()}
    assert listed == {_R_INGEST, _R_BIND, _R_PATCH}, (
        f"목록이 정본(records.agents · 지운 것 뺌)과 다르다 — {sorted(listed)}")

    tax = (await db_client.get("/api/taxonomy/agents")).json()
    assert next(i for i in tax["items"] if i["agent_type"] == _AT)["record_count"] == len(listed)
    disc = (await db_client.get("/api/discover")).json()
    assert next(a for a in disc["agents"] if a["agent_type"] == _AT)["record_count"] == len(listed)

    # 추천의 기록 수 — 후보 절(기본 50)이 이 좌석의 카드를 모두 덮으면 목록 길이와 같아야 한다.
    async with test_session_maker() as s:
        ranked = await recommend_svc.recommend_agents(s, query="낙하 충격 코너 응력", top_k=5)
    seat = next(r for r in ranked if r["agent_type"] == _AT)
    assert seat["matched_records"] == len(listed)
