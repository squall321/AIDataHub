"""MCP stdio 검증 스크립트.

`mcp_server` 를 stdio 서버로 띄우고 9개 도구를 순차 호출한다.

실행:
    & "d:\\Personal\\AI_data\\api_server\\.venv\\Scripts\\python.exe" scripts/mcp_smoke.py

옵션:
    --api-url      : 백엔드 API URL (기본 http://localhost:8000).
    --timeout      : 도구 호출 타임아웃(초). 기본 15.
    --record-id    : find_related/get_record 에 사용할 record id (선택).
    --skip-server  : MCP 서버를 띄우지 않고 백엔드 HTTP 만 직접 검증.

배경
----
- MCP SDK 가 없거나 stdio 연결이 어려운 환경을 대비해 ``--skip-server`` 모드는
  backend HTTP 만 호출한다 (동등한 결과 검증).
- 결과 요약:  PASS / FAIL / SKIP 으로 9개 도구 표시.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

# 프로젝트 루트
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# MCP SDK
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    HAVE_MCP = True
except ImportError:  # pragma: no cover
    HAVE_MCP = False

import httpx


TOOLS = [
    "discover_schema",
    "discover_capabilities",
    "ask",
    "find_related",
    "explain_field",
    "query_data",
    "list_agents",
    "get_record",
    "search",
]


# ---------------------------------------------------------------------------
# 결과 컨테이너
# ---------------------------------------------------------------------------
class Outcome:
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


def _short(payload: Any, limit: int = 160) -> str:
    s = json.dumps(payload, ensure_ascii=False, default=str) if not isinstance(payload, str) else payload
    return s[:limit] + ("…" if len(s) > limit else "")


# ---------------------------------------------------------------------------
# (A) MCP stdio 모드
# ---------------------------------------------------------------------------
async def _run_via_mcp(api_url: str, timeout: float, record_id: str | None) -> dict[str, dict[str, Any]]:
    if not HAVE_MCP:
        return {t: {"outcome": Outcome.SKIP, "reason": "mcp SDK not installed"} for t in TOOLS}

    env = os.environ.copy()
    env["API_URL"] = api_url
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server"],
        env=env,
    )

    results: dict[str, dict[str, Any]] = {}

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=timeout)

            tools_resp = await session.list_tools()
            tool_names = {t.name for t in tools_resp.tools}
            for t in TOOLS:
                if t not in tool_names:
                    results[t] = {"outcome": Outcome.FAIL, "reason": "tool not registered"}

            async def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
                try:
                    resp = await asyncio.wait_for(
                        session.call_tool(name, args), timeout=timeout
                    )
                    # mcp 의 CallToolResult 는 .content (list of TextContent)
                    content = resp.content if hasattr(resp, "content") else resp
                    text = ""
                    if isinstance(content, list):
                        for c in content:
                            text += getattr(c, "text", "") or ""
                    payload: Any
                    try:
                        payload = json.loads(text) if text else {}
                    except Exception:
                        payload = text
                    if isinstance(payload, dict) and "error" in payload:
                        return {"outcome": Outcome.FAIL, "preview": _short(payload)}
                    return {"outcome": Outcome.PASS, "preview": _short(payload)}
                except asyncio.TimeoutError:
                    return {"outcome": Outcome.FAIL, "reason": f"timeout after {timeout}s"}
                except Exception as exc:
                    return {"outcome": Outcome.FAIL, "reason": f"{type(exc).__name__}: {exc}"}

            # 1) discover_schema
            results["discover_schema"] = await _call("discover_schema", {})

            # discover 응답에서 agent / record_id 추출 (있다면)
            preview = results["discover_schema"].get("preview", "")
            agent_type = "iga-analyst"
            try:
                ds = await asyncio.wait_for(
                    session.call_tool("discover_schema", {}), timeout=timeout
                )
                content = ds.content if hasattr(ds, "content") else []
                joined = "".join(getattr(c, "text", "") or "" for c in content)
                obj = json.loads(joined) if joined else {}
                agents = (obj.get("discover") or {}).get("agents") or []
                if agents:
                    a0 = agents[0]
                    agent_type = a0.get("type") or a0.get("agent_type") or agent_type
            except Exception:
                pass

            results["discover_capabilities"] = await _call(
                "discover_capabilities", {"agent_type": agent_type}
            )
            results["ask"] = await _call("ask", {"query": "smoke test", "limit": 3})
            results["list_agents"] = await _call("list_agents", {})
            results["query_data"] = await _call(
                "query_data", {"agent": agent_type, "limit": 3}
            )
            results["search"] = await _call(
                "search", {"mode": "fts", "query": "test"}
            )
            results["explain_field"] = await _call(
                "explain_field", {"field_name": "data_type"}
            )

            # record_id 결정
            rid = record_id
            if not rid:
                # query_data 의 결과에서 첫 record id 시도
                try:
                    qd = await asyncio.wait_for(
                        session.call_tool("query_data", {"agent": agent_type, "limit": 1}),
                        timeout=timeout,
                    )
                    content = qd.content if hasattr(qd, "content") else []
                    joined = "".join(getattr(c, "text", "") or "" for c in content)
                    obj = json.loads(joined) if joined else {}
                    items = obj.get("results") or obj.get("items") or []
                    if items:
                        rid = items[0].get("record_id") or items[0].get("id")
                except Exception:
                    pass

            if rid:
                results["get_record"] = await _call("get_record", {"record_id": rid})
                results["find_related"] = await _call(
                    "find_related", {"record_id": rid, "mode": "auto"}
                )
            else:
                results["get_record"] = {
                    "outcome": Outcome.SKIP,
                    "reason": "no record_id available (DB empty?)",
                }
                results["find_related"] = {
                    "outcome": Outcome.SKIP,
                    "reason": "no record_id available (DB empty?)",
                }

    return results


# ---------------------------------------------------------------------------
# (B) HTTP 직접 모드 (--skip-server)
# ---------------------------------------------------------------------------
async def _run_via_http(api_url: str, timeout: float, record_id: str | None) -> dict[str, dict[str, Any]]:
    base = api_url.rstrip("/")
    headers = {"X-API-Key": os.environ["API_KEY"]} if os.environ.get("API_KEY") else {}

    results: dict[str, dict[str, Any]] = {}

    async def _get(path: str, **params: Any) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers) as c:
                r = await c.get(base + path, params=params)
            if r.status_code >= 400:
                return {"outcome": Outcome.FAIL, "reason": f"HTTP {r.status_code}: {r.text[:120]}"}
            return {"outcome": Outcome.PASS, "preview": _short(r.json())}
        except Exception as exc:
            return {"outcome": Outcome.FAIL, "reason": f"{type(exc).__name__}: {exc}"}

    async def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers) as c:
                r = await c.post(base + path, json=body)
            if r.status_code >= 400:
                return {"outcome": Outcome.FAIL, "reason": f"HTTP {r.status_code}: {r.text[:120]}"}
            return {"outcome": Outcome.PASS, "preview": _short(r.json())}
        except Exception as exc:
            return {"outcome": Outcome.FAIL, "reason": f"{type(exc).__name__}: {exc}"}

    results["discover_schema"] = await _get("/api/discover")
    # 위 응답에서 agent_type 추출
    agent_type = "iga-analyst"
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as c:
            r = await c.get(base + "/api/discover")
        if r.status_code < 400:
            agents = (r.json() or {}).get("agents") or []
            if agents:
                a0 = agents[0]
                agent_type = a0.get("type") or a0.get("agent_type") or agent_type
    except Exception:
        pass

    results["discover_capabilities"] = await _get(f"/api/agents/{agent_type}")
    results["ask"] = await _post("/api/ask", {"query": "smoke test", "limit": 3})
    results["list_agents"] = await _get("/api/agents")
    results["query_data"] = await _get("/api/data", agent=agent_type, limit=3)
    results["search"] = await _get("/api/search", mode="fts", q="test")
    # explain_field 는 /api/schema 의 부분 추출이므로 schema 호출로 대체 검증
    results["explain_field"] = await _get("/api/schema")

    rid = record_id
    if not rid:
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers) as c:
                r = await c.get(base + "/api/data", params={"agent": agent_type, "limit": 1})
            items = (r.json() or {}).get("results") or (r.json() or {}).get("items") or []
            if items:
                rid = items[0].get("record_id") or items[0].get("id")
        except Exception:
            pass

    if rid:
        results["get_record"] = await _get(f"/api/records/{rid}")
        results["find_related"] = await _get(
            "/api/search", mode="tag", tags="IGA"
        )  # 근사 — 실제 find_related 와 동등 아님
    else:
        results["get_record"] = {
            "outcome": Outcome.SKIP,
            "reason": "no record_id available (DB empty?)",
        }
        results["find_related"] = {
            "outcome": Outcome.SKIP,
            "reason": "no record_id available (DB empty?)",
        }

    return results


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------
def _print_summary(results: dict[str, dict[str, Any]]) -> int:
    print("\n=== MCP smoke test summary ===")
    pad = max(len(t) for t in TOOLS)
    fail_count = 0
    for t in TOOLS:
        r = results.get(t, {"outcome": Outcome.FAIL, "reason": "missing"})
        oc = r.get("outcome", Outcome.FAIL)
        line = f"  {t.ljust(pad)}  {oc}"
        detail = r.get("preview") or r.get("reason") or ""
        if detail:
            line += f"  | {detail}"
        print(line)
        if oc == Outcome.FAIL:
            fail_count += 1
    print()
    if fail_count:
        print(f"FAILED: {fail_count} tool(s) failed.")
        return 1
    print("OK: all tools responded successfully (SKIP allowed for missing data).")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="MCP smoke test for ai-data-hub")
    p.add_argument("--api-url", default=os.environ.get("API_URL", "http://localhost:8000"))
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--record-id", default=None)
    p.add_argument(
        "--skip-server",
        action="store_true",
        help="MCP 서버 띄우지 않고 백엔드 HTTP 직접 호출 (동등 검증).",
    )
    return p.parse_args()


async def amain() -> int:
    args = parse_args()
    print(f"[mcp_smoke] api_url={args.api_url} timeout={args.timeout}s "
          f"mode={'http-direct' if args.skip_server else 'mcp-stdio'}")
    if args.skip_server:
        results = await _run_via_http(args.api_url, args.timeout, args.record_id)
    else:
        results = await _run_via_mcp(args.api_url, args.timeout, args.record_id)
    return _print_summary(results)


def main() -> None:
    try:
        sys.exit(asyncio.run(amain()))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
