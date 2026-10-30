# record_sections 를 records.content 에서 다시 짓는 CLI — 평탄화가 하위 절(subsections)을 빠뜨린 카드만 고친다
"""절 색인 다시 짓기 — MX 백서 하위 절(AD-01).

    cd api_server && PYTHONPATH=src .venv/bin/python -m api.scripts.reindex_sections          # 셈만(쓰지 않는다)
    ... --apply                                                                               # 다시 짓는다
    ... --apply --allow-loss                                                                  # held 카드까지

박스에서는 ``bash deploy/apptainer/reindex-sections.sh [--apply [--allow-loss]]`` 로 부른다 — API 와 같은 환경(DATABASE_URL ·
AIDH_CHUNK_*)이라야 적재가 짓는 것과 같은 행이 나온다. ``--apply`` 앞에는 ``bash deploy/apptainer/backup-db.sh`` 로 DB 를
떠 둔다 — 다시 지은 카드의 옛 행은 이 명령으로 되돌릴 수 없다.

왜 필요한가 — 평탄화(``api.ingest.db_writer._flatten_sections``)가 children 만 내려가 MX 백서 DocumentJSON 의
subsections 를 건너뛰었다. 하위 절의 글이 record_sections 에 없어 의미검색 · FTS · get_record_sections(챗 · 심의
근거)에서 빠졌고, 1번 절이 글 없는 「본문」 래퍼인 카드는 빈 절로 보였다. 평탄화를 고쳐도 이미 적재된 카드는 그대로다
— 적재는 본문 해시 · 제목 · 요약이 같으면 건너뛰고(write_record), 운영 박스는 동기화를 받지 않는다.

무엇을 다시 짓나(그 밖은 건드리지 않는다):
    살아 있는 DOC 레코드 가운데 content 의 절 트리에 비지 않은 subsections 가 있고, 지금의 record_sections 행이
    고친 평탄화가 content 에서 짓는 행과 다른 것. 그 레코드의 절만 적재와 같은 함수(``_resync_sections`` — 지우고
    다시 넣는다)로 다시 짓는다 — 행 순서(id 순 = get_record_sections 가 읽는 순서)도 적재와 같다. 다시 돌리면 이미 맞는
    레코드는 건너뛴다(멱등).
    - 글이 그대로인 절(같은 section_id · 같은 글)에는 옛 행의 임베딩(v1 · m3 의 값 · 시각 · 모델)을 옮겨 심는다 — 절 임베딩은
      글로만 짓는다. 새 절과 글이 바뀐 절만 비고(rows.reembed), 그 절은 채워질 때까지 의미검색에 안 걸린다(FTS 는 곧바로).
      API 의 임베딩 스윕(AIDH_EMBED_SWEEP_MIN, 기본 30분)이 채우고, 곧바로 채우려면
      ``bash deploy/apptainer/embed-slot.sh fill --slot <활성 슬롯> --table record_sections`` 다.
    - 다시 지으면 지금 행의 글 가운데 새 행 어디에도 없는 글이 생기는 카드는 held 다 — ``--apply`` 도 건드리지 않는다. 그 글은
      content 에 없어 다시 지으면 되돌릴 수 없다(박스 사이 병합으로 다른 판의 행이 섞인 카드 등). held_cards 의 lost(사라질
      절 · 글자 수 · 앞부분)를 사람이 보고, 버려도 되는 옛 판일 때만 ``--allow-loss`` 로 다시 짓는다.
    - records 행은 쓰지 않는다 — updated_at · content_hash 가 그대로라 박스 사이 병합의 우선순위가 바뀌지 않는다.
    - subsections 가 없는 레코드는 행이 어떻든 보지 않는다 — 예전 평탄화와 결과가 같다.
    - content 가 동기화 자리표시(빈 「본문」 한 절 — 상세 조회가 실패해 목록 메타로 덮인 카드, 84f4ffc 전)인 레코드는
      ``--allow-loss`` 로도 다시 짓지 않는다 — 다시 지으면 행에만 남은 글이 사라진다. 원천을 다시 받아야 고쳐지고(dev 가
      다시 받아 덤프 · 병합), 이 명령은 그 수만 placeholders 로 센다. MX 백서 154장이 이 꼴이다(10-14 · 10-15 cae00
      실측 — 149장은 글이 절 행에만 있었다).
    - 후보를 고르느라 DOC 레코드의 content 를 한 번 훑는다(DB 안에서 글자로 거르고 맞는 것만 읽는다).

결과는 표준 출력에 JSON 한 덩이다 — candidates(subsections 가 든 레코드) · affected(행이 고친 평탄화와 다른 레코드, held
포함) · held(그 가운데 사라질 글이 있는 레코드) · rebuilt(--apply 에서 다시 지은 수) · rows(affected 의 절 행 — before ·
after · added · removed · changed · reembed) · chars(절 글자 수 전 · 후) · textless(글 있는 절이 하나도 없는 레코드 전 ·
후) · dropped_after(고친 뒤에도 색인에서 빠지는 절 — 4단계 이하 · 중복 · 식별자 없음) · sample(affected 앞 20개 id) ·
held_cards(held 앞 50장 — lost 는 사라질 절의 section_id · 제목 · 글자 수 · 앞 40자) · placeholders(동기화 자리표시 카드 ·
그 가운데 절 행에만 글이 남은 카드 · 앞 20개 id).

종료 코드: 0 끝 · 1 오류(커밋한 묶음은 남는다 — 다시 돌리면 남은 것만 한다) · 2 인자 오류.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from types import SimpleNamespace
from typing import Any, NamedTuple

log = logging.getLogger("api.scripts.reindex_sections")

_CHUNK = 50  # 한 트랜잭션에서 보는 레코드 수 — 잠금을 짧게 쥔다
_SAMPLE = 20
_HELD_LIST = 50  # held_cards 에 펼쳐 보이는 카드 수
_HEAD = 40  # 사라질 글을 앞에서 몇 자 보이나
# 절 임베딩 칸 — 글(content_text)로만 지으므로 글이 같은 절은 옛 값을 그대로 옮겨 심는다(embed_backfill 의 _sections_spec).
_EMBED_COLS = ("embedding", "embedded_at", "embedding_model", "embedding_m3", "embedding_m3_at", "embedding_m3_model")
# 동기화 자리표시 후보를 DB 안에서 거르는 글자 — jsonb::text(PG) · json.dumps(SQLite 시험) 모두 ': ' 로 쓴다.
_EMPTY_TEXT = '%"content_text": ""%'


class _Row(NamedTuple):
    """견주는 칸 — 적재가 쓰는 칸 그대로(section_id 는 사전 키)."""

    level: int
    title: str
    text: str
    figure_refs: tuple[str, ...]
    table_refs: tuple[str, ...]
    section_path: str | None
    parent_section_id: str | None
    chunk_index: int | None


def _row(r: Any) -> _Row:
    return _Row(
        int(r["level"]), str(r["title"]), str(r["content_text"] or ""),
        tuple(str(x) for x in r["figure_refs"] or ()), tuple(str(x) for x in r["table_refs"] or ()),
        r["section_path"] or None, r["parent_section_id"] or None, r["chunk_index"],
    )


def _has_subsections(nodes: Any) -> bool:
    """절 트리에 비지 않은 subsections 가 있나 — 고친 평탄화가 결과를 바꾸는 레코드의 표지."""
    for node in nodes if isinstance(nodes, list) else ():
        if isinstance(node, dict) and (node.get("subsections") or _has_subsections(node.get("children"))):
            return True
    return False


def _is_placeholder(content: Any) -> bool:
    """동기화 자리표시인가 — 「본문」 한 절에 글 0자(sync_svc.transform_record 가 본문 없이 짓는 꼴)."""
    secs = content.get("sections") if isinstance(content, dict) else None
    if not (isinstance(secs, list) and len(secs) == 1 and isinstance(secs[0], dict)):
        return False
    s = secs[0]
    return s.get("title") == "본문" and not s.get("content_text") and not (
        s.get("blocks") or s.get("children") or s.get("subsections"))


def _plan(content: dict[str, Any], cur: dict[str, _Row]) -> dict[str, Any] | None:
    """한 카드를 다시 지으면 무엇이 바뀌나 — 행이 이미 맞으면 None. DB 없이 시험한다."""
    from ..ingest.db_writer import _section_rows

    dropped: dict[str, int] = {}
    want = {r["section_id"]: _row(r) for r in _section_rows(content, dropped)}
    if want == cur:
        return None
    keep = [k for k in want if k in cur and cur[k].text == want[k].text]  # 임베딩을 옮겨 심을 절
    new_texts = {v.text for v in want.values()}
    lost = [{"section_id": k, "title": v.title, "chars": len(v.text), "head": v.text[:_HEAD]}
            for k, v in cur.items() if v.text and v.text not in new_texts]
    return {
        "keep": keep,
        "lost": lost,
        "rows": {
            "before": len(cur), "after": len(want),
            "added": sum(1 for k in want if k not in cur),
            "removed": sum(1 for k in cur if k not in want),
            "changed": sum(1 for k in want if k in cur and want[k] != cur[k]),
            "reembed": len(want) - len(keep),
        },
        "chars": {"before": sum(len(v.text) for v in cur.values()), "after": sum(len(v.text) for v in want.values())},
        "textless": {"before": int(not any(v.text for v in cur.values())),
                     "after": int(not any(v.text for v in want.values()))},
        "dropped_after": dropped,
    }


def _live_doc() -> tuple[Any, ...]:
    from ..db.models import Record

    return (Record.data_type == "DOC", Record.deleted_at.is_(None))


def _records_query(part: list[str], *, lock: bool) -> Any:
    """한 묶음의 레코드를 읽는 질의 — 다시 지을 때는 records 행을 FOR UPDATE 로 잡는다. 그사이 같은 카드를 적재가 고치면
    그 적재는 이 묶음 뒤에 쓴다 — 옛 content 로 새 절을 덮지 않는다."""
    from sqlalchemy import select

    from ..db.models import Record

    q = select(Record.id, Record.data_type, Record.content).where(Record.id.in_(part), *_live_doc()).order_by(Record.id)
    return q.with_for_update() if lock else q


async def _rebuild(session: Any, rid: str, data_type: str, content: dict[str, Any], keep: list[str]) -> None:
    """한 카드의 절을 적재와 같이 다시 짓고(지우고 다시 넣기), 글이 그대로인 절에는 옛 임베딩을 옮겨 심는다."""
    from sqlalchemy import bindparam, select, update

    from ..db.models import RecordSection
    from ..ingest.db_writer import _resync_sections

    t = RecordSection.__table__
    saved = []
    if keep:
        saved = [s for s in (await session.execute(
            select(t.c.section_id, *(t.c[c] for c in _EMBED_COLS))
            .where(t.c.record_id == rid, t.c.section_id.in_(keep))
        )).mappings().all() if any(s[c] is not None for c in _EMBED_COLS)]
    # _resync_sections 는 record.id 와 record_in.data_type · content 만 읽는다 — records 행은 쓰지 않는다.
    rec = SimpleNamespace(id=rid, data_type=data_type, content=content)
    await _resync_sections(session, rec, rec, RecordSection)
    if saved:
        await session.execute(
            update(t).where(t.c.record_id == bindparam("b_rid"), t.c.section_id == bindparam("b_sid"))
            .values({c: bindparam(f"b_{c}", type_=t.c[c].type) for c in _EMBED_COLS}),
            [{"b_rid": rid, "b_sid": s["section_id"], **{f"b_{c}": s[c] for c in _EMBED_COLS}} for s in saved],
        )


async def run(maker: Any, *, apply: bool, allow_loss: bool = False, chunk: int = _CHUNK) -> dict[str, Any]:
    """다시 지을 레코드를 세고(``apply`` 면 다시 짓고) 결과를 돌려준다. ``maker`` 는 async_sessionmaker 다.

    held 카드는 ``allow_loss`` 일 때만 다시 짓는다. 동기화 자리표시는 어느 경우에도 다시 짓지 않고 센다.
    """
    from sqlalchemy import Text, cast, or_, select

    from ..db.models import Record, RecordSection

    out: dict[str, Any] = {
        "mode": "apply" if apply else "dry-run", "allow_loss": allow_loss,
        "candidates": 0, "affected": 0, "held": 0, "rebuilt": 0,
        "rows": dict.fromkeys(("before", "after", "added", "removed", "changed", "reembed"), 0),
        "chars": {"before": 0, "after": 0}, "textless": {"before": 0, "after": 0},
        "dropped_after": {}, "sample": [], "held_cards": [],
        "placeholders": {"cards": 0, "rows_have_text": 0, "sample": []},
    }
    txt = cast(Record.content, Text)
    async with maker() as session:
        # DB 안에서 글자로 먼저 거른다 — 하위 절 키나 빈 글 칸이 든 것만. 글 속 낱말 · 절 트리 밖의 같은 키는 아래
        # _has_subsections · _is_placeholder 가 뺀다.
        ids = (await session.execute(
            select(Record.id).where(*_live_doc(), or_(txt.like('%"subsections"%'), txt.like(_EMPTY_TEXT)))
            .order_by(Record.id)
        )).scalars().all()

    cols = (RecordSection.record_id, RecordSection.section_id, RecordSection.level, RecordSection.title,
            RecordSection.content_text, RecordSection.figure_refs, RecordSection.table_refs,
            RecordSection.section_path, RecordSection.parent_section_id, RecordSection.chunk_index)
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        async with maker() as session:
            recs = (await session.execute(_records_query(part, lock=apply))).all()
            have: dict[str, dict[str, _Row]] = {}
            for r in (await session.execute(select(*cols).where(RecordSection.record_id.in_(part))
                                            .order_by(RecordSection.record_id, RecordSection.id))).mappings():
                have.setdefault(r["record_id"], {})[r["section_id"]] = _row(r)

            rebuilt = 0
            for rid, data_type, content in recs:
                cur = have.get(rid, {})
                if not isinstance(content, dict):
                    continue
                if not _has_subsections(content.get("sections")):
                    if _is_placeholder(content):
                        ph = out["placeholders"]
                        ph["cards"] += 1
                        ph["rows_have_text"] += int(any(v.text for v in cur.values()))
                        if len(ph["sample"]) < _SAMPLE:
                            ph["sample"].append(rid)
                    continue
                out["candidates"] += 1
                plan = _plan(content, cur)
                if plan is None:
                    continue
                out["affected"] += 1
                for grp in ("rows", "chars", "textless", "dropped_after"):
                    for k, v in plan[grp].items():
                        out[grp][k] = out[grp].get(k, 0) + v
                if len(out["sample"]) < _SAMPLE:
                    out["sample"].append(rid)
                if plan["lost"]:
                    out["held"] += 1
                    if len(out["held_cards"]) < _HELD_LIST:
                        out["held_cards"].append({"id": rid, "chars": plan["chars"], "lost": plan["lost"]})
                    if not allow_loss:
                        continue
                if apply:
                    await _rebuild(session, rid, data_type, content, plan["keep"])
                    rebuilt += 1
            if apply:
                await session.commit()
                out["rebuilt"] += rebuilt
    return out


async def _main(apply: bool, allow_loss: bool) -> dict[str, Any]:
    from ..db import base as db_base

    try:
        return await run(db_base.SessionLocal, apply=apply, allow_loss=allow_loss)
    finally:
        await db_base.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("sqlalchemy", "api.db.base"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(prog="python -m api.scripts.reindex_sections", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="다시 짓는다(없으면 셈만 하고 쓰지 않는다) — 먼저 bash deploy/apptainer/backup-db.sh")
    ap.add_argument("--allow-loss", action="store_true",
                    help="held 카드(다시 지으면 사라질 글이 있는 카드)도 다시 짓는다 — 셈만 실행의 held_cards 를 본 뒤에만")
    args = ap.parse_args(argv)
    if args.allow_loss and not args.apply:
        ap.error("--allow-loss 는 --apply 와 함께만 쓴다")
    try:
        res = asyncio.run(_main(args.apply, args.allow_loss))
    except Exception:  # noqa: BLE001 — 커밋한 묶음은 남는다
        log.exception("실패 — 커밋한 묶음은 남았다. 다시 돌리면 남은 것만 한다")
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if res["rebuilt"]:
        log.info("새 절 · 글이 바뀐 절의 임베딩은 API 의 임베딩 스윕(AIDH_EMBED_SWEEP_MIN, 기본 30분)이 채운다 — 곧바로는 "
                 "bash deploy/apptainer/embed-slot.sh fill --slot <활성 슬롯> --table record_sections")
    elif res["affected"] > res["held"]:
        log.info("셈만 했다(쓰지 않았다) — 다시 지으려면 --apply")
    if res["held"] and not args.allow_loss:
        log.warning("held %d장 — --apply 는 이 카드를 건드리지 않는다(다시 지으면 절 행에만 있는 글이 사라진다). "
                    "held_cards 의 lost 를 보고 버려도 되는 옛 판일 때만 --apply --allow-loss", res["held"])
    ph = res["placeholders"]
    if ph["cards"]:
        log.warning("동기화 자리표시 %d장(그 가운데 절 행에만 글이 남은 %d장)은 이 명령으로 고쳐지지 않는다 — 원천을 다시 "
                    "받아야 한다(dev 동기화 → 덤프 · 병합)", ph["cards"], ph["rows_have_text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
