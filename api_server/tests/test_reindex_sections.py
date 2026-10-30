# 절 색인 다시 짓기 명령(api.scripts.reindex_sections)이 하위 절이 빠진 카드만 고치는지 — AD-01 박스별 재색인 시험
"""평탄화를 고쳐도 이미 적재된 MX 백서 카드의 record_sections 는 그대로다(적재는 본문이 같으면 건너뛴다).
명령은 기본이 셈만이고, ``--apply`` 에서 content 의 절 트리에 subsections 가 있고 행이 고친 평탄화와 다른
레코드만 다시 짓는다. records 행(updated_at · content_hash)은 쓰지 않고, 다시 돌리면 할 일이 0 이다.

다시 지어도 지금 행의 글을 잃지 않는다 — 글이 그대로인 절은 임베딩을 옮겨 심고, 사라질 글이 있는 카드(held)는
``--allow-loss`` 일 때만, 동기화 자리표시(글이 절 행에만 남은 카드)는 어느 경우에도 다시 짓지 않고 센다.
앞쪽 시험은 DB 없이 돈다(셈 · 거르기 · 잠금 · 인자). 뒤쪽은 SQLite(aiosqlite)가 있어야 돈다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

_PH = "DOC-MX-WP-2026-0000000109"     # 10-15 cae00 실측 0109 의 꼴 — content 는 동기화 자리표시, 글은 절 행에만
_WRAP = "DOC-MX-WP-2026-0000000901"   # 「본문」 래퍼 — 글은 하위 절에만, 행은 예전 평탄화 그대로(빈 1번 절 · 2번)
_OK = "DOC-MX-WP-2026-0000000902"     # 하위 절이 있지만 행이 이미 맞다
_PLAIN = "DOC-MX-WP-2026-0000000903"  # children 꼴 — 행이 낡았어도(section_path 없음) 이 명령의 몫이 아니다
_WORD = "DOC-MX-WP-2026-0000000904"   # 'subsections' 가 글 · 절 밖 키에만 있다
_DEL = "DOC-MX-WP-2026-0000000905"    # 지운 카드
_DATA = "DATA-MX-WP-2026-0000000906"  # DOC 가 아니다
_MIX = "DOC-MX-WP-2026-0000000907"    # 하위 절이 있고 행은 다른 판(병합으로 섞임) — 다시 지으면 행에만 있는 글이 사라진다
_PH0 = "DOC-MX-WP-2026-0000000908"    # 동기화 자리표시 — 어디에도 글이 없다
_EMPTY = "DOC-MX-WP-2026-0000000909"  # 하위 절이 있지만 어디에도 글이 없다
_OLD = datetime(2026, 1, 1, tzinfo=timezone.utc)
_NO_DROP = {"deep": 0, "deep_chars": 0, "dup": 0, "dup_chars": 0, "noid": 0, "noid_chars": 0}


def _p(text: str) -> list[dict]:
    return [{"type": "paragraph", "text": text}]


def _top(number: str, title: str, text: str) -> dict:
    return {"number": number, "level": 1, "title": title, "blocks": _p(text)}


def _mxwp(*subs: tuple[str, str, str], lead: list[dict] | None = None, more: tuple[dict, ...] = ()) -> dict:
    return {"sections": [{"number": "1", "level": 1, "title": "본문", "blocks": lead or [],
                          "subsections": [{"number": n, "level": 2, "title": t, "blocks": _p(x)}
                                          for n, t, x in subs]}, *more]}


def _placeholder() -> dict:
    # sync_svc.transform_record 가 상세 없이 목록 메타로 짓는 꼴(84f4ffc 전에는 상세 조회가 실패해도 이것으로 덮었다)
    return {"sections": [{"section_id": "1", "level": 1, "title": "본문", "content_text": ""}]}


_WRAP_CONTENT = _mxwp(("1.1", "배경", "배경 글"), ("1.2", "결과", "결과 글"), more=(_top("2", "개요", "개요 글"),))
_MIX_CONTENT = _mxwp(("1.1", "하위", "새 글"), more=(_top("2", "개요", "개요 새 글"),))
_EMPTY_CONTENT = _mxwp(("1.1", "빈 하위", ""))


def _r(level: int, title: str, text: str, path: str | None = None):
    from api.scripts.reindex_sections import _Row

    return _Row(level, title, text, (), (), path, None, None)


# --------------------------------------------------------------------------- DB 없이

def test_has_subsections_looks_under_children():
    from api.scripts.reindex_sections import _has_subsections

    assert _has_subsections([{"title": "a", "children": [{"title": "b", "subsections": [{"title": "c"}]}]}])
    assert not _has_subsections([{"title": "a", "subsections": [], "children": [{"title": "b", "subsections": []}]}])
    assert not _has_subsections({"subsections": [{"title": "c"}]})        # 절 트리가 목록이 아니다


def test_placeholder_is_only_the_sync_fallback_shape():
    from api.scripts.reindex_sections import _is_placeholder

    assert _is_placeholder(_placeholder())
    filled = {"sections": [{"section_id": "1", "level": 1, "title": "본문", "content_text": "본문 글"}]}
    two = {"sections": [*_placeholder()["sections"], {"section_id": "2", "level": 1, "title": "부록",
                                                      "content_text": ""}]}
    assert not _is_placeholder(filled)          # 본문이 있다
    assert not _is_placeholder(two)             # 절이 둘이다
    assert not _is_placeholder(_EMPTY_CONTENT)  # 「본문」 래퍼(하위 절) — 다시 짓기의 몫
    assert not _is_placeholder({"sections": "본문"})


def test_plan_wrapper_card_adds_subsections_and_keeps_old_rows():
    from api.scripts.reindex_sections import _plan

    cur = {"1": _r(1, "본문", ""), "2": _r(1, "개요", "개요 글")}   # 예전 평탄화가 지은 행
    plan = _plan(_WRAP_CONTENT, cur)
    assert plan["rows"] == {"before": 2, "after": 4, "added": 2, "removed": 0, "changed": 0, "reembed": 2}
    assert plan["keep"] == ["1", "2"]                                  # 임베딩을 옮겨 심을 절
    assert plan["lost"] == []
    assert plan["chars"] == {"before": 4, "after": 12}
    assert plan["textless"] == {"before": 0, "after": 0}
    assert plan["dropped_after"] == _NO_DROP


def test_plan_mixed_card_lists_the_text_it_would_lose():
    from api.scripts.reindex_sections import _plan

    cur = {"1": _r(1, "본문(옛 판)", ""), "2": _r(1, "개요", "개요 옛 글"), "3": _r(1, "운영", "운영에만 남은 글")}
    plan = _plan(_MIX_CONTENT, cur)
    assert plan["rows"] == {"before": 3, "after": 3, "added": 1, "removed": 1, "changed": 2, "reembed": 2}
    assert plan["keep"] == ["1"]                    # 제목만 바뀐 절도 글이 같으면 임베딩을 옮겨 심는다
    assert plan["chars"] == {"before": 15, "after": 9}
    assert plan["lost"] == [
        {"section_id": "2", "title": "개요", "chars": 6, "head": "개요 옛 글"},
        {"section_id": "3", "title": "운영", "chars": 9, "head": "운영에만 남은 글"},
    ]
    # 글이 다른 section_id 로 옮겨 가기만 하면 잃는 것이 아니다.
    moved = {"1": _r(1, "본문", ""), "9": _r(1, "옛 번호", "새 글"), "2": _r(1, "개요", "개요 새 글")}
    assert _plan(_MIX_CONTENT, moved)["lost"] == []


def test_plan_counts_textless_and_is_none_when_rows_match():
    from api.scripts.reindex_sections import _plan

    plan = _plan(_EMPTY_CONTENT, {"1": _r(1, "본문", "")})
    assert plan["textless"] == {"before": 1, "after": 1}
    assert plan["rows"]["added"] == 1 and plan["lost"] == []
    assert _plan(_EMPTY_CONTENT, {"1": _r(1, "본문", ""), "1.1": _r(2, "빈 하위", "", "본문")}) is None


def test_records_query_locks_only_when_applying():
    from sqlalchemy.dialects import postgresql

    from api.scripts.reindex_sections import _records_query

    def sql(lock: bool) -> str:
        return str(_records_query([_WRAP], lock=lock).compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE" in sql(True)
    assert "FOR UPDATE" not in sql(False)


def test_cli_counts_only_unless_apply(monkeypatch, capsys):
    from api.scripts import reindex_sections as mod

    calls: list[tuple[bool, bool]] = []

    async def fake_main(apply: bool, allow_loss: bool) -> dict:
        calls.append((apply, allow_loss))
        return {"mode": "apply" if apply else "dry-run", "affected": 0, "held": 0, "rebuilt": 0,
                "placeholders": {"cards": 0, "rows_have_text": 0, "sample": []}}

    monkeypatch.setattr(mod, "_main", fake_main)
    assert mod.main([]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "dry-run"
    assert mod.main(["--apply"]) == 0
    assert mod.main(["--apply", "--allow-loss"]) == 0
    with pytest.raises(SystemExit) as exc:
        mod.main(["--allow-loss"])                 # 셈만 실행에는 뜻이 없다 — 인자 오류
    assert exc.value.code == 2
    assert calls == [(False, False), (True, False), (True, True)]


# --------------------------------------------------------------------------- SQLite

def _emb(seed: float) -> dict:
    return {"embedding": [seed] * 768, "embedded_at": _OLD, "embedding_model": "e5-test",
            "embedding_m3": [seed] * 1024, "embedding_m3_at": _OLD, "embedding_m3_model": "bge-m3-test"}


async def _seed(maker) -> None:
    from api.db.models import Record, RecordSection

    def rec(rid: str, seq: int, content: dict, **kw) -> Record:
        return Record(id=rid, data_type=kw.pop("data_type", "DOC"), team="MX", group="WP", year=2026, seq=seq,
                      title=rid[-3:], content=content, content_hash=f"h{seq}", updated_at=_OLD, **kw)

    def sec(rid: str, sid: str, level: int, title: str, text: str, path: str | None = None,
            **kw) -> RecordSection:
        return RecordSection(record_id=rid, section_id=sid, level=level, title=title, content_text=text,
                             section_path=path, **kw)

    async with maker() as s:
        s.add_all([
            rec(_PH, 109, _placeholder()),
            rec(_WRAP, 901, _WRAP_CONTENT),
            rec(_OK, 902, _mxwp(("1.1", "하위", "하위 글"), lead=_p("머리 글"))),
            rec(_PLAIN, 903, {"sections": [{"number": "1", "level": 1, "title": "개요", "blocks": _p("a"),
                                            "children": [{"number": "1.1", "level": 2, "title": "범위",
                                                          "blocks": _p("b")}]}]}),
            rec(_WORD, 904, {"toc": [{"title": "목차", "subsections": [{"title": "x"}]}],
                             "sections": [{"number": "1", "level": 1, "title": "용어",
                                           "blocks": _p('MX 백서는 하위 절을 "subsections" 에 둔다')}]}),
            rec(_DEL, 905, _mxwp(("1.1", "지움", "지운 글")), deleted_at=_OLD),
            rec(_DATA, 906, {"subsections": [{"title": "표"}]}, data_type="DATA"),
            rec(_MIX, 907, _MIX_CONTENT),
            rec(_PH0, 908, _placeholder()),
            rec(_EMPTY, 909, _EMPTY_CONTENT),
        ])
        await s.flush()
        s.add_all([
            sec(_PH, "1", 1, "본문", ""), sec(_PH, "2", 1, "개요", "개요 글 700자"),
            sec(_PH, "4", 1, "응용", "응용 글 729자"),
            sec(_WRAP, "1", 1, "본문", "", **_emb(0.1)), sec(_WRAP, "2", 1, "개요", "개요 글", **_emb(0.2)),
            sec(_OK, "1", 1, "본문", "머리 글"), sec(_OK, "1.1", 2, "하위", "하위 글", "본문"),
            sec(_PLAIN, "1", 1, "개요", "a"), sec(_PLAIN, "1.1", 2, "범위", "b"),   # section_path 가 빠진 옛 행
            sec(_WORD, "1", 1, "옛 제목", "옛 글"),
            sec(_DEL, "1", 1, "본문", ""),
            sec(_DATA, "1", 1, "표", "값"),
            sec(_MIX, "1", 1, "본문(옛 판)", "", **_emb(0.3)), sec(_MIX, "2", 1, "개요", "개요 옛 글", **_emb(0.4)),
            sec(_MIX, "3", 1, "운영", "운영에만 남은 글"),
            sec(_PH0, "1", 1, "본문", ""),
            sec(_EMPTY, "1", 1, "본문", "", embedded_at=_OLD, embedding_model="skipped-empty"),
        ])
        await s.commit()


async def _sections(maker) -> list[tuple]:
    from sqlalchemy import select

    from api.db.models import RecordSection as S

    async with maker() as s:
        rows = (await s.execute(select(S.id, S.record_id, S.section_id, S.level, S.title, S.content_text,
                                       S.section_path).order_by(S.record_id, S.id))).all()
    return [tuple(r) for r in rows]


async def _embeddings(maker, rid: str) -> dict[str, tuple]:
    from sqlalchemy import select

    from api.db.models import RecordSection as S

    t = S.__table__

    def vec(v):
        return None if v is None else tuple(round(float(x), 3) for x in v)

    async with maker() as s:
        rows = (await s.execute(select(t.c.section_id, t.c.embedding, t.c.embedded_at, t.c.embedding_model,
                                       t.c.embedding_m3, t.c.embedding_m3_at, t.c.embedding_m3_model)
                                .where(t.c.record_id == rid))).all()
    return {r[0]: (vec(r[1]), r[2], r[3], vec(r[4]), r[5], r[6]) for r in rows}


async def _records(maker) -> list[tuple]:
    from sqlalchemy import select

    from api.db.models import Record

    async with maker() as s:
        rows = (await s.execute(select(Record.id, Record.updated_at, Record.content_hash, Record.title)
                                .order_by(Record.id))).all()
    return [tuple(r) for r in rows]


@pytest.mark.asyncio
async def test_dry_run_counts_and_writes_nothing(test_session_maker, test_engine):
    from sqlalchemy import event

    from api.scripts.reindex_sections import run

    await _seed(test_session_maker)
    before = await _sections(test_session_maker)
    verbs: list[str] = []

    def spy(_conn, _cur, statement, *_a) -> None:
        verbs.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(test_engine.sync_engine, "before_cursor_execute", spy)
    try:
        res = await run(test_session_maker, apply=False)
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", spy)

    assert verbs and set(verbs) == {"SELECT"}         # 셈만은 읽기만 한다 — 지웠다 되돌리지도 않는다
    assert res["mode"] == "dry-run" and res["allow_loss"] is False
    assert res["candidates"] == 4                      # _WRAP · _OK · _MIX · _EMPTY — 글 속 낱말 · 지운 것 · DATA 는 빠진다
    assert res["affected"] == 3 and res["sample"] == [_WRAP, _MIX, _EMPTY]
    assert res["held"] == 1 and res["rebuilt"] == 0
    assert res["rows"] == {"before": 6, "after": 9, "added": 4, "removed": 1, "changed": 2, "reembed": 5}
    assert res["chars"] == {"before": 4 + 15, "after": 12 + 9}
    assert res["textless"] == {"before": 1, "after": 1}
    assert res["dropped_after"] == _NO_DROP
    assert res["held_cards"] == [{"id": _MIX, "chars": {"before": 15, "after": 9}, "lost": [
        {"section_id": "2", "title": "개요", "chars": 6, "head": "개요 옛 글"},
        {"section_id": "3", "title": "운영", "chars": 9, "head": "운영에만 남은 글"},
    ]}]
    # 동기화 자리표시는 다시 짓지 않고 센다 — 0109 는 글이 절 행에만 있다.
    assert res["placeholders"] == {"cards": 2, "rows_have_text": 1, "sample": [_PH, _PH0]}
    assert await _sections(test_session_maker) == before   # 쓰지 않았다


@pytest.mark.asyncio
async def test_apply_rebuilds_safe_cards_keeps_embeddings_and_holds_the_rest(test_session_maker):
    from api.scripts.reindex_sections import run

    await _seed(test_session_maker)
    sec_before = await _sections(test_session_maker)
    rec_before = await _records(test_session_maker)
    emb_before = await _embeddings(test_session_maker, _WRAP)

    res = await run(test_session_maker, apply=True, chunk=2)   # 묶음 경계를 넘긴다
    assert res["mode"] == "apply" and res["affected"] == 3 and res["held"] == 1 and res["rebuilt"] == 2

    sec_after = await _sections(test_session_maker)
    # 행 순서(id 순 — get_record_sections 가 읽는 순서)는 적재처럼 문서 순서다.
    assert [r[2:] for r in sec_after if r[1] == _WRAP] == [
        ("1", 1, "본문", "", None),
        ("1.1", 2, "배경", "배경 글", "본문"),
        ("1.2", 2, "결과", "결과 글", "본문"),
        ("2", 1, "개요", "개요 글", None),
    ]
    assert [r[2:] for r in sec_after if r[1] == _EMPTY] == [("1", 1, "본문", "", None),
                                                           ("1.1", 2, "빈 하위", "", "본문")]
    # 글이 그대로인 절은 옛 임베딩(v1 · m3 의 값 · 시각 · 모델)을 그대로 갖고, 새 절만 빈다.
    emb_after = await _embeddings(test_session_maker, _WRAP)
    assert emb_after["1"] == emb_before["1"] and emb_after["2"] == emb_before["2"]
    assert emb_after["2"][0] == (0.2,) * 768 and emb_after["2"][3] == (0.2,) * 1024
    assert emb_after["1.1"] == emb_after["1.2"] == (None,) * 6
    assert (await _embeddings(test_session_maker, _EMPTY))["1"][2] == "skipped-empty"
    # held(_MIX) · 자리표시(_PH · _PH0)와 다른 카드의 행은 기본 키까지 그대로다.
    untouched = {_PH, _OK, _PLAIN, _WORD, _DEL, _DATA, _MIX, _PH0}
    assert [r for r in sec_after if r[1] in untouched] == [r for r in sec_before if r[1] in untouched]
    # records 행은 쓰지 않는다 — 병합 우선순위(updated_at)와 본문 해시가 그대로다.
    assert await _records(test_session_maker) == rec_before

    again = await run(test_session_maker, apply=True)
    assert (again["candidates"], again["affected"], again["held"], again["rebuilt"]) == (4, 1, 1, 0)
    assert await _sections(test_session_maker) == sec_after
    assert (await run(test_session_maker, apply=False))["affected"] == 1


@pytest.mark.asyncio
async def test_allow_loss_rebuilds_held_cards_but_never_placeholders(test_session_maker):
    from api.scripts.reindex_sections import run

    await _seed(test_session_maker)
    sec_before = await _sections(test_session_maker)
    emb_before = await _embeddings(test_session_maker, _MIX)

    res = await run(test_session_maker, apply=True, allow_loss=True)
    assert res["allow_loss"] is True and res["held"] == 1 and res["rebuilt"] == 3
    assert [h["id"] for h in res["held_cards"]] == [_MIX]      # 무엇을 버렸는지 결과에 남는다

    sec_after = await _sections(test_session_maker)
    assert [r[2:] for r in sec_after if r[1] == _MIX] == [
        ("1", 1, "본문", "", None),
        ("1.1", 2, "하위", "새 글", "본문"),
        ("2", 1, "개요", "개요 새 글", None),
    ]
    emb_after = await _embeddings(test_session_maker, _MIX)
    assert emb_after["1"] == emb_before["1"]                    # 글이 같은 절만 옮겨 심는다
    assert emb_after["2"] == emb_after["1.1"] == (None,) * 6     # 글이 바뀐 절은 다시 임베딩한다
    # 자리표시는 --allow-loss 로도 다시 짓지 않는다 — 행에만 남은 글(0109 의 2 · 4번 절)이 그대로다.
    assert [r for r in sec_after if r[1] in (_PH, _PH0)] == [r for r in sec_before if r[1] in (_PH, _PH0)]

    again = await run(test_session_maker, apply=True, allow_loss=True)
    assert (again["affected"], again["held"], again["rebuilt"]) == (0, 0, 0)
    assert again["placeholders"]["cards"] == 2
