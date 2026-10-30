# 추천 낱말 겹침이 요청 낱말 · 신호 없는 전문가를 올리지 않는지 — AD-19 회귀 시험
"""DW-13(10-30 디지털 워커 QA) — 'PCB 내부 크랙을 잘 아는 전문가 에이전트를 추천하고, 추천
근거가 된 기록 수를 알려줘.' 에 추천 10명 중 절반이 무관했다(로그 플랫폼 · MCP 레지스트리 ·
카메라 MIPI). 겹침 항은 '전문가 · 에이전트 · 추천 · 근거 · 기록' 같은 요청 낱말에도 점수를
줬고('전문가' 는 이름에 거의 다 들어 있다), 질의에 맞는 기록 · 표본이 하나도 없는 전문가도
낱말 한두 개만 겹치면 순위에 들었다.
"""
from __future__ import annotations

import pytest

_DW13 = "PCB 내부 크랙을 잘 아는 전문가 에이전트를 추천하고, 추천 근거가 된 기록 수를 알려줘."


def test_request_words_are_not_query_terms():
    from api.services.recommend_svc import _query_terms

    qt = _query_terms(_DW13)
    assert {"pcb", "내부", "크랙"} <= qt
    leaked = qt & {"전문", "전문가", "에이전트", "추천", "추천하고", "근거", "기록", "알려줘"}
    assert not leaked, f"요청 낱말이 겹침 항에 남았다 — {sorted(leaked)}"
    # 어미가 붙은 꼴(찾아줘 · 알려주세요)도 빠지고, 앞이 같은 분야 낱말(근거리 · 기록계)은 남는다.
    assert _query_terms("방수 실링 에이전트 찾아줘") == {"방수", "실링"}
    assert _query_terms("근거리 통신 모듈 전문가들") == {"근거리", "통신", "모듈"}
    assert _query_terms("기록계 교정 근거를 알려주세요") == {"기록계", "교정"}


@pytest.fixture
def _hash_env(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.delenv("AIDH_SEARCH_TOP_K_MAX", raising=False)


async def _seed(maker) -> None:
    from api.db.models import Agent, Record, RecordSection
    from api.services.embedding import get_embedder

    emb = get_embedder()

    def _agent(at: str, name: str, desc: str) -> Agent:
        return Agent(agent_type=at, name=name, description=desc, common_tags=[],
                     data_types=["DOC"])

    async with maker() as s:
        # 맞는 사람 — 카드가 있다(기록 신호).
        s.add(_agent("pcb-crack", "PCB 크랙 전문가", "PCB 내층 · 비아 크랙의 원인과 판정"))
        # 맞는 사람 — 카드는 없지만 이름 · 설명이 질의를 덮는다
        # (9b6ee6e 'PCB 휨' 의 길 — 그대로 올라야 한다).
        s.add(_agent("pcb-inner", "PCB 내부 크랙 전문가", "PCB 내부 크랙"))
        # 무관한 사람 — 카드도 표본도 없고, 요청 낱말 · 흔한 낱말로만 겹친다.
        s.add(_agent("xd-registry", "MCP 레지스트리 전문가",
                     "에이전트 등록 기록과 추천 근거를 관리한다"))
        s.add(_agent("sw-log", "로그 플랫폼 전문가", "기기 로그 기록 수집 · 보관과 근거 추적"))
        s.add(_agent("xd-tool", "툴 탐색 전문가", "잘 정리된 도구 목록"))
        for i in range(2):
            rid = f"DOC-HE-CAE-2026-00000007{i:02d}"
            s.add(Record(id=rid, data_type="DOC", team="HE", group="CAE", year=2026,
                         seq=700 + i, title=f"PCB 크랙 사례 {i}", summary="", tags=["PCB"],
                         agents=["pcb-crack"], content={"raw": "x"}))
            await s.flush()
            t = f"PCB 내층 크랙 비아 크랙 사례 {i}"
            s.add(RecordSection(record_id=rid, section_id="1", level=1, title=f"사례{i}",
                                content_text=t, embedding=emb.encode(t),
                                embedding_model=emb.name))
        await s.commit()


@pytest.mark.asyncio
async def test_dw13_like_query_ranks_no_irrelevant_zero_signal_expert(
    _hash_env, test_session_maker
):
    from api.services import recommend_svc

    await _seed(test_session_maker)
    async with test_session_maker() as s:
        ranked = await recommend_svc.recommend_agents(s, query=_DW13, top_k=10)
    ats = [r["agent_type"] for r in ranked]
    assert ats and ats[0] in {"pcb-crack", "pcb-inner"}, ats
    for bad in ("xd-registry", "sw-log", "xd-tool"):
        assert bad not in ats, f"신호 없는 무관한 전문가 {bad} 가 겹침만으로 들었다 — {ats}"
    assert "pcb-inner" in ats, "카드 없이도 이름 · 설명이 질의를 덮는 전문가가 빠졌다"
    crack = next(r for r in ranked if r["agent_type"] == "pcb-crack")
    assert crack["matched_records"] == 2


@pytest.mark.asyncio
async def test_weak_overlap_still_counts_when_the_expert_has_data(_hash_env, test_session_maker):
    """관문은 신호 0 인 전문가에게만 건다 — 카드가 걸린 전문가의 약한 겹침 가점은 종전대로."""
    from api.services import recommend_svc

    await _seed(test_session_maker)
    async with test_session_maker() as s:
        ranked = await recommend_svc.recommend_agents(s, query="비아 잘 아는 사람", top_k=10)
    crack = next(r for r in ranked if r["agent_type"] == "pcb-crack")
    # 질의어 {비아, 잘, 아는, 사람} 가운데 '비아' 하나가 겹친다 — 관문(0.40) 아래지만
    # 카드가 걸렸으니 붙는다.
    assert crack["desc_match"] == pytest.approx(1 / 4)
    # 같은 0.25 라도 신호 0 인 전문가('잘' 하나)는 오르지 않는다.
    ats = [r["agent_type"] for r in ranked]
    assert "xd-tool" not in ats, "'잘' 하나로 신호 없는 전문가가 들었다"
