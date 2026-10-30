# 평탄화가 MX 백서 꼴(subsections) 하위 절도 내려가는지 — 154장 빈 1번 절(AD-01)의 회귀 시험
"""``_flatten_sections`` 는 ``children`` 만 내려갔고 MX 백서 DocumentJSON 은 하위 절을 ``subsections`` 에 둔다.
그래서 1번 절이 글 없는 「본문」 래퍼인 카드는 하위 절 글이 record_sections 에 하나도 없어 챗 · 심의 근거에서
빠졌다. 두 키를 다 내려가고, 두 키가 같은 절을 함께 들면 한 번만 걷는다.
"""
from __future__ import annotations

import pytest


def _p(text: str) -> list[dict]:
    return [{"type": "paragraph", "text": text}]


def _ids(rows: list[dict]) -> list[str]:
    return [r["section_id"] for r in rows]


def test_children_only_is_unchanged():
    from api.ingest.db_writer import _flatten_sections

    sections = [{"number": "1", "level": 1, "title": "개요", "blocks": _p("a"),
                 "children": [{"number": "1.1", "level": 2, "title": "범위", "blocks": _p("b"),
                               "children": [{"number": "1.1.1", "level": 3, "title": "세부",
                                             "blocks": _p("c")}]}]},
                {"number": "2", "level": 1, "title": "결론", "blocks": _p("d")}]
    dropped: dict = {}
    out = _flatten_sections(sections, max_level=3, dropped=dropped)
    assert _ids(out) == ["1", "1.1", "1.1.1", "2"]
    assert [r["section_path"] for r in out] == [None, "개요", "개요 > 범위", None]
    assert [r["content_text"] for r in out] == ["a", "b", "c", "d"]
    assert dropped["dup"] == dropped["deep"] == dropped["noid"] == 0


def test_mxwp_subsections_are_indexed():
    from api.ingest.db_writer import _flatten_sections

    # MX 백서 docx 가져오기의 「본문」 래퍼 — 1단계 절은 글이 없고 글은 하위 절에만 있다.
    sections = [{"id": "01J0000000000000000000000A", "number": "1", "level": 1, "title": "본문", "blocks": [],
                 "subsections": [
                     {"id": "01J0000000000000000000000B", "number": "1.1", "level": 2, "title": "배경",
                      "blocks": _p("하위 절 글"), "subsections": []},
                     {"id": "01J0000000000000000000000C", "number": "1.2", "level": 2, "title": "결과",
                      "blocks": [{"type": "list", "style": "bullet", "items": ["가", "나"]}]},
                 ]}]
    out = _flatten_sections(sections, max_level=3)
    assert _ids(out) == ["1", "1.1", "1.2"]
    by_id = {r["section_id"]: r for r in out}
    assert by_id["1"]["content_text"] == ""                       # 래퍼 자신은 그대로 비어 있다
    assert by_id["1.1"]["content_text"] == "하위 절 글"
    assert by_id["1.2"]["content_text"] == "- 가\n- 나"
    assert by_id["1.1"]["section_path"] == "본문" and by_id["1.1"]["level"] == 2


def test_same_nodes_under_both_keys_are_walked_once():
    from api.ingest.db_writer import _flatten_sections

    kid = {"number": "1.1", "level": 2, "title": "하위", "blocks": _p("한 번만")}
    sections = [{"number": "1", "level": 1, "title": "개요", "blocks": _p("a"),
                 "children": [kid], "subsections": [dict(kid)]}]      # 같은 절을 두 키가 함께 든다(값이 같다)
    dropped: dict = {}
    out = _flatten_sections(sections, max_level=3, dropped=dropped)
    assert _ids(out) == ["1", "1.1"]
    assert dropped["dup"] == 0 and dropped["dup_chars"] == 0          # 중복 section_id 로 세지 않는다


def test_different_nodes_under_both_keys_are_both_walked():
    from api.ingest.db_writer import _flatten_sections

    sections = [{"number": "1", "level": 1, "title": "개요", "blocks": _p("a"),
                 "children": [{"number": "1.1", "level": 2, "title": "자식", "blocks": _p("c1")}],
                 "subsections": [{"number": "1.2", "level": 2, "title": "하위", "blocks": _p("s1")}]}]
    out = _flatten_sections(sections, max_level=3)
    assert _ids(out) == ["1", "1.1", "1.2"]                          # children 먼저, 그다음 subsections
    assert [r["content_text"] for r in out] == ["a", "c1", "s1"]


def test_nested_deeper_subsections_and_mixed_keys():
    from api.ingest.db_writer import _flatten_sections

    deep = {"number": "1.1.1.1", "level": 4, "title": "4단계", "blocks": _p("깊은 글")}
    sections = [{"number": "1", "level": 1, "title": "본문", "blocks": [],
                 "subsections": [{"number": "1.1", "level": 2, "title": "가", "blocks": [],
                                  "subsections": [{"number": "1.1.1", "level": 3, "title": "나",
                                                   "blocks": _p("셋째 단계"), "subsections": [deep]}]}]},
                {"number": "2", "level": 1, "title": "섞임", "blocks": [],
                 "children": [{"number": "2.1", "level": 2, "title": "다", "blocks": [],
                               "subsections": [{"number": "2.1.1", "level": 3, "title": "라",
                                                "blocks": _p("자식 아래 하위 절")}]}]}]
    dropped: dict = {}
    out = _flatten_sections(sections, max_level=3, dropped=dropped)
    assert _ids(out) == ["1", "1.1", "1.1.1", "2", "2.1", "2.1.1"]
    by_id = {r["section_id"]: r for r in out}
    assert by_id["1.1.1"]["section_path"] == "본문 > 가"
    assert by_id["2.1.1"]["content_text"] == "자식 아래 하위 절"
    # 4단계는 지금처럼 색인에서 빠지고, 빠진 것으로 센다(예전엔 걷지도 않아 세지도 않았다).
    assert dropped["deep"] == 1 and dropped["deep_chars"] == len("깊은 글")


@pytest.mark.asyncio
async def test_write_record_indexes_mxwp_subsections(test_session):
    from sqlalchemy import select

    from api.db.models import OrgTeam, RecordSection
    from api.ingest.db_writer import write_record
    from api.schemas import RecordIn

    test_session.add(OrgTeam(code="HE", name="HE"))
    await test_session.flush()
    rid = "DOC-HE-CAE-2026-0000000501"
    content = {"sections": [{"number": "1", "level": 1, "title": "본문", "blocks": [],
                             "subsections": [{"number": "1.1", "level": 2, "title": "배경",
                                              "blocks": _p("적재 길에서도 들어간다")}]}]}
    res = await write_record(test_session, RecordIn(id=rid, data_type="DOC", title="MX", content=content))
    assert res.action == "inserted" and res.sections_written == 2
    rows = (await test_session.execute(
        select(RecordSection.section_id, RecordSection.content_text)
        .where(RecordSection.record_id == rid).order_by(RecordSection.section_id))).all()
    assert [tuple(r) for r in rows] == [("1", ""), ("1.1", "적재 길에서도 들어간다")]
