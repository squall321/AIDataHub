"""AI Data Hub MCP stdio 서버.

REST API(``/api/data``, ``/api/agents`` 등)를 MCP 도구로 래핑한다.

도구 목록
---------
- ``discover_schema()`` → ``GET /api/discover`` + ``/api/schema`` 합본 (먼저 호출).
- ``discover_capabilities(agent_type)`` → ``GET /api/agents/{type}`` 풍부화.
- ``ask(query, limit=5)`` → ``POST /api/ask`` 자연어 검색.
- ``find_related(record_id, mode='auto')`` → tags/semantic/graph 결합.
- ``explain_field(field_name)`` / ``explain_schema(field_name)`` → ``/api/schema`` 단일 필드 설명 (alias).
- ``query_data(agent, query="", limit=5)`` → ``GET /api/data``
- ``list_agents()`` → ``GET /api/agents``
- ``get_record(record_id)`` → ``GET /api/records/{id}``
- ``search(mode, query, tags=None)`` → ``GET /api/search``

LLM 이 도구 docstring 만 읽고도 올바른 도구를 선택할 수 있도록 docstring 을
명시적으로 작성한다 (Agent 30 — Discovery / RAG-friendly API).

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
# 도구: discover_schema (Agent 30 — RAG-friendly entry point)
# ---------------------------------------------------------------------------
@mcp.tool()
async def discover_schema() -> dict[str, Any]:
    """**Call this FIRST.** 데이터 허브의 전체 구조를 한 번에 받아온다.

    반환:
        ``GET /api/discover`` (카운트, 에이전트, 데이터 타입 설명, 시작점 URL) +
        ``GET /api/schema`` (JSON Schema, draft-2020-12, 필드/enum/oneOf).

    AI 에이전트가 백엔드 source 를 읽지 않고도 이 한 호출로 아래를 알 수 있다:
        - 어떤 data_type 이 있고 record 가 몇 개 있는지
        - 어떤 agent 타입이 있고 무엇을 다루는지
        - 필드/enum/관계 (parent_record_id, related_record_ids, ...)
        - 다음 단계 endpoint (starting_points)
    """
    discover = await _request("GET", "/api/discover")
    schema = await _request("GET", "/api/schema")
    return {"discover": discover, "schema": schema}


@mcp.tool()
async def discover_capabilities(agent_type: str) -> dict[str, Any]:
    """특정 agent 타입에 대해 어떤 데이터가 있는지 본다.

    ``discover_schema`` 다음 호출. agent 의 메타(common_tags, data_types) +
    실제 보유 record 수 + 샘플 record 까지.

    Args:
        agent_type: 예시 — 'iga-analyst', 'cae-reporter'.
    """
    if not agent_type:
        return {"error": "missing_argument", "detail": "agent_type required"}
    agent_meta = await _request("GET", f"/api/agents/{agent_type}")
    if isinstance(agent_meta, dict) and "error" in agent_meta:
        return agent_meta
    records = await _request(
        "GET", f"/api/agents/{agent_type}/records"
    )
    sample = records if isinstance(records, list) else []
    return {
        "agent": agent_meta,
        "record_count": len(sample),
        "sample_records": sample[:5],
        "follow_up": [
            f"GET /api/data?agent={agent_type}&query=<keyword>",
            f"POST /api/ask {{\"query\":\"... {agent_type} ...\"}}",
        ],
    }


@mcp.tool()
async def ask(query: str, limit: int = 5) -> dict[str, Any]:
    """자연어 쿼리. 한국어/영어 모두 가능.

    이 도구는 쿼리를 해석한 ``interpreted_query`` (어떤 필터로 풀었는지) +
    ``results`` (record 목록) + ``follow_up_queries`` 를 반환한다.

    Args:
        query: 예시 — '최근 1주일 IGA 시뮬레이션', 'tables with quality>=80'.
        limit: 최대 결과 수 (1-50, 기본 5).

    Returns:
        ``{interpreted_query, results, total_matched, follow_up_queries}``
    """
    if not query or not query.strip():
        return {"error": "missing_argument", "detail": "query required"}
    return await _request(
        "POST", "/api/ask", json_body={"query": query, "limit": _clamp_limit(limit)}
    )


@mcp.tool()
async def find_related(record_id: str, mode: str = "auto") -> dict[str, Any]:
    """주어진 record 와 관련된 record 들을 찾는다.

    Args:
        record_id: 기준 record (예: 'DOC-HE-CAE-2026-000001').
        mode: 'tags' | 'graph' | 'semantic' | 'auto' (기본).
            - 'tags': 같은 태그 ≥1 공유.
            - 'graph': record.related_record_ids + parent/children.
            - 'semantic': /api/search?mode=semantic 활용 (pgvector 필요).
            - 'auto': 위 셋을 모두 합쳐 dedup.

    Returns:
        ``{related: [...], by_mode: {tags: [...], graph: [...], semantic: [...]}}``
    """
    if not record_id:
        return {"error": "missing_argument", "detail": "record_id required"}
    if mode not in {"tags", "graph", "semantic", "auto"}:
        return {
            "error": "invalid_argument",
            "detail": f"mode must be tags/graph/semantic/auto, got {mode!r}",
        }

    base = await _request("GET", f"/api/records/{record_id}")
    if isinstance(base, dict) and "error" in base:
        return base

    by_mode: dict[str, list[dict[str, Any]]] = {
        "tags": [],
        "graph": [],
        "semantic": [],
    }

    # graph: related_record_ids + parent/children
    if mode in ("graph", "auto"):
        ids: list[str] = []
        for rid in base.get("related_record_ids") or []:
            if rid != record_id:
                ids.append(rid)
        if base.get("parent_record_id"):
            ids.append(base["parent_record_id"])
        for rid in ids:
            r = await _request("GET", f"/api/records/{rid}")
            if isinstance(r, dict) and "error" not in r:
                by_mode["graph"].append(
                    {"id": r.get("id"), "title": r.get("title"), "data_type": r.get("data_type")}
                )

    # tags: ?mode=tag&tags=<each>
    if mode in ("tags", "auto"):
        tags = base.get("tags") or []
        if tags:
            params: list[tuple[str, Any]] = [("mode", "tag")]
            for t in tags[:5]:  # cap to first 5 tags
                params.append(("tags", t))
            data = await _request("GET", "/api/search", params=params)  # type: ignore[arg-type]
            items = (
                data.get("results")
                if isinstance(data, dict)
                else (data if isinstance(data, list) else [])
            ) or []
            for it in items:
                if it.get("record_id") and it.get("record_id") != record_id:
                    by_mode["tags"].append(
                        {
                            "id": it.get("record_id"),
                            "title": it.get("title"),
                            "data_type": it.get("data_type"),
                        }
                    )

    # semantic
    if mode in ("semantic", "auto"):
        title = base.get("title") or ""
        if title:
            data = await _request(
                "GET", "/api/search", params={"mode": "semantic", "q": title}
            )
            items = (
                data.get("results") if isinstance(data, dict) else []
            ) or []
            for it in items:
                if it.get("record_id") and it.get("record_id") != record_id:
                    by_mode["semantic"].append(
                        {
                            "id": it.get("record_id"),
                            "title": it.get("title"),
                            "data_type": it.get("data_type"),
                        }
                    )

    # dedup
    seen: set[str] = set()
    related: list[dict[str, Any]] = []
    for items in by_mode.values():
        for r in items:
            rid = r.get("id")
            if rid and rid not in seen:
                seen.add(rid)
                related.append(r)

    return {"record_id": record_id, "mode": mode, "related": related, "by_mode": by_mode}


@mcp.tool()
async def explain_field(field_name: str) -> dict[str, Any]:
    """단일 필드의 의미·타입·허용 값을 설명한다.

    내부적으로 ``GET /api/schema`` 를 받아 해당 프로퍼티만 추출한다.

    Args:
        field_name: 예시 — 'data_type', 'classification', 'capabilities'.
    """
    if not field_name:
        return {"error": "missing_argument", "detail": "field_name required"}
    schema = await _request("GET", "/api/schema")
    if isinstance(schema, dict) and "error" in schema:
        return schema
    props = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
    spec = props.get(field_name)
    if spec is None:
        return {
            "error": "field_not_found",
            "field_name": field_name,
            "available_fields": sorted(props.keys()),
        }
    return {
        "field_name": field_name,
        "spec": spec,
        "is_enum": "enum" in spec,
        "allowed_values": spec.get("enum"),
        "type": spec.get("type"),
        "description": spec.get("description"),
    }


@mcp.tool()
async def explain_schema(field_name: str) -> dict[str, Any]:
    """``explain_field`` 의 alias — REMAINING_JOBS 명세 (B4) 와 1:1 매칭용.

    동작은 ``explain_field`` 와 완전히 동일. 단순한 AI 에이전트가 명세에 적힌
    이름 그대로 호출해도 작동하도록 둘 다 등록한다.

    Args:
        field_name: 예시 — 'data_type', 'classification', 'capabilities'.
    """
    return await explain_field(field_name)


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
