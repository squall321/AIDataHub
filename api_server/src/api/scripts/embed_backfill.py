# 임베딩 슬롯 백필 CLI — 운영자가 GPU 박스에서 큰 일회성 백필을 앞에 두고 돌린다(이어하기 · 진행 로그)
"""임베딩 슬롯 백필.

    cd api_server && PYTHONPATH=src .venv/bin/python -m api.scripts.embed_backfill --slot m3
    ... --slot m3 --table record_sections --limit 2000     # 일부만(시험 · 처리량 재기)
    ... --slot m3 --status                                 # 남은 행 수만(읽기 전용)

환경은 API 와 같아야 한다 — DATABASE_URL(``api_server/.env``)과 EMBEDDING_PROVIDER · EMBEDDING_DIM ·
AIDH_BGE_M3_*(``deploy/apptainer/.env``). ``deploy/apptainer/embed-slot.sh`` 가 둘을 실어 이 모듈을 부른다.

- 청크마다 커밋한다. 멈추면(Ctrl-C) 진행 중이던 청크만 버려지고, 다시 돌리면 빈 행부터 이어 간다.
- (슬롯, 표)마다 하나만 돈다(advisory lock). 인앱 스윕이 같은 슬롯 · 표를 채우는 중이면 그 표는 '잠김' 으로
  건너뛰고 종료 코드 3 을 낸다 — 스윕이 끝난 뒤 다시 돌리거나, 그 슬롯을 AIDH_EMBED_FILL 에서 빼고 API 를
  다시 띄운 뒤 돌린다.
- 결과는 표준 출력에 JSON 한 덩이(표마다 채움 · 빈 글 · 실패 · 한도 넘침 · 초 · 행/초), 진행은 표준 오류 로그다.

종료 코드: 0 끝 · 1 실패 행 또는 오류 · 2 인자 오류 · 3 잠겨서 건너뛴 표가 있음.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys


def _parser() -> argparse.ArgumentParser:
    from ..services import embed_backfill, embed_slots

    ap = argparse.ArgumentParser(prog="python -m api.scripts.embed_backfill", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slot", required=True, choices=embed_slots.SLOT_NAMES, help="채울 슬롯")
    ap.add_argument("--table", action="append", choices=embed_slots.TABLES,
                    help="채울 표(여러 번 줄 수 있다, 기본 넷 전부 — 작은 표 먼저, 절은 마지막)")
    ap.add_argument("--limit", type=int, default=0, help="표마다 이번에 볼 행 수 상한(0=전부)")
    ap.add_argument("--chunk", type=int, default=embed_backfill.DEFAULT_CHUNK,
                    help="한 번에 읽고 커밋하는 행 수")
    ap.add_argument("--batch", type=int, default=0,
                    help="모델 한 번에 넣는 글 수(0=공급자 기본 — bge_m3 는 AIDH_BGE_M3_BATCH)")
    ap.add_argument("--log-every", type=float, default=30.0, help="진행 로그 간격(초)")
    ap.add_argument("--status", action="store_true", help="남은 행 수만 보고 끝낸다(쓰지 않는다)")
    return ap


async def _status(slot_name: str, tables: list[str]) -> dict:
    from ..db import base as db_base
    from ..services import embed_backfill, embed_slots

    try:
        async with db_base.SessionLocal() as session:
            return await embed_backfill.backlog(session, [embed_slots.slot(slot_name)], tables)
    finally:
        await db_base.engine.dispose()


async def _fill(args: argparse.Namespace, tables: list[str]) -> dict:
    from ..db import base as db_base
    from ..services import embed_backfill, embed_slots

    try:
        return await embed_backfill.run(
            [embed_slots.slot(args.slot)], tables,
            limit=args.limit or None, chunk=args.chunk, batch=args.batch or None,
            log_every_s=args.log_every,
        )
    finally:
        await db_base.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("sqlalchemy", "api.db.base", "httpx", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)  # 풀 · 모델 적재 잡음은 빼고 진행만 남긴다
    args = _parser().parse_args(argv)
    from ..services import embed_slots

    if args.chunk <= 0 or args.limit < 0 or args.batch < 0:
        print("--chunk 는 1 이상, --limit · --batch 는 0 이상이어야 한다", file=sys.stderr)
        return 2
    tables = [t for t in embed_slots.TABLES if t in (args.table or embed_slots.TABLES)]

    if args.status:
        print(json.dumps(asyncio.run(_status(args.slot, tables)), ensure_ascii=False, indent=2))
        return 0
    try:
        res = asyncio.run(_fill(args, tables))
    except KeyboardInterrupt:
        print("중단 — 커밋된 청크는 남았다. 다시 돌리면 빈 행부터 이어 간다.", file=sys.stderr)
        return 1
    per = res.get(args.slot, {})
    for st in per.values():
        secs = float(st.get("seconds") or 0.0)
        st["rows_per_s"] = round(float(st.get("processed") or 0) / secs, 1) if secs > 0 else None
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    if any(st.get("error") or st.get("failed") for st in per.values()):
        return 1
    if any(st.get("locked") for st in per.values()):
        print("잠긴 표가 있다 — 다른 러너(인앱 스윕 · 다른 CLI)가 채우는 중이었다. 끝난 뒤 다시 돌린다.",
              file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
