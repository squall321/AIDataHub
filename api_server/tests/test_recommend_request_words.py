# 추천 낱말 겹침이 요청 낱말 · 신호 없는 전문가를 올리지 않는지 — AD-19 회귀 시험
"""DW-13(10-30 디지털 워커 QA) — 'PCB 내부 크랙을 잘 아는 전문가 에이전트를 추천하고, 추천
근거가 된 기록 수를 알려줘.' 에 추천 10명 중 절반이 무관했다(로그 플랫폼 · MCP 레지스트리 ·
카메라 MIPI). 겹침 항은 '전문가 · 에이전트 · 추천 · 근거 · 기록' 같은 요청 낱말에도 점수를
줬고('전문가' 는 이름에 거의 다 들어 있다), 질의에 맞는 기록 · 표본이 하나도 없는 전문가도
낱말 한두 개만 겹치면 점수대로 순위에 끼었다.

그런 전문가는 목록에서 빼지 않고 근거가 있는 전문가 뒤에 둔다. 빼면 말투가 길 때(기능어가
질의어 분모를 키울 때) 이름이 질의와 같은 전문가까지 사라진다(10-30 반대 검토).
"""
from __future__ import annotations

import pytest

_DW13 = "PCB 내부 크랙을 잘 아는 전문가 에이전트를 추천하고, 추천 근거가 된 기록 수를 알려줘."
# 같은 물음을 길게 — 기능어('누구인지' · '싶은데' · '있나요?')가 질의어 분모를 키운다.
_CHATTY = "PCB 내부 크랙을 잘 아는 전문가가 누구인지 알고 싶은데 추천해 줄 수 있나요?"
_SHORT = "PCB 크랙 전문 에이전트 추천"


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
    # '전문가' 의 가까운 꼴('전문' · '전문으로' · '전문의')과 영어 이름 끝(Expert · Specialist ·
    # Agent)도 빠진다. 앞이 같은 분야 낱말('전문검색')은 남는다.
    assert _query_terms(_SHORT) == {"pcb", "크랙"}
    assert _query_terms("크랙 전문으로 보는 전문의") == {"크랙", "보는"}
    assert _query_terms("PCB crack experts and agent specialist") == {"pcb", "crack"}
    assert _query_terms("전문검색 엔진 전문가") == {"전문검색", "엔진"}


@pytest.fixture
def _hash_env(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.delenv("AIDH_SEARCH_TOP_K_MAX", raising=False)
    monkeypatch.delenv("AIDH_RERANK_PROVIDER", raising=False)


async def _seed(maker, *, cards_for: tuple[str, ...] = ()) -> None:
    """``cards_for`` 의 전문가에게는 질의와 무관한 카드를 한 장씩 단다(의미검색 후보에 걸린 꼴)."""
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
        # (9b6ee6e 'PCB 휨' 의 길 — 점수대로 서야 한다).
        s.add(_agent("pcb-inner", "PCB 내부 크랙 전문가", "PCB 내부 크랙"))
        # 무관한 사람 — 요청 낱말로만 겹친다.
        s.add(_agent("xd-registry", "MCP 레지스트리 전문가",
                     "에이전트 등록 기록과 추천 근거를 관리한다"))
        s.add(_agent("sw-log", "로그 플랫폼 전문가", "기기 로그 기록 수집 · 보관과 근거 추적"))
        # 무관한 사람 — 흔한 낱말('잘' · '된')이나 분야 낱말 일부('PCB' · '내부')만 겹친다.
        s.add(_agent("xd-tool", "툴 탐색 전문가", "잘 정리된 도구 목록"))
        s.add(_agent("cam-mipi", "카메라 MIPI PHY 전문가", "카메라 모듈 PCB 내부 배선과 MIPI 신호"))
        # 질의와 낱말은 하나도 안 겹치지만 카드가 후보에 걸린 사람(근거 신호만 있다).
        s.add(_agent("rel-solder", "솔더 신뢰성 담당", "솔더 접합 피로 수명"))
        cards = [("pcb-crack", f"PCB 내층 크랙 비아 크랙 사례 {i}") for i in range(2)]
        cards.append(("rel-solder", "솔더 접합 피로 수명 시험"))
        cards += [(at, f"{at} 운영 메모") for at in cards_for]
        for i, (at, t) in enumerate(cards):
            rid = f"DOC-HE-CAE-2026-00000007{i:02d}"
            s.add(Record(id=rid, data_type="DOC", team="HE", group="CAE", year=2026,
                         seq=700 + i, title=t, summary="", tags=[], agents=[at],
                         content={"raw": "x"}))
            await s.flush()
            s.add(RecordSection(record_id=rid, section_id="1", level=1, title=t,
                                content_text=t, embedding=emb.encode(t),
                                embedding_model=emb.name))
        await s.commit()


async def _rank(maker, q: str) -> list[dict]:
    from api.services import recommend_svc

    async with maker() as s:
        return await recommend_svc.recommend_agents(s, query=q, top_k=10)


@pytest.mark.asyncio
async def test_dw13_like_query_ranks_no_irrelevant_zero_signal_expert(
    _hash_env, test_session_maker
):
    await _seed(test_session_maker)
    ranked = await _rank(test_session_maker, _DW13)
    ats = [r["agent_type"] for r in ranked]
    assert ats and ats[0] in {"pcb-crack", "pcb-inner"}, ats
    for bad in ("xd-registry", "sw-log"):
        assert bad not in ats, f"요청 낱말로만 겹치던 {bad} 가 남았다 — {ats}"
    # 신호 없이 낱말만 약하게 겹친 전문가(2/7)는 빼지 않고, 점수가 더 높아도 근거 있는
    # 전문가(rel-solder) 뒤에 둔다.
    assert set(ats[-2:]) == {"xd-tool", "cam-mipi"}, ats
    assert ats.index("rel-solder") < ats.index("xd-tool")
    assert "pcb-inner" in ats[:3], f"카드 없이도 이름 · 설명이 질의를 덮는 전문가가 밀렸다 — {ats}"
    crack = next(r for r in ranked if r["agent_type"] == "pcb-crack")
    assert crack["matched_records"] == 2


@pytest.mark.asyncio
async def test_chatty_phrasing_keeps_the_matching_expert_listed(_hash_env, test_session_maker):
    """기능어가 분모를 키워 비율이 관문(0.40) 밑으로 내려가도 이름이 질의와 같은 전문가는
    목록에 남는다 — 신호 없는 약한 일치 가운데 맨 앞이다(심의 후보 · 띵킹 예심이 고른다)."""
    await _seed(test_session_maker)
    ranked = await _rank(test_session_maker, _CHATTY)
    ats = [r["agent_type"] for r in ranked]
    assert "pcb-inner" in ats, f"이름이 질의와 같은 전문가가 목록에서 빠졌다 — {ats}"
    inner = next(r for r in ranked if r["agent_type"] == "pcb-inner")
    assert inner["desc_match"] == pytest.approx(3 / 11, abs=1e-4)  # 응답은 넷째 자리로 반올림
    assert ats.index("pcb-crack") < ats.index("pcb-inner") < ats.index("cam-mipi")


@pytest.mark.asyncio
async def test_one_shared_word_does_not_outrank_an_expert_with_data(
    _hash_env, test_session_maker
):
    """질의어 둘 중 하나('PCB')만 겹친 신호 0 전문가는 비율이 0.5 라도 강한 일치가 아니다."""
    await _seed(test_session_maker)
    ranked = await _rank(test_session_maker, _SHORT)
    ats = [r["agent_type"] for r in ranked]
    cam = next(r for r in ranked if r["agent_type"] == "cam-mipi")
    assert cam["desc_match"] == pytest.approx(0.5)
    assert ats.index("rel-solder") < ats.index("cam-mipi"), ats
    # 두 낱말이 다 겹치면 카드가 없어도 점수대로 선다(9b6ee6e).
    assert ats.index("pcb-inner") < ats.index("rel-solder"), ats
    # 질의어가 하나뿐이면 그 하나가 겹친 것으로 강한 일치다.
    ats = [r["agent_type"] for r in await _rank(test_session_maker, "크랙")]
    assert ats.index("pcb-inner") < ats.index("rel-solder"), ats


@pytest.mark.asyncio
async def test_request_words_do_not_lift_an_expert_with_data(_hash_env, test_session_maker):
    """요청 낱말로만 겹치던 전문가가 무관한 카드 한 장으로 후보에 걸려도(의미검색 경로)
    겹침 가점은 없다 — 맞는 두 사람 뒤에 선다."""
    await _seed(test_session_maker, cards_for=("xd-registry", "sw-log"))
    ranked = await _rank(test_session_maker, _DW13)
    ats = [r["agent_type"] for r in ranked]
    for at in ("xd-registry", "sw-log"):
        r = next(r for r in ranked if r["agent_type"] == at)
        assert r["desc_match"] == 0.0 and r["matched_records"] == 1
        assert ats.index(at) > max(ats.index("pcb-crack"), ats.index("pcb-inner")), ats


@pytest.mark.asyncio
async def test_weak_overlap_still_counts_when_the_expert_has_data(_hash_env, test_session_maker):
    """뒤로 미루는 것은 신호 0 인 전문가뿐 — 카드가 걸린 전문가의 약한 겹침 가점은 종전대로."""
    await _seed(test_session_maker)
    ranked = await _rank(test_session_maker, "비아 잘 아는 사람")
    crack = next(r for r in ranked if r["agent_type"] == "pcb-crack")
    # 질의어 {비아, 잘, 아는, 사람} 가운데 '비아' 하나가 겹친다 — 관문(0.40) 아래지만
    # 카드가 걸렸으니 점수대로 선다.
    assert crack["desc_match"] == pytest.approx(1 / 4)
    # 같은 0.25 라도 신호 0 인 전문가('잘' 하나)는 근거 있는 전문가 뒤다.
    ats = [r["agent_type"] for r in ranked]
    assert "xd-tool" in ats, f"신호 없는 약한 일치가 목록에서 빠졌다 — {ats}"
    assert ats[-1] == "xd-tool", f"'잘' 하나로 신호 없는 전문가가 앞에 섰다 — {ats}"


@pytest.mark.asyncio
async def test_sample_hit_counts_as_signal(_hash_env, test_session_maker):
    """표본(sample_queries)만 걸린 전문가도 신호가 있다 — 약한 겹침이어도 뒤로 미루지 않는다."""
    from api.db.models import Agent, AgentSampleEmbedding
    from api.services.embedding import get_embedder

    q = "비아 잘 아는 사람"
    await _seed(test_session_maker)
    async with test_session_maker() as s:
        s.add(Agent(agent_type="smp-via", name="비아 도금 담당", description="비아 도금 균열",
                    common_tags=[], data_types=["DOC"]))
        await s.flush()
        s.add(AgentSampleEmbedding(agent_type="smp-via", sample_text=q,
                                   embedding=get_embedder().encode(q)))
        await s.commit()
    ranked = await _rank(test_session_maker, q)
    smp = ranked[0]
    assert smp["agent_type"] == "smp-via", [r["agent_type"] for r in ranked]
    assert smp["matched_records"] == 0 and smp["matched_samples"] == 1
    assert smp["desc_match"] == pytest.approx(1 / 4)
