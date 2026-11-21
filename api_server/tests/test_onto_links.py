# 카드 ↔ 온톨로지 객체 연결의 시험 — 카드 구조에서 낱말 뽑기 · 허브 사전으로 잇기 · 전문가 프로필 · 추천의 onto 인자
from __future__ import annotations

import json

import pytest

from api.services import onto_svc

_RULES = {
    "version": 1, "max_ngram": 8, "min_hangul": 2, "min_latin": 3, "keywords": True,
    "properties": ["material_card"],
    "card_types": {"expert_knowledge_card": {"type": {"failure-case": ["failure_mode"]},
                                             "tier": {"failure_mode": ["failure_mode"]}},
                   "material_card": {"always": ["material_property"]}},
    "stop": ["faq", "checklist"],
}
_HUB = "rootdesigndatahub"


def _rec(rid: str, seq: int, **kw):
    from api.db.models import Record

    base = dict(id=rid, data_type="DOC", team="HE", group="CAE", year=2026, seq=seq, title=rid, summary="",
                tags=[], agents=[], subject_keywords=[], content={"raw": "x"})
    base.update(kw)
    return Record(**base)


async def _seed(maker) -> None:
    from api.db.models import Agent, RecordSection

    async with maker() as s:
        for at, name in (("rel-solder", "솔더 접합 신뢰성"), ("rel-drop", "낙하 충격"), ("mat-polymer", "고분자 물성")):
            s.add(Agent(agent_type=at, name=name, description=name, common_tags=[], data_types=["DOC"]))
        s.add(_rec("C-1", 1, doc_type="expert_knowledge_card", title="리플로 뒤 접합부가 갈라짐", agents=["rel-solder"],
                   subject_keywords=["솔더 크랙", "faq", "BGA"],
                   content={"card": {"type": "failure-case", "tier": "failure_mode"}}))
        s.add(_rec("C-2", 2, doc_type="expert_knowledge_card", title="언더필 선정 규칙", agents=["rel-solder"],
                   subject_keywords=["언더필"], content={"card": {"type": "design-rule", "tier": "checklist"}}))
        s.add(_rec("C-3", 3, doc_type="expert_knowledge_card", title="코너 낙하에서 미세균열", agents=["rel-drop"],
                   subject_keywords=["낙하"], content={"card": {"type": "concept", "tier": "deep"}}))
        s.add(_rec("M-1", 4, doc_type="material_card", title="PA6-GF50", agents=["mat-polymer"],
                   content={"properties": [{"key": "mechanical.youngs_modulus", "name": "탄성계수"}, "깨진 줄"]}))
        s.add(_rec("P-1", 5, doc_type="paper", title="Solder crack growth under thermal cycling", agents=["rel-solder"],
                   summary="BGA 접합부의 솔더 크랙을 다룬다", subject_keywords=["thermal cycling"]))
        s.add(_rec("X-1", 6, doc_type="paper", title="지워진 카드 솔더 크랙", agents=["rel-drop"],
                   subject_keywords=["솔더 크랙"]))
        await s.flush()
        s.add(RecordSection(record_id="C-2", section_id="1", level=1, title="규칙",
                            content_text="언더필이 없으면 솔더 크랙이 빨리 난다. PCBs 는 세지 않는다."))
        s.add(RecordSection(record_id="C-3", section_id="1", level=1, title="정의",
                            content_text="코너 낙하에서는 미세균열이 먼저 생긴다."))
        await s.commit()
    async with maker() as s:
        from datetime import datetime, timezone

        from api.db.models import Record

        (await s.get(Record, "X-1")).deleted_at = datetime.now(timezone.utc)
        await s.commit()


_DICT = [
    {"type_slug": "failure_mode", "key": "FM-SOLDER-CRACK", "term": "솔더 크랙"},
    {"type_slug": "failure_mode", "key": "FM-SOLDER-CRACK", "term": "Solder Crack"},
    {"type_slug": "failure_mode", "key": "FM-CRACK", "term": "균열"},
    {"type_slug": "material_property", "key": "MP-E", "term": "탄성계수"},
    {"type_slug": "part", "key": "PT-PCB", "term": "PCB"},
    {"type_slug": "part", "key": "PT-BGA", "term": "BGA"},
    {"type_slug": "part", "key": "PT-BGA-2", "term": "bga"},          # 한 타입 안에서 같은 낱말이 키 둘 — 버린다
    {"type_slug": "part", "key": "PT-X", "term": "ab"},               # 영문 두 글자 — 버린다
    {"type_slug": "part", "key": "PT-FAQ", "term": "FAQ"},            # 뺄 낱말
]


# ── 낱말 · 규칙 ───────────────────────────────────────────────────────────
def test_tokens_and_particles():
    assert onto_svc.tokens("Solder-Crack(솔더 크랙)이") == ["solder", "crack", "솔더", "크랙", "이"]
    assert onto_svc.strip_particle("균열이") == "균열"
    assert onto_svc.strip_particle("휨이") == "휨이"                # 떼면 한 글자가 남는다 — 그대로 둔다
    # '가속도' 는 조사를 떼면 '가속' 이 되지만 맞추기는 원래 꼴도 함께 본다(탐침 EO-5 와 같다)
    lex, _ = onto_svc.build_lexicon([{"type_slug": "t", "key": "K", "term": "가속도"}], _RULES)
    assert lex.match("가속도 센서") == {("가속도",): 1}
    assert onto_svc.strip_particle("pcb") == "pcb"
    assert not onto_svc.term_ok(("ab",)) and onto_svc.term_ok(("pcb",)) and onto_svc.term_ok(("균열",))
    assert not onto_svc.term_ok(("123",)) and not onto_svc.term_ok(("휨",))


def test_extract_is_deterministic_and_typed_only_where_rules_say():
    card = dict(doc_type="expert_knowledge_card", keywords=["솔더 크랙", "faq", "BGA", "솔더  크랙"],
                card={"type": "failure-case", "tier": "failure_mode"})
    rows = onto_svc.extract(_RULES, **card)
    assert rows == onto_svc.extract(_RULES, **card)
    assert rows == [("card", "failure_mode", "", ""), ("keyword", "", "솔더 크랙", "솔더 크랙"), ("keyword", "", "bga", "BGA")]
    # 규칙에 없는 카드 종류는 타입 줄이 없다 — 짐작으로 붙이지 않는다
    assert onto_svc.extract(_RULES, doc_type="expert_knowledge_card", keywords=["언더필"],
                            card={"type": "design-rule", "tier": "checklist"}) == [("keyword", "", "언더필", "언더필")]
    mat = onto_svc.extract(_RULES, doc_type="material_card",
                           properties=[{"key": "mechanical.youngs_modulus", "name": "탄성계수"}, "깨진 줄", {"key": 3}])
    assert ("card", "material_property", "", "") in mat and ("property", "", "youngs modulus", "youngs_modulus") in mat
    # 물성 줄은 규칙이 적은 문서 종류에서만 낱말이 된다
    assert onto_svc.extract(_RULES, doc_type="paper", properties=[{"name": "탄성계수"}]) == []


def test_rules_file_and_overlay(tmp_path):
    base = tmp_path / "onto_rules.json"
    base.write_text(json.dumps({**_RULES, "_doc": "x"}), encoding="utf-8")
    r0 = onto_svc.load_rules(base)
    assert r0["_overlay"] is False and "_doc" not in r0
    (tmp_path / "onto_rules.local.json").write_text(json.dumps(
        {"card_types": {"expert_knowledge_card": {"type": {"design-rule": ["criterion"]}}}, "stop": None}), encoding="utf-8")
    r1 = onto_svc.load_rules(base)
    assert r1["_overlay"] is True and "stop" not in r1
    assert onto_svc.card_types(r1, "expert_knowledge_card", {"type": "design-rule"}) == ["criterion"]
    assert onto_svc.card_types(r1, "expert_knowledge_card", {"type": "failure-case"}) == ["failure_mode"]
    assert onto_svc.rules_fingerprint(r0) != onto_svc.rules_fingerprint(r1)
    (tmp_path / "onto_rules.local.json").write_text("{깨짐", encoding="utf-8")
    assert onto_svc.load_rules(base)["_overlay"] is False            # 깨진 덧씌우기는 버리고 기본으로


def test_tracked_rules_default_maps_only_the_two_clear_types():
    rules = onto_svc.load_rules(onto_svc.RULES_PATH.parent / "onto_rules.json")
    if rules["_overlay"]:
        pytest.skip("이 박스에 덧씌우기가 있다")
    assert onto_svc.card_types(rules, "expert_knowledge_card", {"type": "failure-case", "tier": "failure_mode"}) == ["failure_mode"]
    assert onto_svc.card_types(rules, "material_card", None) == ["material_property"]
    for t in ("concept", "design-rule", "faq", "data", "standard-summary"):
        assert onto_svc.card_types(rules, "expert_knowledge_card", {"type": t, "tier": "deep"}) == []


def test_lexicon_drops_ambiguous_short_and_stop_terms():
    lex, counts = onto_svc.build_lexicon(_DICT + ["줄이 아니다", {"type_slug": "part", "key": "", "term": "x"}], _RULES)
    assert counts["terms"] == 5 and counts["ambiguous"] == 1 and counts["short"] == 1 and counts["stop"] == 1
    assert counts["bad"] == 2 and counts["objects"] == {"failure_mode": 2, "material_property": 1, "part": 1}
    assert ("bga",) not in lex.terms
    found = lex.match("솔더 크랙이 PCBs 가 아니라 PCB에서, 미세균열과 균열을 본다 solder   crack")
    assert found == {("솔더", "크랙"): 1, ("pcb",): 1, ("균열",): 1, ("solder", "crack"): 1}


# ── 짓기 ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_build_mentions_then_skip_until_something_changes(test_session_maker):
    from sqlalchemy import select

    from api.db.models import Record, RecordMention

    await _seed(test_session_maker)
    async with test_session_maker() as s:
        out = await onto_svc.build_mentions(s, rules=_RULES)
        await s.commit()
    assert out["skipped"] is False and out["by_type"] == {"failure_mode": 1, "material_property": 1}
    assert out["by_source"] == {"card": 2, "keyword": 5, "property": 3}
    async with test_session_maker() as s:
        rows = sorted((await s.execute(select(RecordMention.record_id, RecordMention.source, RecordMention.type_slug,
                                              RecordMention.norm))).all())
    assert ("C-1", "card", "failure_mode", "") in rows and ("C-1", "keyword", "", "솔더 크랙") in rows
    assert ("C-1", "keyword", "", "faq") not in rows                 # 뺄 낱말
    assert not [r for r in rows if r[0] == "X-1"]                    # 지워진 카드
    assert ("M-1", "property", "", "탄성계수") in rows
    async with test_session_maker() as s:
        again = await onto_svc.build_mentions(s, rules=_RULES)
        assert again["skipped"] is True
        forced = await onto_svc.build_mentions(s, rules=_RULES, force=True)
        assert forced["skipped"] is False and forced["rows"] == out["rows"]
        changed = await onto_svc.build_mentions(s, rules={**_RULES, "stop": []})
        assert changed["skipped"] is False and changed["rows"] == out["rows"] + 1      # faq 가 돌아온다
        await s.commit()
    async with test_session_maker() as s:
        s.add(_rec("C-9", 9, doc_type="paper", subject_keywords=["새 열쇠말"]))
        await s.commit()
        more = await onto_svc.build_mentions(s, rules={**_RULES, "stop": []})
        assert more["skipped"] is False and more["rows"] == changed["rows"] + 1
        assert (await s.execute(select(Record.id).where(Record.id == "C-9"))).scalar() == "C-9"


# ── 잇기 ─────────────────────────────────────────────────────────────────
async def _bind(maker, entries=_DICT, **kw):
    async with maker() as s:
        await onto_svc.build_mentions(s, rules=_RULES)
        out = await onto_svc.bind_entities(s, hub=_HUB, entries=entries, rules=_RULES, **kw)
        await s.commit()
    return out


async def _links(maker):
    from sqlalchemy import select

    from api.db.models import RecordEntity

    async with maker() as s:
        return {(r, t, k): (w, st, h) for r, t, k, w, st, h in (await s.execute(select(
            RecordEntity.record_id, RecordEntity.type_slug, RecordEntity.key, RecordEntity.found_in, RecordEntity.strong,
            RecordEntity.hits))).all()}


@pytest.mark.asyncio
async def test_bind_links_exact_terms_only_and_marks_where(test_session_maker):
    await _seed(test_session_maker)
    out = await _bind(test_session_maker, dict_version="v1")
    links = await _links(test_session_maker)
    # 열쇠말 = 굵은 연결
    assert links[("C-1", "failure_mode", "FM-SOLDER-CRACK")] == ("keyword", True, 1)
    # 절 글 = 본문 연결. 'PCBs' 는 'PCB' 가 아니다(낱말 단위)
    assert links[("C-2", "failure_mode", "FM-SOLDER-CRACK")] == ("section", False, 1)
    assert ("C-2", "part", "PT-PCB") not in links
    # '미세균열' 안의 '균열' 은 맞지 않는다 — 제목과 절 어디서도
    assert not [k for k in links if k[0] == "C-3"]
    # 물성 줄 = 굵은 연결
    assert links[("M-1", "material_property", "MP-E")] == ("property", True, 1)
    # 논문 — 제목(영문 별칭)과 요약 둘에서 맞으면 가장 굵은 자리 · 횟수 합
    assert links[("P-1", "failure_mode", "FM-SOLDER-CRACK")] == ("title", True, 2)
    # 한 타입 안에서 겹친 낱말(BGA)은 잇지 않는다 · 지워진 카드는 잇지 않는다
    assert not [k for k in links if k[2].startswith("PT-BGA") or k[0] == "X-1"]
    assert out["written"] is True and out["links"] == len(links) == 4 and out["dict_version"] == "v1"
    assert out["records_linked"] == 4 and out["links_by_where"] == {"keyword": 1, "property": 1, "section": 1, "title": 1}
    assert out["records_by_doc_type"]["expert_knowledge_card"] == {"linked": 2, "total": 3}
    assert out["objects_linked"] == {"failure_mode": 1, "material_property": 1}
    # 결과에 카드 글 · 객체 이름이 없다(셈만)
    assert "솔더" not in json.dumps(out, ensure_ascii=False) and "FM-SOLDER" not in json.dumps(out)


@pytest.mark.asyncio
async def test_bind_is_repeatable_replaces_and_keeps_links_when_dictionary_is_empty(test_session_maker):
    await _seed(test_session_maker)
    await _bind(test_session_maker)
    first = await _links(test_session_maker)
    await _bind(test_session_maker)
    assert await _links(test_session_maker) == first                 # 같은 사전 · 같은 카드면 같은 연결
    empty = await _bind(test_session_maker, entries=[{"type_slug": "part", "key": "PT-X", "term": "ab"}])
    assert empty["written"] is False and await _links(test_session_maker) == first      # 허브에 못 닿은 날 지우지 않는다
    await _bind(test_session_maker, entries=[{"type_slug": "material_property", "key": "MP-E", "term": "탄성계수"}])
    assert set(await _links(test_session_maker)) == {("M-1", "material_property", "MP-E")}      # 통째로 다시 쓴다
    # 다른 허브의 연결은 건드리지 않는다
    async with test_session_maker() as s:
        await onto_svc.bind_entities(s, hub="failuretrace", entries=[_DICT[0]], rules=_RULES)
        await s.commit()
    await _bind(test_session_maker, entries=[_DICT[3]])
    assert ("C-1", "failure_mode", "FM-SOLDER-CRACK") in await _links(test_session_maker)
    async with test_session_maker() as s:
        st = await onto_svc.status(s)
    assert set(st["binds"]) == {_HUB, "failuretrace"} and st["rows"]["record_entities"] == 4
    with pytest.raises(ValueError):
        async with test_session_maker() as s:
            await onto_svc.bind_entities(s, hub=" ", entries=_DICT, rules=_RULES)


# ── 프로필 ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_profile_rolls_up_by_expert_and_domain(test_session_maker):
    await _seed(test_session_maker)
    async with test_session_maker() as s:
        await onto_svc.build_mentions(s, rules=_RULES)
        before = await onto_svc.profile(s)
        await s.commit()
    # 허브 없이도 구조 셈은 있다
    assert before["agents"]["rel-solder"] == {"cards": 3, "types": {"failure_mode": {"structural": 1, "linked": 0, "objects": 0}}}
    assert before["domains"]["mat"]["types"] == {"material_property": {"structural": 1, "linked": 0, "objects": 0}}
    await _bind(test_session_maker)
    async with test_session_maker() as s:
        after = await onto_svc.profile(s)
    assert after["agents"]["rel-solder"]["types"]["failure_mode"] == {"structural": 1, "linked": 3, "objects": 1}
    assert after["domains"]["rel"]["cards"] == 4 and after["domains"]["rel"]["types"]["failure_mode"]["linked"] == 3
    assert after["agents"]["rel-drop"] == {"cards": 1, "types": {}}


# ── 질문 맞추기 · 추천 ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_question_entities_and_seat_signal(test_session_maker):
    await _seed(test_session_maker)
    async with test_session_maker() as s:
        assert await onto_svc.question_entities(s, "솔더 크랙 원인") == []           # 잇기 전에는 신호가 없다
        assert await onto_svc.seat_signal(s, "솔더 크랙 원인") == ({}, [])
    await _bind(test_session_maker)
    async with test_session_maker() as s:
        ents = await onto_svc.question_entities(s, "리플로 뒤 solder crack이 왜 나는가, 탄성계수는?")
        assert ents == [{"hub": _HUB, "type_slug": "failure_mode", "key": "FM-SOLDER-CRACK"},
                        {"hub": _HUB, "type_slug": "material_property", "key": "MP-E"}]
        assert await onto_svc.question_entities(s, "미세균열 PCBs") == []
        sig, got = await onto_svc.seat_signal(s, "솔더 크랙 원인")
    assert len(got) == 1 and set(sig) == {"rel-solder"}
    # 굵은 연결 둘(C-1 열쇠말 · P-1 제목) + 본문 연결 하나(C-2) → 깊이 min(1, 2.5/3). 혼자 다루는 객체라 드묾은 1
    assert sig["rel-solder"] == {"objects": 1, "strong": 2, "weak": 1, "bonus": round(onto_svc.SEAT_WEIGHT * 2.5 / 3, 4)}


@pytest.mark.asyncio
async def test_seat_signal_discounts_objects_many_experts_cover(test_session_maker):
    """흔한 객체 하나가 여럿에게 같은 덧점을 주지 않는다 — 다루는 전문가가 많을수록 덧점이 준다."""
    from api.db.models import Agent

    async with test_session_maker() as s:
        for i in range(8):
            s.add(Agent(agent_type=f"pcb-x{i}", name=f"x{i}", description="", common_tags=[], data_types=["DOC"]))
            for j in range(3):
                s.add(_rec(f"K-{i}-{j}", 100 + i * 10 + j, doc_type="paper", agents=[f"pcb-x{i}"], subject_keywords=["흔한 고장"]))
        s.add(Agent(agent_type="rel-only", name="only", description="", common_tags=[], data_types=["DOC"]))
        for j in range(3):
            s.add(_rec(f"R-{j}", 300 + j, doc_type="paper", agents=["rel-only"], subject_keywords=["드문 고장"]))
        await s.commit()
    await _bind(test_session_maker, entries=[{"type_slug": "failure_mode", "key": "FM-COMMON", "term": "흔한 고장"},
                                             {"type_slug": "failure_mode", "key": "FM-RARE", "term": "드문 고장"}])
    async with test_session_maker() as s:
        common, _ = await onto_svc.seat_signal(s, "흔한 고장")
        rare, _ = await onto_svc.seat_signal(s, "드문 고장")
        both, ents = await onto_svc.seat_signal(s, "흔한 고장과 드문 고장")
    w = onto_svc.SEAT_WEIGHT
    assert len(common) == 8 and {v["bonus"] for v in common.values()} == {round(w * 0.25, 4)}       # 1 / (1 + log2 8)
    assert rare == {"rel-only": {"objects": 1, "strong": 3, "weak": 0, "bonus": round(w * 1.0, 4)}}
    # 객체 둘을 물으면 평균이다 — 하나만 가진 전문가는 그 절반
    assert len(ents) == 2 and both["rel-only"]["bonus"] == round(w * 0.5, 4) and both["pcb-x0"]["bonus"] == round(w * 0.125, 4)


@pytest.fixture
def _rt(monkeypatch, test_session_maker):
    from api import mcp_runtime

    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.delenv("AIDH_SEARCH_TOP_K_MAX", raising=False)
    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    return mcp_runtime


@pytest.mark.asyncio
async def test_recommend_onto_off_is_unchanged_shadow_annotates_rank_reorders(_rt, test_session_maker):
    from api.db.models import Agent

    await _seed(test_session_maker)
    async with test_session_maker() as s:
        # 이름이 질문과 꼭 맞는 전문가 — 신호가 없으면 이 사람이 앞선다
        s.add(Agent(agent_type="rel-crack-theory", name="솔더 크랙 원인", description="솔더 크랙 원인", common_tags=[],
                    data_types=["DOC"]))
        await s.commit()
    q = "솔더 크랙 원인"
    base = await _rt.recommend_agents(q=q, top_k=10, top_k_tools=0)
    assert "onto" not in base and all("onto" not in a for a in base["agents"])
    await _bind(test_session_maker)
    off = await _rt.recommend_agents(q=q, top_k=10, top_k_tools=0)
    assert off == base                                               # 연결이 생겨도 청하지 않으면 글자 그대로
    assert await _rt.recommend_agents(q=q, top_k=10, top_k_tools=0, onto="모르는 값") == base
    order = [a["agent_type"] for a in base["agents"]]
    assert order[0] == "rel-crack-theory" and "rel-solder" in order

    shadow = await _rt.recommend_agents(q=q, top_k=10, top_k_tools=0, onto="shadow")
    assert [a["agent_type"] for a in shadow["agents"]] == order      # 순위는 그대로
    assert [a["score"] for a in shadow["agents"]] == [a["score"] for a in base["agents"]]
    by = {a["agent_type"]: a for a in shadow["agents"]}
    assert by["rel-solder"]["onto"]["objects"] == 1 and "onto" not in by["rel-crack-theory"]
    assert shadow["onto"] == {"mode": "shadow", "entities": 1, "agents": 1, "applied": False, "verdict": None}

    rank = await _rt.recommend_agents(q=q, top_k=10, top_k_tools=0, onto="rank")
    rby = {a["agent_type"]: a for a in rank["agents"]}
    assert rby["rel-solder"]["score"] == pytest.approx(by["rel-solder"]["score"] + by["rel-solder"]["onto"]["bonus"], abs=1e-3)
    assert rby["rel-crack-theory"]["score"] == by["rel-crack-theory"]["score"]
    assert "이어진 카드 3장" in rby["rel-solder"]["why"] and rank["onto"]["applied"] is True
    # 질문에 객체가 없으면 rank 도 순위가 그대로다
    plain = await _rt.recommend_agents(q="낙하 충격", top_k=10, top_k_tools=0)
    ranked = await _rt.recommend_agents(q="낙하 충격", top_k=10, top_k_tools=0, onto="rank")
    assert [(a["agent_type"], a["score"]) for a in ranked["agents"]] == [(a["agent_type"], a["score"]) for a in plain["agents"]]
    assert ranked["onto"] == {"mode": "rank", "entities": 0, "agents": 0, "applied": False, "verdict": None}


@pytest.mark.asyncio
async def test_맡은_사람은_이번_추천의_후보_안에서_센다(_rt, test_session_maker):
    """객체에 이어진 카드의 전문가가 전부 후보 밖(전문가 표에 없다)이면 이 추천에서는 맡은 사람이 없다 — 덧점도 후보에게만 간다."""
    from sqlalchemy import delete

    from api.db.models import Agent

    await _seed(test_session_maker)
    await _bind(test_session_maker)
    q = "솔더 크랙 원인"
    assert (await _rt.recommend_agents(q=q, top_k=10, top_k_tools=0, onto="shadow"))["onto"]["verdict"] is None
    async with test_session_maker() as s:
        await s.execute(delete(Agent).where(Agent.agent_type == "rel-solder"))
        s.add(Agent(agent_type="rel-other", name="솔더 크랙 원인", description="솔더 크랙 원인", common_tags=[], data_types=["DOC"]))
        await s.commit()
    got = await _rt.recommend_agents(q=q, top_k=10, top_k_tools=0, onto="shadow")
    assert "rel-solder" not in [a["agent_type"] for a in got["agents"]]
    assert got["onto"]["entities"] == 1 and got["onto"]["agents"] == 1, "신호 자체는 그대로 센다(이어진 전문가 수)"
    assert got["onto"]["verdict"] == "no_owner"


# ── 명령 ─────────────────────────────────────────────────────────────────
def test_cli_bind_reads_dictionary_file_and_reports_counts_only(monkeypatch, test_session_maker, tmp_path, capsys):
    import asyncio

    from api.db import base as db_base
    from api.scripts import onto as cli

    class _Engine:
        async def dispose(self):
            return None

    asyncio.run(_seed(test_session_maker))
    monkeypatch.setattr(db_base, "SessionLocal", test_session_maker)
    monkeypatch.setattr(db_base, "engine", _Engine())
    monkeypatch.setattr(onto_svc, "load_rules", lambda path=None: dict(_RULES))
    assert cli.main(["build"]) == 0
    assert json.loads(capsys.readouterr().out)["by_type"] == {"failure_mode": 1, "material_property": 1}
    f = tmp_path / "dict.json"
    f.write_text(json.dumps({"hub": _HUB, "dict_version": "d1", "entries": _DICT}), encoding="utf-8")
    assert cli.main(["bind", "--dict", str(f)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["bind"]["links"] == 4 and out["bind"]["hub"] == _HUB and out["mentions"]["skipped"] is True
    assert cli.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"]["record_entities"] == 4
    assert cli.main(["profile", "--domains"]) == 0
    assert set(json.loads(capsys.readouterr().out)) == {"domains"}
    f.write_text(json.dumps({"entries": "목록이 아니다"}), encoding="utf-8")
    assert cli.main(["bind", "--dict", str(f)]) == 2
    assert cli.main(["bind", "--dict", str(tmp_path / "없는파일.json")]) == 2


# ── 판정 후보(HWAXPortal docs/expert-ontology PLAN 2.4 · EO-12 — 알리기만 한다) ─────────────────────────────
def test_verdict_는_코어_데이터_질문과_맡은_사람_없음을_가르고_나머지는_말하지_않는다():
    task = {"hub": "h", "type_slug": "plm_task", "key": "T-1"}
    model = {"hub": "h", "type_slug": "plm_model", "key": "M-1"}
    fm = {"hub": "h", "type_slug": "failure_mode", "key": "FM-1"}
    sig = {"rel-solder": {"objects": 1, "strong": 1, "weak": 0, "bonus": 0.3}}
    assert onto_svc.verdict([], {}) is None, "객체가 안 풀렸으면 말할 것이 없다"
    assert onto_svc.verdict([task, model], {}) == "core_question"
    assert onto_svc.verdict([task], sig) == "core_question", "과제만 풀렸으면 이어진 전문가가 있어도 기준정보 질문 후보다"
    assert onto_svc.verdict([fm], {}) == "no_owner"
    assert onto_svc.verdict([task, fm], {}) == "no_owner", "도메인 객체가 하나라도 있으면 코어 데이터 질문이 아니다"
    assert onto_svc.verdict([fm], sig) is None and onto_svc.verdict([task, fm], sig) is None
    assert onto_svc.verdict([{"type_slug": ""}, "글", None], {}) is None, "꼴이 틀린 항목은 세지 않는다"
