"""``/metrics`` — Prometheus 텍스트 포맷.

ENABLE_METRICS=false 면 ``router`` 는 등록되지 않는다 (register 측에서 처리).
별도로 ``mcp_router`` (``/api/metrics/mcp``) 는 항상 등록 — 운영 가시성용.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Query
from starlette.responses import Response

from ..middleware.metrics import render_metrics

router = APIRouter(tags=["system"])


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


# ---------------------------------------------------------------------------
# /api/metrics/mcp — MCP 호출 집계 (logs/mcp-calls.jsonl tail 분석).
# 운영자가 어떤 tool 이 얼마나 호출되는지, 평균 latency, error rate 를 즉시 확인.
# ---------------------------------------------------------------------------
mcp_router = APIRouter(prefix="/api/metrics", tags=["system"])


_DEFAULT_LOG_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "logs" / "mcp-calls.jsonl"
)


def _log_path() -> Path:
    p = os.environ.get("AIDH_MCP_LOG_PATH")
    return Path(p) if p else _DEFAULT_LOG_PATH


def _tail(path: Path, max_lines: int) -> list[dict]:
    """파일 끝에서 max_lines 줄 JSONL 만 파싱해 list[dict] 반환.

    ~수만 줄 까지 메모리 안전. 깨진 줄은 skip.
    """
    if not path.exists():
        return []
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            # 라인 평균 200 byte 가정 — 충분히 크게 잡고 부족하면 더 읽음.
            block = max(8192, max_lines * 256)
            start = max(0, size - block)
            f.seek(start)
            tail = f.read().decode("utf-8", errors="replace")
    except Exception:
        return []
    lines = tail.splitlines()
    # 끝에서 max_lines 만.
    lines = lines[-max_lines:]
    out: list[dict] = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


@mcp_router.get("/mcp")
async def mcp_metrics(
    tail: int = Query(500, ge=1, le=5000, description="최근 N개 호출만 집계"),
) -> dict:
    """최근 ``tail`` 개 MCP 호출 집계.

    Returns:
        - ``total`` : 분석 대상 호출 수
        - ``by_tool``: {tool_name: {count, avg_ms, p95_ms, error_count}}
        - ``by_method``: {jsonrpc_method: count}
        - ``recent`` : 직전 10건 raw
        - ``log_path``: 사용된 로그 파일 절대경로
    """
    entries = _tail(_log_path(), tail)
    by_tool: dict[str, dict] = {}
    method_counter: Counter[str] = Counter()
    status_counter: Counter[int] = Counter()

    # 도구별 latency 누적
    tool_lat: dict[str, list[float]] = {}
    tool_err: Counter[str] = Counter()

    for e in entries:
        m = e.get("method") or "unknown"
        method_counter[m] += 1
        s = int(e.get("status") or 0)
        status_counter[s] += 1
        tool = e.get("tool") or (m if m != "tools/call" else "(unknown_tool)")
        dur = float(e.get("duration_ms") or 0.0)
        tool_lat.setdefault(tool, []).append(dur)
        if s >= 400:
            tool_err[tool] += 1

    for tool, durs in tool_lat.items():
        durs_sorted = sorted(durs)
        n = len(durs_sorted)
        avg = sum(durs_sorted) / n if n else 0.0
        # p95 — 단순 percentile.
        idx = max(0, min(n - 1, int(round(0.95 * (n - 1)))))
        p95 = durs_sorted[idx] if n else 0.0
        by_tool[tool] = {
            "count": n,
            "avg_ms": round(avg, 3),
            "p95_ms": round(p95, 3),
            "error_count": tool_err.get(tool, 0),
        }

    # count 내림차순 정렬
    by_tool_sorted = dict(
        sorted(by_tool.items(), key=lambda kv: kv[1]["count"], reverse=True)
    )

    return {
        "total": len(entries),
        "by_tool": by_tool_sorted,
        "by_method": dict(method_counter.most_common()),
        "by_status": dict(status_counter.most_common()),
        "recent": entries[-10:],
        "log_path": str(_log_path()),
    }


__all__ = ["router", "mcp_router"]
