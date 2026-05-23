"""MCP 호출 로깅 — JSON-RPC method/tool name + latency 를 JSONL 로.

배경:
    /mcp/ 는 streamable HTTP 위의 JSON-RPC 라 URL path 만으로는 어떤 tool 이
    호출됐는지 모른다 (전부 "/" 또는 "/mcp/" 로 보임). 본 미들웨어는 요청
    본문을 가볍게 sniff 해서 ``method`` 와 ``params.name`` (tool 이름) 을
    추출, ``logs/mcp-calls.jsonl`` 에 append 한다.

설계:
    - ASGI middleware (raw) — Starlette BaseHTTPMiddleware 는 body 를 두 번
      읽으면 hang 위험. send/receive 를 직접 감싼다.
    - body size > MAX_BUFFER (default 64KB) 면 sniff skip — large doc upload
      류 안전.
    - 잘못된 JSON 이면 method 만 unknown 으로 기록 (요청은 영향 X).
    - env ``AIDH_MCP_LOG=0`` 이면 미들웨어가 no-op 으로 동작 (회귀 안전망).

로그 라인 (한 줄 JSON):
    {"ts": "...", "method": "tools/call", "tool": "agent_search",
     "duration_ms": 42.1, "status": 200, "client": "...", "size": 312}
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("api.mcp.calls")


_MAX_BUFFER = 64 * 1024  # 64 KB

# 로그 파일 경로 — main.py 의 BASE 와 정합. 기본 api_server/logs/mcp-calls.jsonl.
_DEFAULT_LOG_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "logs" / "mcp-calls.jsonl"
)


def _log_path() -> Path:
    p = os.environ.get("AIDH_MCP_LOG_PATH")
    return Path(p) if p else _DEFAULT_LOG_PATH


def _enabled() -> bool:
    return os.environ.get("AIDH_MCP_LOG", "1") != "0"


def _sniff_tool_name(raw: bytes) -> tuple[str | None, str | None]:
    """JSON-RPC body → (method, tool_name).

    tools/call 의 경우 params.name 이 실제 tool. 나머지 method 는 tool=None.
    파싱 실패 시 (None, None) — 로그는 method=unknown 으로 기록.
    """
    if not raw:
        return None, None
    try:
        # streamable HTTP 는 한 요청에 여러 JSON-RPC 가 묶이지 않으므로 단일 객체 가정.
        obj: Any = json.loads(raw)
    except Exception:
        return None, None
    if isinstance(obj, list):
        # batch — 첫 번째만 본다 (운영상 단일 호출이 대부분).
        obj = obj[0] if obj else {}
    if not isinstance(obj, dict):
        return None, None
    method = obj.get("method")
    if not isinstance(method, str):
        method = None
    tool = None
    if method == "tools/call":
        params = obj.get("params") or {}
        if isinstance(params, dict):
            n = params.get("name")
            if isinstance(n, str):
                tool = n
    return method, tool


class MCPLoggingASGI:
    """ASGI middleware that wraps the MCP sub-app.

    Apply once over the FastMCP streamable_http_app — ``main.py`` mount.
    Non-MCP requests (path != "/" relative to mount) and non-HTTP scopes
    are passed through with zero overhead.
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self._log_path_cache = _log_path()
        # ensure parent dir
        try:
            self._log_path_cache.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not _enabled():
            await self.app(scope, receive, send)
            return

        t0 = time.perf_counter()
        body_chunks: list[bytes] = []
        size_total = 0
        sniff_done = False

        async def recv_wrap() -> Any:
            nonlocal size_total, sniff_done
            msg = await receive()
            if msg.get("type") == "http.request":
                chunk = msg.get("body") or b""
                size_total += len(chunk)
                if not sniff_done and size_total <= _MAX_BUFFER:
                    body_chunks.append(chunk)
                else:
                    sniff_done = True  # buffer 초과 → 이후 chunk 누적 중단
            return msg

        status_holder: dict[str, int] = {"code": 0}

        async def send_wrap(msg: Any) -> None:
            if msg.get("type") == "http.response.start":
                status_holder["code"] = int(msg.get("status") or 0)
            await send(msg)

        try:
            await self.app(scope, recv_wrap, send_wrap)
        finally:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            self._emit(scope, body_chunks, size_total, status_holder["code"], duration_ms)

    def _emit(
        self,
        scope: Any,
        body_chunks: list[bytes],
        size_total: int,
        status: int,
        duration_ms: float,
    ) -> None:
        try:
            method = None
            tool = None
            if body_chunks and size_total <= _MAX_BUFFER:
                method, tool = _sniff_tool_name(b"".join(body_chunks))
            client_ip = None
            client = scope.get("client")
            if isinstance(client, (list, tuple)) and client:
                client_ip = str(client[0])
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "method": method or "unknown",
                "tool": tool,
                "status": status,
                "duration_ms": round(duration_ms, 3),
                "size": size_total,
                "client": client_ip,
            }
            # JSONL append. logger 도 동시에 (구조화 액세스 로그 정합).
            try:
                with self._log_path_cache.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception:
                pass
            try:
                log.info("mcp_call", extra=entry)
            except Exception:
                pass
        except Exception:  # pragma: no cover
            pass


__all__ = ["MCPLoggingASGI"]
