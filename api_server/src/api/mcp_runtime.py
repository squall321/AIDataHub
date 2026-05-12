"""MCP (Model Context Protocol) HTTP server for AI Data Hub.

FastMCP 인스턴스를 만들어 7 tools + 2 resources 를 등록하고, 그 streamable
HTTP app 을 ``main.py`` 가 ``/mcp`` 로 mount 한다.

Cline / Claude Desktop / Claude Code 등 MCP 클라이언트가 등록하면 도구를
자동 발견하고 표준 JSON-RPC ``tools/call`` 로 호출 가능.

설계 노트:
    - 도구 내부는 우리 service layer 를 in-process 직접 호출 (REST 우회).
    - DB 세션은 ``SessionLocal()`` 으로 매 호출마다 새로 열고 닫는다.
    - 인증은 PoC 단계에서 생략 — ``AUTH_REQUIRED=true`` 환경에서는 별도
      미들웨어 또는 token_verifier 통합이 필요 (out-of-scope this cycle).
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .db.base import SessionLocal


_INSTRUCTIONS = """\
Mobile eXperience AI Data Hub — MCP server.

이 허브는 사업부 문서/측정/시뮬레이션/CAD/로그/양식을 통합한 데이터 카탈로그다.
정상적인 흐름:
  1. discover() 또는 list_agents() 로 시스템 전모를 파악한다.
  2. recommend_agents(q="자연어") 로 가장 적합한 agent 를 받는다.
  3. get_context_bundle(agent_type=...) 로 해당 agent 의 RAG payload 를 받는다.
  4. 후속 질의는 semantic_search 또는 get_record_sections 로 보강한다.

모든 답변은 record id 를 출처로 인용해라 (예: source: DOC-HE-CAE-2026-0000000001 §4).
"""


# ---------------------------------------------------------------------------
# FastMCP instance
# ---------------------------------------------------------------------------
# ``stateless_http=True`` — 매 요청마다 세션을 새로 (인스턴스 상태 의존 X).
# 멀티 워커 / 재시작에 안전.
mcp = FastMCP(
    name="aidatahub",
    instructions=_INSTRUCTIONS,
    stateless_http=True,
    streamable_http_path="/",  # 우리가 /mcp 로 mount 할 거라 sub-path 는 / 로
    # DNS rebinding 보호 비활성화 — 외부 IP/사내망에서 접근하는 PoC 단계용.
    # 운영 환경에서는 ``allowed_hosts=["host:port", ...]`` 로 화이트리스트 명시 권장.
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool(
    title="System catalog (one-shot)",
    description=(
        "Fetches the entire hub catalog summary: total records, by data_type, by team, "
        "and the registered agents with their record counts. Call this first."
    ),
)
async def discover() -> dict[str, Any]:
    from .services import discover_svc

    async with SessionLocal() as session:
        return await discover_svc.build_discover_payload(session)


@mcp.tool(
    title="Recommend agents",
    description=(
        "Given a natural-language query, returns ranked agents based on semantic "
        "search aggregation over registered records. Use this when the user describes "
        "an intent and you need to pick the right agent."
    ),
)
async def recommend_agents(
    q: str,
    top_k: int = 5,
) -> dict[str, Any]:
    from .services import recommend_svc

    async with SessionLocal() as session:
        agents = await recommend_svc.recommend_agents(
            session, query=q, top_k=top_k
        )
        return {"query": q, "agents": agents}


@mcp.tool(
    title="Get LLM-ready context bundle for an agent",
    description=(
        "Returns agent metadata + its records + key sections, formatted as either "
        "JSON (default) or Markdown. Use this immediately after picking an agent to "
        "load your knowledge into the conversation context."
    ),
)
async def get_context_bundle(
    agent_type: str,
    format: str = "json",
    max_records: int = 10,
    max_sections_per_record: int = 8,
) -> dict[str, Any] | str:
    from .services import recommend_svc

    async with SessionLocal() as session:
        bundle = await recommend_svc.build_context_bundle(
            session,
            agent_type=agent_type,
            max_records=max_records,
            max_sections_per_record=max_sections_per_record,
        )
        if bundle is None:
            raise ValueError(f"agent not found: {agent_type}")
        if format.lower() == "markdown":
            return recommend_svc.render_context_bundle_markdown(bundle)
        return bundle


@mcp.tool(
    title="Semantic search",
    description=(
        "Cosine-similarity search over RAG section chunks (embeddings). Optionally "
        "filter by data_type. Returns top-k sections with their record metadata."
    ),
)
async def semantic_search(
    q: str,
    top_k: int = 10,
    data_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    from .services import search_svc

    async with SessionLocal() as session:
        return await search_svc.semantic_search(
            session,
            q,
            top_k=top_k,
            data_types=data_types,
        )


@mcp.tool(
    title="Get full record by ID",
    description=(
        "Returns the full record document including content body. Use this when you "
        "need the complete data of a record referenced by id (e.g., from search results)."
    ),
)
async def get_record(record_id: str) -> dict[str, Any]:
    from sqlalchemy import select

    from .db.models import Record

    async with SessionLocal() as session:
        rec = (
            await session.execute(select(Record).where(Record.id == record_id))
        ).scalar_one_or_none()
        if rec is None:
            raise ValueError(f"record not found: {record_id}")
        return {
            "id": rec.id,
            "data_type": rec.data_type,
            "team": rec.team,
            "group": rec.group,
            "year": rec.year,
            "seq": rec.seq,
            "title": rec.title,
            "summary": rec.summary or "",
            "tags": list(rec.tags or []),
            "agents": list(rec.agents or []),
            "doc_type": rec.doc_type,
            "content": rec.content,
        }


@mcp.tool(
    title="Get RAG section chunks for a record",
    description=(
        "Returns the sectioned (chunked) body of a record, suitable for RAG. Each "
        "section has section_id, level, title, content_text."
    ),
)
async def get_record_sections(record_id: str, limit: int = 50) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from .db.models import RecordSection

    async with SessionLocal() as session:
        secs = (
            await session.execute(
                select(RecordSection)
                .where(RecordSection.record_id == record_id)
                .order_by(RecordSection.id.asc())
                .limit(limit)
            )
        ).scalars().all()
        return [
            {
                "section_id": s.section_id,
                "level": s.level,
                "title": s.title,
                "content_text": s.content_text or "",
            }
            for s in secs
        ]


@mcp.tool(
    title="List all agents",
    description=(
        "Returns all registered agents with their metadata. Use this to see the catalog "
        "of specialized agents before calling recommend_agents."
    ),
)
async def list_agents() -> list[dict[str, Any]]:
    from sqlalchemy import select

    from .db.models import Agent

    async with SessionLocal() as session:
        rows = (
            await session.execute(select(Agent).order_by(Agent.agent_type))
        ).scalars().all()
        return [
            {
                "agent_type": a.agent_type,
                "name": a.name,
                "description": a.description or "",
                "common_tags": list(a.common_tags or []),
                "data_types": list(a.data_types or []),
            }
            for a in rows
        ]


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------
@mcp.resource(
    "aidh://llm-guide",
    name="AI Data Hub — LLM Quick Reference",
    description="One-page guide for any LLM on how to use this hub. Read first.",
    mime_type="text/markdown",
)
async def llm_guide() -> str:
    """Mirror of GET /api/docs/llm.txt — quick reference for any LLM."""
    from .services.discover_svc import build_llm_doc
    try:
        return build_llm_doc()
    except Exception:
        return (
            "# Mobile eXperience AI Data Hub — LLM Guide\n\n"
            "First call `discover` tool to learn the catalog. Then use "
            "`recommend_agents` for intent → agent matching. After picking an agent, "
            "load `get_context_bundle` to populate RAG context.\n"
        )


@mcp.resource(
    "aidh://discover",
    name="Hub catalog snapshot",
    description="Live snapshot of the catalog (same as the `discover` tool result).",
    mime_type="application/json",
)
async def discover_resource() -> str:
    import json

    from .services import discover_svc

    async with SessionLocal() as session:
        d = await discover_svc.build_discover_payload(session)
    return json.dumps(d, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Public ASGI app — main.py 가 ``app.mount("/mcp", mcp_runtime.app)`` 로 mount.
# ---------------------------------------------------------------------------
app = mcp.streamable_http_app()


__all__ = ["mcp", "app"]
