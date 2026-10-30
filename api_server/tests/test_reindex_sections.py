# 절 색인 다시 짓기 명령(api.scripts.reindex_sections)이 하위 절이 빠진 카드만 고치는지 — AD-01 박스별 재색인 시험
"""평탄화를 고쳐도 이미 적재된 MX 백서 카드의 record_sections 는 그대로다(적재는 본문이 같으면 건너뛴다).
명령은 기본이 셈만이고, ``--apply`` 에서 content 의 절 트리에 subsections 가 있고 행이 고친 평탄화와 다른
레코드만 다시 짓는다. records 행(updated_at · content_hash)은 쓰지 않고, 다시 돌리면 할 일이 0 이다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

_MX = "DOC-MX-WP-2026-0000000109"     # 「본문」 래퍼 — 글은 하위 절에만, 행은 예전 평탄화 그대로(빈 1번 절)
_OK = "DOC-MX-WP-2026-0000000110"     # 하위 절이 있지만 행이 이미 맞다
_PLAIN = "DOC-MX-WP-2026-0000000111"  # children 꼴 — 행이 낡았어도(section_path 없음) 이 명령의 몫이 아니다
_WORD = "DOC-MX-WP-2026-0000000112"   # 'subsections' 가 글 · 절 밖 키에만 있다
_DEL = "DOC-MX-WP-2026-0000000113"    # 지운 카드
_DATA = "DATA-MX-WP-2026-0000000114"  # DOC 가 아니다
_OLD = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _p(text: str) -> list[dict]:
    return [{"type": "paragraph", "text": text}]


def _mxwp(*subs: tuple[str, str, str], lead: list[dict] | None = None) -> dict:
    return {"sections": [{"number": "1", "level": 1, "title": "본문", "blocks": lead or [],
                          "subsections": [{"number": n, "level": 2, "title": t, "blocks": _p(x)}
                                          for n, t, x in subs]}]}


async def _seed(maker) -> None:
    from api.db.models import Record, RecordSection

    def rec(rid: str, seq: int, content: dict, **kw) -> Record:
        return Record(id=rid, data_type=kw.pop("data_type", "DOC"), team="MX", group="WP", year=2026, seq=seq,
                      title=rid[-3:], content=content, content_hash=f"h{seq}", updated_at=_OLD, **kw)

    def sec(rid: str, sid: str, level: int, title: str, text: str, path: str | None = None) -> RecordSection:
        return RecordSection(record_id=rid, section_id=sid, level=level, title=title, content_text=text,
                             section_path=path)

    async with maker() as s:
        s.add_all([
            rec(_MX, 109, _mxwp(("1.1", "배경", "배경 글"), ("1.2", "결과", "결과 글"))),
            rec(_OK, 110, _mxwp(("1.1", "하위", "하위 글"), lead=_p("머리 글"))),
            rec(_PLAIN, 111, {"sections": [{"number": "1", "level": 1, "title": "개요", "blocks": _p("a"),
                                            "children": [{"number": "1.1", "level": 2, "title": "범위",
                                                          "blocks": _p("b")}]}]}),
            rec(_WORD, 112, {"toc": [{"title": "목차", "subsections": [{"title": "x"}]}],
                             "sections": [{"number": "1", "level": 1, "title": "용어",
                                           "blocks": _p('MX 백서는 하위 절을 "subsections" 에 둔다')}]}),
            rec(_DEL, 113, _mxwp(("1.1", "지움", "지운 글")), deleted_at=_OLD),
            rec(_DATA, 114, {"subsections": [{"title": "표"}]}, data_type="DATA"),
        ])
        await s.flush()
        s.add_all([
            sec(_MX, "1", 1, "본문", ""),
            sec(_OK, "1", 1, "본문", "머리 글"), sec(_OK, "1.1", 2, "하위", "하위 글", "본문"),
            sec(_PLAIN, "1", 1, "개요", "a"), sec(_PLAIN, "1.1", 2, "범위", "b"),   # section_path 가 빠진 옛 행
            sec(_WORD, "1", 1, "옛 제목", "옛 글"),
            sec(_DEL, "1", 1, "본문", ""),
            sec(_DATA, "1", 1, "표", "값"),
        ])
        await s.commit()


async def _sections(maker) -> list[tuple]:
    from sqlalchemy import select

    from api.db.models import RecordSection as S

    async with maker() as s:
        rows = (await s.execute(select(S.id, S.record_id, S.section_id, S.level, S.title, S.content_text,
                                       S.section_path).order_by(S.record_id, S.section_id))).all()
    return [tuple(r) for r in rows]


async def _records(maker) -> list[tuple]:
    from sqlalchemy import select

    from api.db.models import Record

    async with maker() as s:
        rows = (await s.execute(select(Record.id, Record.updated_at, Record.content_hash, Record.title)
                                .order_by(Record.id))).all()
    return [tuple(r) for r in rows]


@pytest.mark.asyncio
async def test_dry_run_counts_and_writes_nothing(test_session_maker):
    from api.scripts.reindex_sections import run

    await _seed(test_session_maker)
    before = await _sections(test_session_maker)
    res = await run(test_session_maker, apply=False)

    assert res["mode"] == "dry-run"
    assert res["candidates"] == 2                      # _MX · _OK — 글 속 낱말(_WORD) · 지운 것 · DATA 는 빠진다
    assert res["affected"] == 1 and res["sample"] == [_MX]
    assert res["rebuilt"] == 0
    assert res["rows"] == {"before": 1, "after": 3, "added": 2, "removed": 0, "changed": 0}
    assert res["chars"] == {"before": 0, "after": len("배경 글") + len("결과 글")}
    assert res["textless"] == {"before": 1, "after": 0}
    assert res["dropped_after"] == {"deep": 0, "deep_chars": 0, "dup": 0, "dup_chars": 0,
                                    "noid": 0, "noid_chars": 0}
    assert await _sections(test_session_maker) == before   # 쓰지 않았다


@pytest.mark.asyncio
async def test_apply_rebuilds_only_the_affected_card_and_is_idempotent(test_session_maker):
    from api.scripts.reindex_sections import run

    await _seed(test_session_maker)
    sec_before = await _sections(test_session_maker)
    rec_before = await _records(test_session_maker)

    res = await run(test_session_maker, apply=True, chunk=2)   # 묶음 경계를 넘긴다
    assert res["mode"] == "apply" and res["affected"] == 1 and res["rebuilt"] == 1

    sec_after = await _sections(test_session_maker)
    assert [r[2:] for r in sec_after if r[1] == _MX] == [
        ("1", 1, "본문", "", None),
        ("1.1", 2, "배경", "배경 글", "본문"),
        ("1.2", 2, "결과", "결과 글", "본문"),
    ]
    # 다른 카드의 행은 기본 키까지 그대로다 — 행이 낡은 _PLAIN · _WORD 도, 지운 _DEL · DATA 도.
    assert [r for r in sec_after if r[1] != _MX] == [r for r in sec_before if r[1] != _MX]
    # records 행은 쓰지 않는다 — 병합 우선순위(updated_at)와 본문 해시가 그대로다.
    assert await _records(test_session_maker) == rec_before

    again = await run(test_session_maker, apply=True)
    assert again["candidates"] == 2 and again["affected"] == 0 and again["rebuilt"] == 0
    assert await _sections(test_session_maker) == sec_after
    assert (await run(test_session_maker, apply=False))["affected"] == 0


def test_cli_counts_only_unless_apply(monkeypatch, capsys):
    from api.scripts import reindex_sections as mod

    calls: list[bool] = []

    async def fake_main(apply: bool) -> dict:
        calls.append(apply)
        return {"mode": "apply" if apply else "dry-run", "affected": 0, "rebuilt": 0}

    monkeypatch.setattr(mod, "_main", fake_main)
    assert mod.main([]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "dry-run"
    assert mod.main(["--apply"]) == 0
    assert calls == [False, True]
