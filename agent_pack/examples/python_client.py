"""AI Data Hub — Minimal Python client (stdlib only).

다른 AI agent 가 이 한 파일만 import 해서 즉시 검색·발견 가능.
의존성: Python 3.10+, 표준 라이브러리만.

사용법:
    from python_client import client
    items = client.search("KooRemapper", mode="semantic", limit=5)
    rec   = client.get_record(items[0]["record_id"])

또는:
    python python_client.py            # 간이 자가 진단
    python python_client.py search "응력 변형률"
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
import urllib.error
from typing import Any

# === HARDCODED API URL =====================================================
# 다른 서버에서 운영 중이면 이 한 줄만 변경.
BASE = "http://110.15.177.125:8000"
API_KEY: str | None = None  # 인증 활성 시 설정 (CONFIG.md 참조)
# ===========================================================================


class AIDataHubClient:
    def __init__(self, base: str = BASE, api_key: str | None = API_KEY) -> None:
        self.base = base.rstrip("/")
        self.api_key = api_key

    # ------------------------------------------------------------------ core
    def _request(
        self, path: str, method: str = "GET", body: Any = None, params: dict | None = None
    ) -> Any:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                ct = r.headers.get("Content-Type", "")
                raw = r.read()
                if "application/json" in ct:
                    return json.loads(raw)
                return raw.decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            payload = e.read().decode("utf-8", "ignore")
            try:
                err = json.loads(payload)
            except Exception:
                err = {"error": {"code": "UNKNOWN", "message": payload}}
            raise RuntimeError(
                f"HTTP {e.code}: {err.get('error', {}).get('message', payload)}"
            ) from e

    # --------------------------------------------------------------- helpers
    def health(self) -> dict:
        """헬스체크. status='ok' 가 정상."""
        return self._request("/api/system/health")

    def discover(self) -> dict:
        """카탈로그 — total_records, by_data_type, agents, top_tags."""
        return self._request("/api/discover")

    def search(
        self,
        q: str,
        mode: str = "semantic",
        limit: int = 20,
        tags: list[str] | None = None,
    ) -> list[dict]:
        """검색 — semantic / fts / tag.

        - mode=semantic: 한↔영 cross-lingual, 의미 기반 (e5_small)
        - mode=fts:      PG to_tsvector 토큰 매칭
        - mode=tag:      tags=['IGA','NURBS'] 필수, AND 매칭
        """
        params: dict[str, Any] = {"mode": mode, "limit": limit}
        if mode == "tag":
            if not tags:
                raise ValueError("mode=tag requires tags list")
            params["tags"] = tags
        else:
            params["q"] = q
        return self._request("/api/search", params=params).get("items", [])

    def search_faceted(self, q: str | None = None, **filters: Any) -> dict:
        """다축 필터 + facet 카운트 (다음 좁힘 후보 안내)."""
        params: dict[str, Any] = {}
        if q:
            params["q"] = q
        params.update({k: v for k, v in filters.items() if v is not None})
        return self._request("/api/search/faceted", params=params)

    def get_record(self, record_id: str) -> dict:
        """단일 record 본문 + 첨부."""
        return self._request(f"/api/records/{urllib.parse.quote(record_id)}")

    def list_records(self, **filters: Any) -> dict:
        """카탈로그 필터링 (data_type/tag/agent/limit/offset)."""
        return self._request("/api/records", params=filters)

    def auto_groups(self, q: str, n_groups: int = 3, top_k: int = 50) -> dict:
        """semantic 자동 클러스터링."""
        return self._request(
            "/api/groups/auto",
            method="POST",
            body={"q": q, "n_groups": n_groups, "top_k": top_k},
        )

    def cluster_neighbors(self, record_id: str, top_k: int = 10) -> list[dict]:
        """단일 record 의 시맨틱 이웃."""
        return self._request(
            f"/api/records/{urllib.parse.quote(record_id)}/cluster",
            params={"top_k": top_k},
        ).get("neighbors", [])

    def ask(self, q: str) -> dict:
        """자연어 질의 → interpreted_query + results."""
        return self._request("/api/ask", method="POST", body={"q": q})

    def taxonomy_tags(self, limit: int = 50) -> list[dict]:
        return self._request("/api/taxonomy/tags", params={"limit": limit}).get(
            "tags", []
        )


# 모듈 레벨 싱글턴
client = AIDataHubClient()


# ------------------------------------------------------------------ self test
def _self_test() -> int:
    print(f"=== AI Data Hub self-test ({BASE}) ===")
    try:
        h = client.health()
        print(f"[OK] health: status={h.get('status')} version={h.get('version')}")
    except Exception as e:
        print(f"[FAIL] health: {e}")
        print(f"  서버가 안 떠있거나 URL ({BASE}) 잘못됨. CONFIG.md 참조.")
        return 1

    try:
        d = client.discover()
        print(
            f"[OK] discover: total_records={d.get('total_records')} "
            f"by_data_type={d.get('by_data_type', {})}"
        )
    except Exception as e:
        print(f"[FAIL] discover: {e}")
        return 1

    try:
        items = client.search("KooRemapper", mode="semantic", limit=3)
        print(f"[OK] semantic search: {len(items)} items")
        for it in items[:2]:
            print(
                f"  - {it.get('record_id')} sim={it.get('score',0):.3f}: "
                f"{it.get('title', '')[:50]}"
            )
    except Exception as e:
        print(f"[FAIL] search: {e}")
        return 1

    print("[ALL PASSED] 직결 OK")
    return 0


def _cli_search(argv: list[str]) -> int:
    if len(argv) < 1:
        print("usage: python_client.py search '<query>' [mode]")
        return 2
    q = argv[0]
    mode = argv[1] if len(argv) > 1 else "semantic"
    items = client.search(q, mode=mode, limit=10)
    for it in items:
        rid = it.get("record_id") or it.get("id", "")
        print(f"{rid:30}  {it.get('score', '-'):>8}  {it.get('title','')[:60]}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "search":
        sys.exit(_cli_search(sys.argv[2:]))
    sys.exit(_self_test())
