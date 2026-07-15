#!/usr/bin/env python3
# AI Data Hub — sync export(JSONL) → 운영서버 upsert import (records + agents).
"""머지 동기화의 import 측. 표준 라이브러리만 사용(운영서버에 requests 불필요).

records → POST /api/records/import (배치 ≤1000, db_writer 멱등 upsert, auto_seq=false 로
          소스 id 보존). agents → POST /api/agents, 409(기존)면 PATCH.
--dry-run: 실제 쓰기 없이 계획만(records 는 서버 dry_run, agents 는 존재여부로 예측).
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import urllib.error
import urllib.request

BATCH_MAX = 1000


def read_jsonl_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def chunk(iterable, n):
    buf = []
    for x in iterable:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def _req(method, url, key, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except urllib.error.URLError as e:
        return 0, {"error": str(e)}


def import_records(base, key, path, dry_run, batch):
    agg = {"total": 0, "inserted": 0, "updated": 0, "skipped": 0, "dry_run": 0, "error": 0}
    batch = min(max(1, batch), BATCH_MAX)
    for i, b in enumerate(chunk(read_jsonl_gz(path), batch), 1):
        agg["total"] += len(b)
        # auto_seq=false → 소스 id 보존(인용 안정성). external_source 로 출처 표시.
        st, resp = _req("POST", f"{base}/api/records/import", key,
                        {"records": b, "dry_run": dry_run, "auto_seq": False,
                         "external_source": "hub-sync"})
        if st != 200:
            agg["error"] += len(b)
            print(f"  [records batch {i}] HTTP {st}: {str(resp)[:200]}", file=sys.stderr)
            continue
        for r in resp.get("results", []):
            if r.get("error"):
                agg["error"] += 1
            else:
                a = r.get("action", "dry_run")
                agg[a] = agg.get(a, 0) + 1
        print(f"  records batch {i}: +{len(b)} (누적 {agg['total']})")
    return agg


def import_agents(base, key, path, dry_run):
    agg = {"total": 0, "created": 0, "updated": 0, "error": 0}
    for a in read_jsonl_gz(path):
        agg["total"] += 1
        at = a.get("agent_type")
        if not at:
            agg["error"] += 1
            continue
        if dry_run:
            st, _ = _req("GET", f"{base}/api/agents/{at}", key)
            agg["updated" if st == 200 else "created"] += 1
            continue
        st, resp = _req("POST", f"{base}/api/agents", key, a)
        if st in (200, 201):
            agg["created"] += 1
        elif st == 409:  # 기존 agent → PATCH 로 갱신(오버랩)
            st2, resp2 = _req("PATCH", f"{base}/api/agents/{at}", key, a)
            if st2 == 200:
                agg["updated"] += 1
            else:
                agg["error"] += 1
                print(f"  [agent {at}] PATCH HTTP {st2}: {str(resp2)[:150]}", file=sys.stderr)
        else:
            agg["error"] += 1
            print(f"  [agent {at}] POST HTTP {st}: {str(resp)[:150]}", file=sys.stderr)
    return agg


def main():
    ap = argparse.ArgumentParser(description="AIDataHub sync export → upsert import")
    ap.add_argument("--url", required=True, help="타겟 서버 base (예: http://127.0.0.1:8001)")
    ap.add_argument("--key", default="", help="X-API-Key (쓰기 인증)")
    ap.add_argument("--records", help="records.jsonl.gz 경로")
    ap.add_argument("--agents", help="agents.jsonl.gz 경로")
    ap.add_argument("--dry-run", action="store_true", help="실제 쓰기 없이 계획만")
    ap.add_argument("--batch", type=int, default=500, help="records 배치 크기 (≤1000)")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    mode = "DRY-RUN (쓰기 없음)" if args.dry_run else "APPLY (실제 upsert)"
    print(f"== sync import → {base}  [{mode}] ==")

    rec = agt = None
    if args.records:
        print(f"[records] {args.records}")
        rec = import_records(base, args.key, args.records, args.dry_run, args.batch)
    if args.agents:
        print(f"[agents] {args.agents}")
        agt = import_agents(base, args.key, args.agents, args.dry_run)

    print("\n── 결과 ──")
    if rec is not None:
        print(f"  records: total={rec['total']} inserted={rec['inserted']} "
              f"updated={rec['updated']} skipped={rec['skipped']} dry_run={rec['dry_run']} error={rec['error']}")
    if agt is not None:
        print(f"  agents : total={agt['total']} created={agt['created']} "
              f"updated={agt['updated']} error={agt['error']}")
    # 에러가 있으면 non-zero exit (자동화가 감지)
    err = (rec or {}).get("error", 0) + (agt or {}).get("error", 0)
    sys.exit(1 if err else 0)


if __name__ == "__main__":
    main()
