# 임베딩 슬롯 m3 의 HNSW 색인을 백필 뒤에 CONCURRENTLY 로 짓는 CLI — 표의 쓰기를 막지 않는다
"""임베딩 슬롯 색인.

    cd api_server && PYTHONPATH=src .venv/bin/python -m api.scripts.embed_index --slot m3
    ... --slot m3 --maintenance-work-mem 8GB --workers 4     # 큰 표를 빨리
    ... --slot m3 --dry-run                                   # 지을 문장만 본다

왜 이행(alembic 0032)이 짓지 않나 — 빈 열에 색인을 먼저 지으면 백필의 행마다 색인 갱신이 붙어 느려지고, 일반
CREATE INDEX 는 짓는 동안 표의 쓰기를 막는다. 백필이 끝난 뒤 ``CREATE INDEX CONCURRENTLY`` 로 짓는다.

- 색인은 v1 과 같은 꼴이다 — HNSW(m=16, ef_construction=64) · ``halfvec_cosine_ops`` · ``WHERE 열 IS NOT NULL``
  (record_sections · records · mcp_uploads, agent_sample_embeddings 는 v1 처럼 짓지 않는다 — 행이 적다).
- 이미 있고 유효하면 건너뛴다. 실패한 CONCURRENTLY 가 남긴 INVALID 색인은 지우고 다시 짓는다.
- HNSW 그래프가 ``maintenance_work_mem`` 에 들어가야 빠르다(넘치면 pgvector 가 NOTICE 를 내고 크게 느려진다).
  1024차원 halfvec 109만 행은 약 2.5GB 다 — 기본값을 4GB 로 둔다(이 세션에만 건다). 진행은 다른 창에서
  ``SELECT phase, blocks_done, blocks_total, tuples_done, tuples_total FROM pg_stat_progress_create_index;``
- 색인이 없어도 검색은 맞게 돈다(정확 검색) — 전역 검색이 느릴 뿐이다. 좌석 범위 검색은 색인을 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time

# (표, 슬롯 열 → 색인 이름) — v1 의 색인 이름 꼴(idx_<표>_<열>_hnsw)을 따른다.
_INDEXES = {
    "m3": (
        ("record_sections", "embedding_m3", "idx_sections_embedding_m3_hnsw"),
        ("records", "signature_embedding_m3", "idx_records_signature_embedding_m3_hnsw"),
        ("mcp_uploads", "description_embedding_m3", "idx_mcp_uploads_desc_embedding_m3_hnsw"),
    ),
}

log = logging.getLogger("api.scripts.embed_index")


def _ddl(table: str, col: str, name: str) -> str:
    return (f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} "
            f"USING hnsw ({col} halfvec_cosine_ops) WITH (m = 16, ef_construction = 64) "
            f"WHERE {col} IS NOT NULL")


async def _build(args: argparse.Namespace) -> int:
    from sqlalchemy import text

    from ..db import base as db_base

    targets = [t for t in _INDEXES[args.slot] if not args.table or t[0] in args.table]
    if args.dry_run:
        for table, col, name in targets:
            print(_ddl(table, col, name) + ";")
        return 0
    engine = db_base.engine
    rc = 0
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")  # CONCURRENTLY 는 트랜잭션 밖
            # SET 은 바인드를 받지 않는다 — 꼴을 검사한 값만 박는다.
            await conn.execute(text(f"SET maintenance_work_mem = '{args.maintenance_work_mem}'"))
            await conn.execute(text(f"SET max_parallel_maintenance_workers = {int(args.workers)}"))
            for table, col, name in targets:
                row = (await conn.execute(text(
                    "SELECT i.indisvalid FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
                    "WHERE c.relname = :n"), {"n": name})).first()
                if row is not None and row[0]:
                    log.info("%s: 이미 있다(유효) — 건너뛴다", name)
                    continue
                if row is not None:
                    log.warning("%s: INVALID(앞선 CONCURRENTLY 실패의 잔해) — 지우고 다시 짓는다", name)
                    await conn.execute(text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))
                n = (await conn.execute(text(f"SELECT count(*) FROM {table} WHERE {col} IS NOT NULL"))).scalar()
                log.info("%s: %s 행으로 짓는다 — %s", name, n, _ddl(table, col, name))
                t0 = time.monotonic()
                try:
                    await conn.execute(text(_ddl(table, col, name)))
                except Exception as exc:  # noqa: BLE001 — 다음 색인은 계속 짓는다
                    rc = 1
                    log.error("%s: 실패 — %s (INVALID 로 남았으면 다음 실행이 지우고 다시 짓는다)", name, exc)
                    continue
                size = (await conn.execute(text("SELECT pg_size_pretty(pg_relation_size(CAST(:n AS regclass)))"),
                                           {"n": name})).scalar()
                log.info("%s: 지었다 — %.0f초, %s", name, time.monotonic() - t0, size)
    finally:
        await engine.dispose()
    return rc


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("sqlalchemy", "api.db.base", "httpx", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)  # 풀 · 모델 적재 잡음은 빼고 진행만 남긴다
    ap = argparse.ArgumentParser(prog="python -m api.scripts.embed_index", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slot", required=True, choices=sorted(_INDEXES), help="색인을 지을 슬롯(v1 은 이행이 지었다)")
    ap.add_argument("--table", action="append", choices=[t[0] for t in _INDEXES["m3"]], help="표(기본 전부)")
    ap.add_argument("--maintenance-work-mem", default="4GB", help="이 세션의 maintenance_work_mem(예 4GB · 512MB)")
    ap.add_argument("--workers", type=int, default=2, help="이 세션의 max_parallel_maintenance_workers")
    ap.add_argument("--dry-run", action="store_true", help="지을 문장만 출력한다")
    args = ap.parse_args(argv)
    if not re.fullmatch(r"\d+(kB|MB|GB)", args.maintenance_work_mem):
        print("--maintenance-work-mem 은 숫자 + kB|MB|GB 꼴이어야 한다(예 4GB)", file=sys.stderr)
        return 2
    if not 0 <= args.workers <= 64:
        print("--workers 는 0~64", file=sys.stderr)
        return 2
    return asyncio.run(_build(args))


if __name__ == "__main__":
    raise SystemExit(main())
