"""AI Data Hub — Discovery walkthrough.

Agent 가 시스템에 처음 접속해 무엇이 있는지 1~2분 내에 파악하는 과정.
4단계 호출로 시스템 윤곽 → 첫 검색까지.
"""
from __future__ import annotations
import json
import sys

sys.path.insert(0, ".")
from python_client import client


def main() -> int:
    print("=" * 60)
    print(" AI Data Hub — Discovery walkthrough")
    print(f" base = {client.base}")
    print("=" * 60)

    # Step 1: Health
    print("\n[1/4] Health check ...")
    h = client.health()
    if h.get("status") != "ok":
        print(f"  [FAIL] status={h.get('status')}")
        return 1
    print(f"  [OK] version={h.get('version')} auth_required={h.get('auth_required')}")

    # Step 2: Discover
    print("\n[2/4] Catalog (/api/discover) ...")
    d = client.discover()
    print(f"  total_records: {d.get('total_records')}")
    print(f"  by_data_type:  {json.dumps(d.get('by_data_type', {}), ensure_ascii=False)}")
    agents = d.get("agents")
    if isinstance(agents, list):
        print(f"  agents ({len(agents)}): {agents[:5]}")
    elif isinstance(agents, dict):
        print(f"  agents ({len(agents)}): {list(agents.keys())[:5]}")

    # Step 3: Tag cloud
    print("\n[3/4] Top tags ...")
    tags = client.taxonomy_tags(limit=15)
    for t in tags[:10]:
        name = t.get("tag") or t.get("name", "?")
        count = t.get("count") or t.get("usage_count", 0)
        print(f"  {name:30} {count:>4}")

    # Step 4: Sample search
    print("\n[4/4] Sample searches ...")
    queries = [
        ("semantic", "KooRemapper"),
        ("fts", "stress"),
    ]
    for mode, q in queries:
        try:
            items = client.search(q, mode=mode, limit=3)
            print(f"  [{mode}] q={q!r}: {len(items)} items")
            for it in items[:2]:
                rid = it.get("record_id") or it.get("id", "")
                title = (it.get("title") or "")[:50]
                score = it.get("score", "-")
                if isinstance(score, float):
                    score = f"{score:.3f}"
                print(f"    {rid:30} {score:>8}  {title}")
        except Exception as e:
            print(f"  [{mode}] q={q!r}: FAIL — {e}")

    print("\n" + "=" * 60)
    print(" Discovery complete. 다음 단계 권장:")
    print("   - patterns.md §10 그룹 단위 발췌")
    print("   - patterns.md §16 작은 모델 진입 시퀀스")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
