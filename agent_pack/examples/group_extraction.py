"""AI Data Hub — 그룹/체크리스트 발췌 패턴.

작성자가 `tags=["group:CAE", "checklist", "scope:group"]` 같은
그룹 메타를 박아둔 record 중에서 특정 그룹의 자료만 깔끔하게
추출하는 호출 패턴 데모.
"""
from __future__ import annotations
import json
import sys

sys.path.insert(0, ".")
from python_client import client


def extract_group_records(
    group_code: str,
    doc_kind: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """그룹 코드로 record 발췌.

    예: extract_group_records("CAE", doc_kind="checklist")
        → tag="group:CAE" + tag="checklist" 모두 매칭하는 record
    """
    tags = [f"group:{group_code}"]
    if doc_kind:
        tags.append(doc_kind)

    # mode=tag 는 모든 tag 매칭 (AND).
    items = client.search("", mode="tag", limit=limit, tags=tags)
    return items


def discover_groups_via_facets(seed_query: str) -> dict:
    """seed query 로 검색한 결과의 facets 에서 group:* 후보 발견.

    그룹 메타가 정착되지 않은 코퍼스에서 어떤 그룹이 존재하는지
    탐색하는 휴리스틱.
    """
    resp = client.search_faceted(q=seed_query, mode="semantic", limit=50)
    tag_facet = (resp.get("facets") or {}).get("tags", {})
    domain_facet = (resp.get("facets") or {}).get("domain", {})
    group_tags = {k: v for k, v in tag_facet.items() if k.startswith("group:")}
    return {
        "group_tags": group_tags,
        "domains": domain_facet,
        "total_records": resp.get("total"),
    }


def slice_by_classification(
    records: list[dict],
    classification: str = "internal",
) -> list[dict]:
    """클라이언트 후필터 (서버는 classification 직접 필터를 catalog 에서 지원하지 않음)."""
    return [r for r in records if r.get("classification") == classification]


def main() -> int:
    print("=== 그룹 발췌 데모 ===\n")

    # 1. 그룹 메타 발견
    print("[1] facets 로 group:* 후보 탐색 ...")
    survey = discover_groups_via_facets("매뉴얼")
    print(f"  발견된 group:* tag: {list(survey['group_tags'].keys())}")
    print(f"  domains:          {list(survey['domains'].keys())}")

    # 2. 특정 그룹 추출
    target = next(iter(survey["group_tags"]), None)
    if target:
        code = target.split(":", 1)[1]
        print(f"\n[2] '{target}' record 추출 ...")
        items = extract_group_records(code, doc_kind=None, limit=20)
        print(f"  → {len(items)} record")
        for it in items[:3]:
            rid = it.get("id") or it.get("record_id", "")
            print(f"    {rid:30}  {it.get('title','')[:50]}")
    else:
        print("\n[2] (group:* tag 가 없음 — 작성 표준 적용 전)")

    # 3. 권한 후필터 예시
    print("\n[3] classification=internal 후필터 ...")
    if target:
        slice_ = slice_by_classification(items, classification="internal")
        print(f"  internal 만: {len(slice_)} / {len(items)}")
    else:
        print("  (skip)")

    # 4. 작성 표준 가이드 출력
    print("\n[4] 권장 작성 표준 (META_FORMAT_AUDIT.md):")
    print("    tags = ['group:<코드>', 'scope:group', 'checklist']")
    print("    classification = 'internal' | 'restricted-<group>'")
    print("\n  이 표준이 적용되면 본 데모의 1단계 facets 호출이")
    print("  곧바로 그룹 카탈로그가 된다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
