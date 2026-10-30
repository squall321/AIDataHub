# record_sections 를 records.content 에서 다시 짓는 CLI — 평탄화가 하위 절(subsections)을 빠뜨린 카드만 고친다
"""절 색인 다시 짓기 — MX 백서 하위 절(AD-01).

    cd api_server && PYTHONPATH=src .venv/bin/python -m api.scripts.reindex_sections          # 셈만(쓰지 않는다)
    ... --apply                                                                               # 다시 짓는다

박스에서는 ``bash deploy/apptainer/reindex-sections.sh [--apply]`` 로 부른다 — API 와 같은 환경(DATABASE_URL ·
AIDH_CHUNK_*)이라야 적재가 짓는 것과 같은 행이 나온다.

왜 필요한가 — 평탄화(``api.ingest.db_writer._flatten_sections``)가 children 만 내려가 MX 백서 DocumentJSON 의
subsections 를 건너뛰었다. 하위 절의 글이 record_sections 에 없어 의미검색 · FTS · get_record_sections(챗 · 심의
근거)에서 빠졌고, 1번 절이 글 없는 「본문」 래퍼인 카드는 빈 절로 보였다. 평탄화를 고쳐도 이미 적재된 카드는 그대로다
— 적재는 본문 해시 · 제목 · 요약이 같으면 건너뛰고(write_record), 운영 박스는 동기화를 받지 않는다.

무엇을 다시 짓나(그 밖은 건드리지 않는다):
    살아 있는 DOC 레코드 가운데 content 의 절 트리에 비지 않은 subsections 가 있고, 지금의 record_sections 행이
    고친 평탄화가 content 에서 짓는 행과 다른 것. 그 레코드의 절만 적재와 같은 함수(``_resync_sections`` — 지우고
    다시 넣는다)로 다시 짓는다. 다시 돌리면 이미 맞는 레코드는 건너뛴다(멱등).
    - records 행은 쓰지 않는다 — updated_at · content_hash 가 그대로라 박스 사이 병합의 우선순위가 바뀌지 않는다.
    - subsections 가 없는 레코드는 행이 어떻든 보지 않는다 — 예전 평탄화와 결과가 같다. content 가 동기화 폴백(빈
      「본문」 한 절)인 카드도 여기에 든다 — 원천을 다시 받아야 하고, 이 명령으로는 고쳐지지 않는다.
    - 다시 지은 절은 임베딩이 비어 있다. API 의 임베딩 스윕(AIDH_EMBED_SWEEP_MIN, 기본 30분)이 채우고, 곧바로 채우려면
      ``bash deploy/apptainer/embed-slot.sh fill --slot <활성 슬롯> --table record_sections`` 다.
    - 후보를 고르느라 DOC 레코드의 content 를 한 번 훑는다(DB 안에서 글자로 거르고 맞는 것만 읽는다).

결과는 표준 출력에 JSON 한 덩이다 — candidates(subsections 가 든 레코드) · affected(다시 지을 · 지은 레코드) ·
rows(그 레코드들의 절 행 — before · after · added · removed · changed) · chars(절 글자 수 전 · 후) · textless(글 있는
절이 하나도 없는 레코드 전 · 후) · dropped_after(고친 뒤에도 색인에서 빠지는 절 — 4단계 이하 · 중복 · 식별자 없음) ·
sample(앞 20개 id) · rebuilt(--apply 에서 다시 지은 수).

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


async def run(maker: Any, *, apply: bool, chunk: int = _CHUNK) -> dict[str, Any]:
    """다시 지을 레코드를 세고(``apply`` 면 다시 짓고) 결과를 돌려준다. ``maker`` 는 async_sessionmaker 다."""
    from sqlalchemy import Text, cast, select

    from ..db.models import Record, RecordSection
    from ..ingest.db_writer import _resync_sections, _section_rows

    out: dict[str, Any] = {
        "mode": "apply" if apply else "dry-run", "candidates": 0, "affected": 0, "rebuilt": 0,
        "rows": dict.fromkeys(("before", "after", "added", "removed", "changed"), 0),
        "chars": {"before": 0, "after": 0}, "textless": {"before": 0, "after": 0},
        "dropped_after": {}, "sample": [],
    }
    live_doc = (Record.data_type == "DOC", Record.deleted_at.is_(None))
    async with maker() as session:
        # DB 안에서 글자로 먼저 거른다 — 글 속 낱말 · 절 트리 밖의 같은 키는 아래 _has_subsections 가 뺀다.
        ids = (await session.execute(
            select(Record.id).where(*live_doc, cast(Record.content, Text).like('%"subsections"%'))
            .order_by(Record.id)
        )).scalars().all()

    cols = (RecordSection.record_id, RecordSection.section_id, RecordSection.level, RecordSection.title,
            RecordSection.content_text, RecordSection.figure_refs, RecordSection.table_refs,
            RecordSection.section_path, RecordSection.parent_section_id, RecordSection.chunk_index)
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        async with maker() as session:
            q = select(Record.id, Record.data_type, Record.content).where(Record.id.in_(part), *live_doc)
            if apply:
                # 그사이 같은 레코드를 적재가 고치면 그 적재는 이 묶음 뒤에 쓴다 — 옛 content 로 새 절을 덮지 않는다.
                q = q.with_for_update()
            recs = (await session.execute(q.order_by(Record.id))).all()
            have: dict[str, dict[str, _Row]] = {}
            for r in (await session.execute(select(*cols).where(RecordSection.record_id.in_(part)))).mappings():
                have.setdefault(r["record_id"], {})[r["section_id"]] = _row(r)

            rebuilt = 0
            for rid, data_type, content in recs:
                if not isinstance(content, dict) or not _has_subsections(content.get("sections")):
                    continue
                out["candidates"] += 1
                dropped: dict[str, int] = {}
                want = {r["section_id"]: _row(r) for r in _section_rows(content, dropped)}
                cur = have.get(rid, {})
                if want == cur:
                    continue
                out["affected"] += 1
                rows = out["rows"]
                rows["before"] += len(cur)
                rows["after"] += len(want)
                rows["added"] += len(want.keys() - cur.keys())
                rows["removed"] += len(cur.keys() - want.keys())
                rows["changed"] += sum(1 for k in want.keys() & cur.keys() if want[k] != cur[k])
                out["chars"]["before"] += sum(len(v.text) for v in cur.values())
                out["chars"]["after"] += sum(len(v.text) for v in want.values())
                out["textless"]["before"] += not any(v.text for v in cur.values())
                out["textless"]["after"] += not any(v.text for v in want.values())
                for k, v in dropped.items():
                    out["dropped_after"][k] = out["dropped_after"].get(k, 0) + v
                if len(out["sample"]) < _SAMPLE:
                    out["sample"].append(rid)
                if apply:
                    # _resync_sections 는 record.id 와 record_in.data_type · content 만 읽는다 — records 행은 쓰지 않는다.
                    rec = SimpleNamespace(id=rid, data_type=data_type, content=content)
                    await _resync_sections(session, rec, rec, RecordSection)
                    rebuilt += 1
            if apply:
                await session.commit()
                out["rebuilt"] += rebuilt
    return out


async def _main(apply: bool) -> dict[str, Any]:
    from ..db import base as db_base

    try:
        return await run(db_base.SessionLocal, apply=apply)
    finally:
        await db_base.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("sqlalchemy", "api.db.base"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(prog="python -m api.scripts.reindex_sections", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="다시 짓는다(없으면 셈만 하고 쓰지 않는다)")
    args = ap.parse_args(argv)
    try:
        res = asyncio.run(_main(args.apply))
    except Exception:  # noqa: BLE001 — 커밋한 묶음은 남는다
        log.exception("실패 — 커밋한 묶음은 남았다. 다시 돌리면 남은 것만 한다")
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if res["rebuilt"]:
        log.info("다시 지은 절의 임베딩은 API 의 임베딩 스윕(AIDH_EMBED_SWEEP_MIN, 기본 30분)이 채운다 — 곧바로는 "
                 "bash deploy/apptainer/embed-slot.sh fill --slot <활성 슬롯> --table record_sections")
    elif res["affected"]:
        log.info("셈만 했다(쓰지 않았다) — 다시 지으려면 --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
