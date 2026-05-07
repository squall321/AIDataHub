"""AI Data Hub MCP stdio 서버.

REST API(``/api/data``, ``/api/agents`` 등)를 MCP 도구로 래핑한다.

도구 목록
---------
- ``query_data(agent, query="", limit=5)`` → ``GET /api/data``
- ``list_agents()`` → ``GET /api/agents``
- ``get_record(record_id)`` → ``GET /api/records/{id}``
- ``search(mode, query, tags=None)`` → ``GET /api/search``

실행
----
``python -m mcp_server`` 또는 직접 ``python src/mcp_server/server.py``
(MCP 클라이언트 측 stdio 트랜스포트 등록)
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

# MCP SDK
try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "mcp 패키지가 필요합니다. `pip install 'mcp>=1.2.0'` 로 설치하세요."
    ) from exc


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
API_URL: str = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
API_TIMEOUT: float = float(os.environ.get("API_TIMEOUT", "30"))
MAX_LIMIT: int = 20

logger = logging.getLogger("mcp_server")
logging.basicConfig(
    level=os.environ.get("MCP_LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


# ---------------------------------------------------------------------------
# FastMCP 인스턴스
# ---------------------------------------------------------------------------
mcp = FastMCP("ai-data-hub")


# ---------------------------------------------------------------------------
# 공용 HTTP 호출 헬퍼
# ---------------------------------------------------------------------------
async def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """API 호출 + 일관된 에러 페이로드.

    실패 시 호출자에게 사람이 읽을 수 있는 dict 를 반환한다 (예외 raise 안 함).
    """
    url = f"{API_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            resp = await client.request(method, url, params=params, json=json_body)
    except httpx.TimeoutException:
        return {"error": "timeout", "url": url, "timeout": API_TIMEOUT}
    except httpx.RequestError as exc:
        return {"error": "request_failed", "detail": str(exc), "url": url}

    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        return {
            "error": "http_error",
            "status_code": resp.status_code,
            "detail": detail,
            "url": url,
        }

    try:
        return resp.json()
    except Exception:
        return {"error": "invalid_json", "text": resp.text[:1000], "url": url}


def _clamp_limit(limit: int) -> int:
    if limit is None:
        return 5
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return 5
    return max(1, min(limit, MAX_LIMIT))


# ---------------------------------------------------------------------------
# 도구: query_data
# ---------------------------------------------------------------------------
@mcp.tool()
async def query_data(agent: str, query: str = "", limit: int = 5) -> dict[str, Any]:
    """지정 에이전트 타입에 해당하는 데이터 레코드를 조회한다.

    Args:
        agent: 에이전트 타입 (예: 'iga-analyst', 'cae-reporter').
        query: 선택적 검색 키워드 (제목/요약/섹션 매칭).
        limit: 최대 결과 수 (기본 5, 최대 20).

    Returns:
        ``{"results": [...]}`` 형태의 dict. 실패 시 ``{"error": ...}``.
    """
    if not agent:
        return {"error": "missing_argument", "detail": "agent is required"}

    params: dict[str, Any] = {"agent": agent, "limit": _clamp_limit(limit)}
    if query:
        params["query"] = query

    data = await _request("GET", "/api/data", params=params)
    # 응답 정규화
    if isinstance(data, dict) and "error" not in data and "results" not in data:
        # API가 list 를 그대로 반환하는 케이스 호환
        if "items" in data:
            data = {"results": data["items"]}
    elif isinstance(data, list):
        data = {"results": data}
    return data


# ---------------------------------------------------------------------------
# 도구: list_agents
# ---------------------------------------------------------------------------
@mcp.tool()
async def list_agents() -> dict[str, Any]:
    """등록된 모든 에이전트 타입과 데이터 스코프를 반환한다.

    Returns:
        ``{"agents": [...]}`` 형태. 실패 시 ``{"error": ...}``.
    """
    data = await _request("GET", "/api/agents")
    if isinstance(data, list):
        return {"agents": data}
    if isinstance(data, dict) and "error" not in data:
        if "items" in data:
            return {"agents": data["items"]}
        if "agents" not in data:
            return {"agents": data.get("results", [])}
    return data  # dict (already shaped or error)


# ---------------------------------------------------------------------------
# 도구: get_record
# ---------------------------------------------------------------------------
@mcp.tool()
async def get_record(record_id: str) -> dict[str, Any]:
    """단일 레코드를 ID로 조회한다.

    Args:
        record_id: 레코드 ID (예: 'DOC-HE-CAE-2026-000001').

    Returns:
        Record 페이로드 dict. 없으면 ``{"error": "http_error", "status_code": 404, ...}``.
    """
    if not record_id:
        return {"error": "missing_argument", "detail": "record_id is required"}
    return await _request("GET", f"/api/records/{record_id}")


# ---------------------------------------------------------------------------
# 도구: search
# ---------------------------------------------------------------------------
@mcp.tool()
async def search(
    mode: str,
    query: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """레코드 검색.

    Args:
        mode: 'tag' | 'fts' | 'semantic'.
        query: fts/semantic 모드에서 사용할 검색 문장.
        tags: tag 모드에서 사용할 태그 리스트.

    Returns:
        ``{"results": [...]}`` 또는 에러 dict.
    """
    if mode not in {"tag", "fts", "semantic"}:
        return {
            "error": "invalid_argument",
            "detail": f"mode must be one of tag/fts/semantic, got {mode!r}",
        }

    params: dict[str, Any] = {"mode": mode}
    if query:
        params["q"] = query
    if tags:
        # FastAPI 는 동일 쿼리 키 반복을 list 로 받는다. httpx 는 tuple list 를 그대로 보낸다.
        params_list: list[tuple[str, Any]] = [("mode", mode)]
        if query:
            params_list.append(("q", query))
        for t in tags:
            params_list.append(("tags", t))
        data = await _request("GET", "/api/search", params=params_list)  # type: ignore[arg-type]
    else:
        data = await _request("GET", "/api/search", params=params)

    if isinstance(data, list):
        return {"results": data}
    if isinstance(data, dict) and "error" not in data and "results" not in data:
        if "items" in data:
            return {"results": data["items"]}
    return data


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------
def main() -> None:
    """stdio 트랜스포트로 MCP 서버 실행."""
    logger.info("starting ai-data-hub MCP server (API_URL=%s)", API_URL)
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
