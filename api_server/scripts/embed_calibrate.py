# 임베딩 슬롯 문턱 보정 — DB(읽기 전용)에서 v1 · m3 벡터가 둘 다 있는 표본으로 날 코사인 문턱을 분위로 옮겨 본다
"""임베딩 슬롯 문턱 보정.

e5(v1)는 무관한 글끼리도 코사인 0.7~0.9 라 그 분포로 정한 날 코사인 문턱(시그니처 확신 0.93 · 0.88, 군집 0.85)은
BGE-M3(m3)에서 뜻이 바뀐다. 같은 표본에서 e5 문턱이 가르는 몫(분위)을 BGE-M3 분포에서 같은 몫이 되는 값으로
옮긴다 — 결정(높음 · 중간 · 같은 그룹)의 비율을 지금과 같게 두는 값이다. ``api.services.embed_slots.THRESHOLDS``
의 m3 값이 이 방법으로 잰 것이다(2026-10-16, 절 8,905 · 시그니처 6,029 · 질의 397 의 표본).

m3 백필이 끝난 뒤 dev 전체에서 다시 재려면(쓰지 않는다 — 세션이 read-only 다)

    cd api_server && PYTHONPATH=src HF_HUB_OFFLINE=1 .venv/bin/python scripts/embed_calibrate.py \\
        --queries /data/svc/aidh/eval/ko-search-20261016/queries.jsonl

질의(--queries)를 주면 두 모델로 질의를 인코딩해 질의–절 점수 분포(검색 점수는 (1+cos)/2 — 에이전트별
score_threshold 가 거르는 몫)와 히트 절 쌍(군집 build_query_groups)도 잰다. 모델 둘을 올린다(GPU 가 있으면 GPU).
DATABASE_URL 은 api_server/.env, EMBEDDING_PROVIDER · EMBEDDING_DIM 은 deploy/apptainer/.env 를 따른다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import numpy as np

CURRENT_V1 = {"signature_high": 0.93, "signature_medium": 0.88, "cluster_sim": 0.85, "cluster_record_sim": 0.85}


def _vec(txt: str) -> np.ndarray:
    return np.asarray(txt[1:-1].split(","), dtype="float32")


def _norm(rows: list[str]) -> np.ndarray:
    m = np.stack([_vec(t) for t in rows])
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def _pct(a) -> dict:
    a = np.asarray(a, dtype="float64")
    return {str(p): round(float(np.percentile(a, p)), 4) for p in (0, 1, 5, 25, 50, 75, 95, 99, 100)}


def _qmap(src, dst, thr: float) -> dict:
    """src 에서 thr 이상의 몫 → dst 에서 같은 몫이 되는 값."""
    src = np.asarray(src)
    frac = float((src >= thr).mean())
    val = None if frac <= 0 else round(float(np.quantile(np.asarray(dst), 1 - frac)), 4)
    return {"v1": thr, "share_ge_v1": round(frac, 5), "m3": val}


async def _fetch(args) -> tuple[list, list, list]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from api.config import settings

    url = settings.database_url
    kw = {"connect_args": {"server_settings": {"default_transaction_read_only": "on"}}} \
        if url.startswith("postgresql+asyncpg") else {}
    eng = create_async_engine(url, **kw)
    try:
        async with eng.connect() as c:
            total = (await c.execute(text("SELECT reltuples FROM pg_class WHERE relname = 'record_sections'"))).scalar()
            pct = min(100.0, 100.0 * args.sections / max(float(total or 1), 1.0))
            secs = (await c.execute(text(
                f"SELECT s.record_id, s.embedding::text AS v1, s.embedding_m3::text AS m3 FROM record_sections s "
                f"TABLESAMPLE SYSTEM ({pct:.4f}) REPEATABLE ({args.seed}) "
                "WHERE s.embedding IS NOT NULL AND s.embedding_m3 IS NOT NULL"))).all()
            rtotal = (await c.execute(text("SELECT reltuples FROM pg_class WHERE relname = 'records'"))).scalar()
            rpct = min(100.0, 100.0 * args.records / max(float(rtotal or 1), 1.0))
            recs = (await c.execute(text(
                f"SELECT r.id, r.data_type, r.signature_embedding::text AS v1, r.signature_embedding_m3::text AS m3 "
                f"FROM records r TABLESAMPLE SYSTEM ({rpct:.4f}) REPEATABLE ({args.seed}) "
                "WHERE r.deleted_at IS NULL AND r.signature_embedding IS NOT NULL "
                "AND r.signature_embedding_m3 IS NOT NULL"))).all()
            firsts = (await c.execute(text(
                "SELECT DISTINCT ON (s.record_id) s.record_id, s.embedding::text AS v1, s.embedding_m3::text AS m3 "
                "FROM record_sections s WHERE s.record_id = ANY(:ids) AND s.embedding IS NOT NULL "
                "AND s.embedding_m3 IS NOT NULL ORDER BY s.record_id, s.level, s.id"),
                {"ids": [r.id for r in recs]})).all()
    finally:
        await eng.dispose()
    return secs, recs, firsts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sections", type=int, default=9000, help="표본 절 수(대략 — 표 쪽수로 뽑는다)")
    ap.add_argument("--records", type=int, default=6000, help="표본 레코드 수(대략)")
    ap.add_argument("--queries", default="", help="질의 jsonl(필드 query, title_copy 는 뺀다) — 주면 모델 둘을 올린다")
    ap.add_argument("--seed", type=int, default=20261016)
    args = ap.parse_args(argv)

    secs, recs, firsts = asyncio.run(_fetch(args))
    if len(secs) < 100 or len(recs) < 100:
        print(f"두 슬롯 벡터가 다 있는 표본이 너무 적다(절 {len(secs)} · 레코드 {len(recs)}) — m3 백필 뒤에 돌린다",
              file=sys.stderr)
        return 2
    out: dict = {"n_sections": len(secs), "n_records": len(recs), "n_first_sections": len(firsts)}
    rng = np.random.default_rng(args.seed)

    # 시그니처 최근접 이웃(같은 data_type) — find_similar_data 의 확신 문턱
    g1, g3 = _norm([r.v1 for r in recs]), _norm([r.m3 for r in recs])
    dts = np.array([r.data_type for r in recs])
    top1 = {}
    for name, g in (("v1", g1), ("m3", g3)):
        m = g @ g.T
        np.fill_diagonal(m, -2)
        m[dts[:, None] != dts[None, :]] = -2
        top1[name] = m.max(axis=1)
    out["signature_top1_cos"] = {k: _pct(v) for k, v in top1.items()}
    out["signature_high"] = _qmap(top1["v1"], top1["m3"], CURRENT_V1["signature_high"])
    out["signature_medium"] = _qmap(top1["v1"], top1["m3"], CURRENT_V1["signature_medium"])

    # 레코드 첫 절끼리 — cluster_around_record
    f1, f3 = _norm([r.v1 for r in firsts]), _norm([r.m3 for r in firsts])
    sub = rng.choice(len(firsts), size=min(1500, len(firsts)), replace=False)
    a1, a3 = f1[sub] @ f1.T, f3[sub] @ f3.T
    mask = np.ones_like(a1, dtype=bool)
    mask[np.arange(len(sub)), sub] = False
    out["cluster_record_sim"] = _qmap(a1[mask], a3[mask], CURRENT_V1["cluster_record_sim"])

    if args.queries:
        from api.services import embedding

        qs = [json.loads(ln) for ln in open(args.queries, encoding="utf-8") if ln.strip()]
        qs = [q["query"] for q in qs if not q.get("title_copy")]
        s1, s3 = _norm([r.v1 for r in secs]), _norm([r.m3 for r in secs])
        rid = np.array([r.record_id for r in secs])
        e5 = embedding.get_embedder()
        m3 = embedding.get_embedder_for("bge_m3")
        q1 = np.asarray([e5.encode_query(q) for q in qs], dtype="float32")
        q3 = np.asarray([m3.encode_query(q) for q in qs], dtype="float32")
        c1, c3 = q1 @ s1.T, q3 @ s3.T
        out["query_section_cos"] = {"v1": _pct(c1.ravel()), "m3": _pct(c3.ravel())}
        # 검색 점수 (1+cos)/2 문턱이 거르는 몫 — score_threshold 0.3 · 0.5, 군집 min_score 0.4
        out["score_below"] = {f"{thr}": {"v1": float((((1 + c1) / 2) < thr).mean()),
                                         "m3": float((((1 + c3) / 2) < thr).mean())} for thr in (0.3, 0.4, 0.5)}
        p1, p3 = [], []
        for i in range(len(qs)):
            idx: set[int] = set()
            for c in (c1, c3):
                seen: set[str] = set()
                for j in np.argsort(-c[i]):
                    if rid[j] in seen:
                        continue
                    seen.add(rid[j])
                    idx.add(int(j))
                    if len(seen) >= 50:
                        break
            ix = sorted(idx)
            iu = np.triu_indices(len(ix), 1)
            p1.append((s1[ix] @ s1[ix].T)[iu])
            p3.append((s3[ix] @ s3[ix].T)[iu])
        out["cluster_sim"] = _qmap(np.concatenate(p1), np.concatenate(p3), CURRENT_V1["cluster_sim"])

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
