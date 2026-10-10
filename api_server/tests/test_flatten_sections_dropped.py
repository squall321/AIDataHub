# 적재 평탄화가 색인에서 빼는 절(4단계 이하·중복 section_id)을 세는지 — 말없이 빠지던 것의 회귀 시험
"""``_flatten_sections(max_level=3)`` 는 4단계 이하 절과 같은 section_id 의 두 번째 등장을
record_sections 에 넣지 않는다. 그 본문은 의미검색·FTS·get_record_sections 어디에도 없고
get_record 의 content JSON 에만 남는다. 색인 단위는 재색인이 걸린 결정이라 이번에 바꾸지 않고,
빠진 수를 세어 적재 로그에 남긴다(예전엔 로그도 수도 없었다).
"""
from __future__ import annotations


def test_flatten_counts_what_it_leaves_out():
    from api.ingest.db_writer import _flatten_sections

    sections = [
        {"number": "1", "level": 1, "title": "개요", "content_text": "a",
         "children": [
             {"number": "1.1", "level": 2, "title": "범위", "content_text": "b",
              "children": [
                  {"number": "1.1.1", "level": 3, "title": "세부", "content_text": "c",
                   "children": [
                       {"number": "1.1.1.1", "level": 4, "title": "더 세부", "content_text": "깊은 본문"},
                   ]},
              ]},
         ]},
        {"number": "1", "level": 1, "title": "같은 번호", "content_text": "두 번째 본문"},
    ]
    dropped: dict = {}
    out = _flatten_sections(sections, max_level=3, dropped=dropped)
    assert [r["section_id"] for r in out] == ["1", "1.1", "1.1.1"]      # 종전과 같다
    assert dropped == {"deep": 1, "deep_chars": len("깊은 본문"),
                       "dup": 1, "dup_chars": len("두 번째 본문"),
                       "noid": 0, "noid_chars": 0}
