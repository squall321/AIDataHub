# 한국어 검색 평가셋으로 지금 검색(어휘 · 의미 · 하이브리드 · agent_search)의 기준선을 재는 실행기 — 이어하기, 집계.
"""한국어 검색 기준선 실행기.

하는 일
  run     ``queries.jsonl`` 의 질의를 모드마다 한 번씩 AIDataHub MCP 로 부르고, 정답 카드가 몇 번째 히트로
          나왔는지와 지연을 ``results.jsonl`` 에 쌓는다. 이미 잰 (질의, 모드)는 건너뛴다(이어하기).
  report  ``results.jsonl`` 을 모아 모드별 hit@1·5·10, MRR@10, 지연 p50·p95 를 내고 질의 꼴 · 카드 언어 · 제목 겹침 ·
          임베딩 유무 · 동명 카드 · 좌석 크기 · 도메인으로 나눠 본다. 제목을 베낀 질의는 제목 겹침 나눔에서만 센다.
          질의 글자는 싣지 않는다(``summary.json`` 은 숫자만).

모드(전부 지금 서버의 MCP 도구를 그대로 부른다 — 검색 코드를 흉내 내지 않는다)
  fts · semantic · hybrid   전역 ``fts_search`` · ``semantic_search`` · ``hybrid_search`` (top_k 10)
  agent_fts · agent_semantic · agent_hybrid   정답 카드가 묶인 전문가의 ``agent_search`` (top_k 10)
  agent_seat   ``agent_search(mode=hybrid)`` 를 top_k 없이 — 심의 · 챗 · 띵킹의 좌석이 실제로 받는 것(좌석 기본 6건)

순위는 돌아온 히트 목록(절 단위)에서 정답 카드의 절이 처음 나온 자리다. 같은 제목의 다른 카드를 맞힌 것도
정답으로 치는 느슨한 순위(``rank_same_title``)를 함께 남긴다 — 정답이 하나라는 가정의 거짓 음성을 가늠하려고.

부하는 순차 한 줄이다(``--pause`` 초씩 쉰다). 읽기만 하는 검색 도구만 부른다.

실행(리포 루트에서)
  api_server/.venv/bin/python api_server/scripts/ko_search_eval/run_baseline.py run
  api_server/.venv/bin/python api_server/scripts/ko_search_eval/run_baseline.py report
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DEFAULT_DATA = Path(__file__).resolve().parent / "data"
TOP_K = 10


def _args_global(q: dict) -> dict:
    return {"q": q["query"], "top_k": TOP_K}


def _args_agent(mode: str, top_k: int | None):
    def f(q: dict) -> dict:
        a = {"agent_type": q["agent_type"], "q": q["query"], "mode": mode}
        if top_k:
            a["top_k"] = top_k
        return a
    return f


MODES: dict[str, tuple[str, object]] = {
    "fts": ("fts_search", _args_global),
    "semantic": ("semantic_search", _args_global),
    "hybrid": ("hybrid_search", _args_global),
    "agent_fts": ("agent_search", _args_agent("fts", TOP_K)),
    "agent_semantic": ("agent_search", _args_agent("semantic", TOP_K)),
    "agent_hybrid": ("agent_search", _args_agent("hybrid", TOP_K)),
    "agent_seat": ("agent_search", _args_agent("hybrid", None)),
}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _hits(res) -> tuple[list[dict], dict]:
    """도구 결과 → (히트 목록, 덧붙일 값). 목록 도구는 {"result": [...]}, agent_search 는 {"hits": [...]} 다.

    agent_search 면 refused 와 좌석의 레코드 수(scope)를 덧붙인다 — 좌석이 크면 범위 의미검색이 HNSW 색인을 타고
    범위 밖 후보를 걸러 내며 히트를 잃으므로(2026-10-16 EXPLAIN 실측) 좌석 크기로 나눠 본다.
    """
    sc = res.structuredContent
    if not isinstance(sc, dict):
        items = [json.loads(c.text) for c in res.content if getattr(c, "type", "") == "text"]
        sc = items[0] if len(items) == 1 and isinstance(items[0], dict) and "hits" in items[0] else {"result": items}
    if "hits" in sc:
        scope = (sc.get("applied_config") or {}).get("agent_record_scope")
        return list(sc.get("hits") or []), {"refused": bool(sc.get("refused")),
                                             "scope": scope if isinstance(scope, int) else None}
    return list(sc.get("result") or []), {}


def _rank(hits: list[dict], gold: str, gold_title: str) -> tuple[int | None, int | None]:
    strict = next((i for i, h in enumerate(hits, 1) if h.get("record_id") == gold), None)
    loose = next((i for i, h in enumerate(hits, 1)
                  if h.get("record_id") == gold or (gold_title and h.get("title") == gold_title)), None)
    return strict, loose


async def run(args: argparse.Namespace) -> int:
    queries = _read_jsonl(args.data_dir / "queries.jsonl")
    if not queries:
        print(f"질의가 없다 — 먼저 gen_queries.py 를 돌려라({args.data_dir})", file=sys.stderr)
        return 2
    out_path = args.data_dir / "results.jsonl"
    prev = _read_jsonl(out_path)
    done = {(r["qid"], r["mode"]) for r in prev if not (args.retry_errors and r.get("error"))}
    modes = [m for m in (args.modes.split(",") if args.modes else MODES) if m]
    unknown = [m for m in modes if m not in MODES]
    if unknown:
        print(f"모르는 모드 {unknown} — 쓸 수 있는 것 {list(MODES)}", file=sys.stderr)
        return 2
    todo = [(q, m) for q in queries for m in modes if (q["qid"], m) not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"잴 것 {len(todo)}건(질의 {len(queries)} × 모드 {len(modes)}, 이미 잰 것 제외) → {out_path}",
          file=sys.stderr)

    t0 = time.monotonic()
    async with (
        httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(args.timeout)) as hc,
        streamable_http_client(args.mcp_url, http_client=hc) as (rd, wr, _),
        ClientSession(rd, wr) as session,
    ):
        await session.initialize()
        with out_path.open("a", encoding="utf-8") as f:
            for n, (q, mode) in enumerate(todo, 1):
                tool, build = MODES[mode]
                row = {"qid": q["qid"], "mode": mode}
                t = time.perf_counter()
                try:
                    res = await session.call_tool(tool, build(q))
                    row["ms"] = round((time.perf_counter() - t) * 1000, 1)
                    if res.isError:
                        txt = " ".join(getattr(c, "text", "") for c in res.content)
                        row["error"] = txt.strip()[:300] or "isError"
                    else:
                        hits, extra = _hits(res)
                        row["rank"], row["rank_same_title"] = _rank(hits, q["record_id"],
                                                                    q.get("gold_title", ""))
                        row["n_hits"] = len(hits)
                        row.update(extra)
                except Exception as exc:  # noqa: BLE001 — 한 건의 실패가 측정 전체를 멈추지 않게 남기고 간다
                    row["ms"] = round((time.perf_counter() - t) * 1000, 1)
                    row["error"] = f"{type(exc).__name__}: {str(exc)[:280]}"
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                if n % 100 == 0:
                    print(f"  {n}/{len(todo)} ({time.monotonic() - t0:.0f}초)", file=sys.stderr)
                if args.pause:
                    await asyncio.sleep(args.pause)
    print(f"끝 — {len(todo)}건, {time.monotonic() - t0:.0f}초", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------
def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))]


def _metrics(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r.get("error")]
    n = len(ok)
    ranks = [r.get("rank") for r in ok]
    loose = [r.get("rank_same_title") for r in ok]
    ms = [r["ms"] for r in ok if "ms" in r]

    def hit(k: int, rs=ranks) -> float | None:
        return round(sum(1 for x in rs if x is not None and x <= k) / n, 4) if n else None

    h10 = hit(10)
    return {
        "n": n, "errors": len(rows) - n,
        "hit1": hit(1), "hit5": hit(5), "hit10": h10,
        "hit10_ci95": round(1.96 * math.sqrt(h10 * (1 - h10) / n), 4) if n else None,
        "mrr10": round(sum(1 / x for x in ranks if x is not None and x <= 10) / n, 4) if n else None,
        "hit10_same_title": hit(10, loose),
        "refused": sum(1 for r in ok if r.get("refused")),
        "p50_ms": round(_pct(ms, 50)) if ms else None, "p95_ms": round(_pct(ms, 95)) if ms else None,
    }


def _bucket_overlap(q: dict) -> str:
    if q.get("title_copy"):
        return "copy"
    return "partial" if (q.get("title_overlap") or 0) >= 0.4 else "low"


def _bucket_embed(q: dict) -> str:
    ne, ns = q.get("n_embedded") or 0, q.get("n_sections") or 0
    return "none" if ne == 0 else ("all" if ne >= ns else "partial")


def _bucket_seat(q: dict) -> str:
    n = q.get("_scope")
    if n is None:
        return "?"
    return "~100" if n <= 100 else ("101~300" if n <= 300 else "301~")


GROUPS = {
    "style": lambda q: q["style"],
    "card_lang": lambda q: q["card_lang"],
    "title_overlap": _bucket_overlap,
    "embedded": _bucket_embed,
    "dup_title": lambda q: "dup" if q.get("dup_title") else "unique",
    "seat_size": _bucket_seat,
    "domain": lambda q: q["domain"],
}


def _paired(by_q: dict[str, dict[str, dict]], a: str, b: str) -> dict:
    """같은 질의에서 a 만 맞힌 수 · b 만 맞힌 수(hit@10)와 맥니마 z — 둘의 차가 우연인지 가늠한다."""
    only_a = only_b = 0
    for per in by_q.values():
        ra, rb = per.get(a), per.get(b)
        if not ra or not rb or ra.get("error") or rb.get("error"):
            continue
        ha = ra.get("rank") is not None and ra["rank"] <= 10
        hb = rb.get("rank") is not None and rb["rank"] <= 10
        only_a += ha and not hb
        only_b += hb and not ha
    z = (only_a - only_b) / math.sqrt(only_a + only_b) if only_a + only_b else 0.0
    return {"a": a, "b": b, "only_a": only_a, "only_b": only_b, "z": round(z, 2)}


def _fmt(v) -> str:
    if v is None:
        return "—"
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def report(args: argparse.Namespace) -> int:
    queries = {q["qid"]: q for q in _read_jsonl(args.data_dir / "queries.jsonl")}
    latest: dict[tuple[str, str], dict] = {}    # --retry-errors 로 다시 잰 것은 나중 줄이 이긴다
    for r in _read_jsonl(args.data_dir / "results.jsonl"):
        if r["qid"] in queries:
            latest[(r["qid"], r["mode"])] = r
    results = list(latest.values())
    if not results:
        print("결과가 없다 — 먼저 run 을 돌려라", file=sys.stderr)
        return 2
    for r in results:                       # 좌석 크기는 agent_search 응답에서 온다(질의마다 같은 좌석)
        if r.get("scope") is not None:
            queries[r["qid"]].setdefault("_scope", r["scope"])
    # 머리 숫자와 나눔은 제목을 베낀 질의를 뺀다(어휘 검색을 거저 맞힌다). 베낌은 제목 겹침 나눔에서만 따로 본다.
    keep = [r for r in results if not queries[r["qid"]].get("title_copy")]
    by_mode: dict[str, list[dict]] = defaultdict(list)
    by_q: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in keep:
        by_mode[r["mode"]].append(r)
        by_q[r["qid"]][r["mode"]] = r
    modes = [m for m in MODES if any(r["mode"] == m for r in results)]

    summary: dict = {
        "queries": len(queries), "cards": len({q["record_id"] for q in queries.values()}),
        "title_copy_queries": sum(1 for q in queries.values() if q.get("title_copy")),
        "overall": {m: _metrics(by_mode[m]) for m in modes},
        "overall_with_copy": {m: _metrics([r for r in results if r["mode"] == m]) for m in modes},
        "groups": {}, "paired": [],
        "eval_set": {g: dict(sorted(_count(queries.values(), fn).items())) for g, fn in GROUPS.items()},
    }
    for g, fn in GROUPS.items():
        cell: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for r in (results if g == "title_overlap" else keep):
            cell[fn(queries[r["qid"]])][r["mode"]].append(r)
        summary["groups"][g] = {k: {m: _metrics(v[m]) for m in modes if m in v} for k, v in sorted(cell.items())}
    for a, b in (("semantic", "fts"), ("hybrid", "semantic"), ("hybrid", "fts"),
                 ("agent_semantic", "agent_fts"), ("agent_hybrid", "agent_semantic"), ("agent_hybrid", "agent_fts"),
                 ("agent_seat", "agent_hybrid")):
        if a in by_mode and b in by_mode:
            summary["paired"].append(_paired(by_q, a, b))
    (args.data_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    cols = ("n", "hit1", "hit5", "hit10", "hit10_ci95", "mrr10", "hit10_same_title", "p50_ms", "p95_ms",
            "errors", "refused")
    print(f"질의 {summary['queries']}개(제목 베낌 {summary['title_copy_queries']}개) · 카드 {summary['cards']}장")
    for key, label in (("overall", "제목 베낌 제외"), ("overall_with_copy", "베낌 포함")):
        print(f"\n### 모드별 — {label}\n")
        print("| 모드 | " + " | ".join(cols) + " |")
        print("|---" * (len(cols) + 1) + "|")
        for m in modes:
            o = summary[key][m]
            print(f"| {m} | " + " | ".join(_fmt(o[c]) for c in cols) + " |")
    for g, cells in summary["groups"].items():
        print(f"\n### {g} — hit@10 / MRR@10 (n){'' if g == 'title_overlap' else ', 제목 베낌 제외'}\n")
        print("| 값 | " + " | ".join(modes) + " |")
        print("|---" * (len(modes) + 1) + "|")
        for k, per in cells.items():
            n = max((v["n"] for v in per.values()), default=0)
            print(f"| {k} (n={n}) | " + " | ".join(
                f"{_fmt(per[m]['hit10'])} / {_fmt(per[m]['mrr10'])}" if m in per else "—" for m in modes) + " |")
    print("\n### 짝 비교(hit@10, 같은 질의, 제목 베낌 제외)\n")
    for p in summary["paired"]:
        print(f"- {p['a']} 만 {p['only_a']} · {p['b']} 만 {p['only_b']} · z {p['z']}")
    return 0


def _count(qs, fn) -> dict[str, int]:
    c: dict[str, int] = defaultdict(int)
    for q in qs:
        c[fn(q)] += 1
    return c


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("run", "report"))
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--mcp-url", default="http://127.0.0.1:8001/mcp/", help="AIDataHub MCP 끝점(게이트웨이를 거치지 않는다)")
    ap.add_argument("--modes", default="", help="쉼표로 고른 모드(기본 전부)")
    ap.add_argument("--limit", type=int, default=0, help="이번 실행에서 잴 (질의, 모드) 수 상한(0=전부)")
    ap.add_argument("--pause", type=float, default=0.05, help="호출 사이 쉬는 초")
    ap.add_argument("--timeout", type=float, default=120.0, help="호출 하나의 제한(초)")
    ap.add_argument("--retry-errors", action="store_true", help="오류로 남은 (질의, 모드)를 다시 잰다")
    args = ap.parse_args()
    return asyncio.run(run(args)) if args.cmd == "run" else report(args)


if __name__ == "__main__":
    sys.exit(main())
