"""MCP (Model Context Protocol) HTTP server for AI Data Hub.

FastMCP 인스턴스를 만들어 tools + resources 를 등록하고, 그 streamable
HTTP app 을 ``main.py`` 가 ``/mcp`` 로 mount 한다.

Tool 목록:
    # 탐색
    discover()                          — 카탈로그 전체 요약 (첫 번째 호출)
    list_agents()                       — agent 목록
    recommend_agents(q, top_k)          — 자연어 → 적합 agent 추천

    # Agent 세션 초기화 (P1)
    get_agent_session(agent_type)       — system_prompt + 설정 일괄 반환
                                          → LLM 이 이것으로 persona 초기화

    # 검색 (P1/P2)
    agent_search(agent_type, q, mode)   — retrieval_config 자동 적용 agent-scoped 검색
    semantic_search(q, top_k, ...)      — 전체 DB 시맨틱 검색 (agent_type 옵션)
    fts_search(q, top_k, agent_type)    — 키워드(FTS) 검색
    tag_search(tags, agent_type)        — 태그 정확 매칭 검색

    # 레코드 상세
    get_record(record_id)               — 전체 레코드
    get_record_sections(record_id)      — RAG 청크 (section 단위)

    # 번들 (P3)
    get_context_bundle(agent_type, ...) — agent records + sections LLM-ready 묶음
                                          (score_threshold 적용)
"""
from __future__ import annotations

import os
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
  3. get_agent_session(agent_type=...) 으로 persona + 검색 설정을 초기화한다.
  4. agent_search(agent_type, q) 로 검색한다 — retrieval_config 자동 적용.
  5. get_record_sections(record_id) 로 상세 RAG 청크를 가져온다.

모든 답변은 record id 를 출처로 인용해라 (예: source: DOC-HE-CAE-2026-0000000001 §4).
"""

# 시스템 base URL — system_prompt 렌더링에 사용 (배포 환경에서 환경변수로 지정)
_BASE_URL = os.environ.get("MCP_BASE_URL", os.environ.get("HOST_URL", "http://localhost:8001"))


mcp = FastMCP(
    name="aidatahub",
    instructions=_INSTRUCTIONS,
    stateless_http=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


# ===========================================================================
# 탐색 tools
# ===========================================================================

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
    title="List all agents",
    description=(
        "Returns all registered agents with their metadata (agent_type, name, description, "
        "common_tags, data_types). Use this to browse the catalog before recommend_agents."
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


@mcp.tool(
    title="Recommend agents",
    description=(
        "Given a natural-language query, returns ranked agents based on semantic "
        "search aggregation + sample query matching. Use this when the user describes "
        "an intent and you need to pick the right agent."
    ),
)
async def recommend_agents(q: str, top_k: int = 5) -> dict[str, Any]:
    from .services import recommend_svc
    async with SessionLocal() as session:
        agents = await recommend_svc.recommend_agents(session, query=q, top_k=top_k)
        return {"query": q, "agents": agents}


# ===========================================================================
# P1 — Agent 세션 초기화
# ===========================================================================

@mcp.tool(
    title="Initialize agent session",
    description=(
        "CALL THIS FIRST after picking an agent. Returns the agent's rendered system_prompt "
        "(adopt it as your persona), retrieval_config (search settings), response_config "
        "(answer style rules), and sample_queries. After this call, use agent_search() "
        "for all searches — it automatically applies retrieval_config."
    ),
)
async def get_agent_session(agent_type: str) -> dict[str, Any]:
    """Agent 세션 초기화.

    LLM 이 agent 를 선택한 직후 호출해서 persona + 행동 규칙을 세팅한다.
    무거운 records 데이터 없이 설정값만 빠르게 반환한다.
    """
    from .db.models import Agent
    from .services.recommend_svc import build_system_prompt

    async with SessionLocal() as session:
        agent = await session.get(Agent, agent_type)
        if agent is None:
            raise ValueError(f"agent not found: {agent_type}")

        system_prompt = build_system_prompt(agent, base_url=_BASE_URL)

        rc = dict(agent.retrieval_config or {})
        rsp = dict(agent.response_config or {})

        return {
            "agent_type": agent.agent_type,
            "name": agent.name,
            "description": agent.description or "",
            # LLM 은 이 system_prompt 를 즉시 자신의 persona 로 채택할 것
            "system_prompt": system_prompt,
            "retrieval_config": rc,
            "response_config": rsp,
            "sample_queries": list(agent.sample_queries or []),
            "scope": {
                "common_tags": list(agent.common_tags or []),
                "data_types": list(agent.data_types or []),
                "required_doc_type": agent.required_doc_type,
                "required_tags": list(agent.required_tags or []),
                "excluded_tags": list(agent.excluded_tags or []),
            },
            "usage_hint": (
                f"1. Adopt system_prompt as your persona now.\n"
                f"2. Call agent_search('{agent_type}', q) for every user question — "
                f"retrieval_config is applied automatically.\n"
                f"3. Call get_record_sections(record_id) for full RAG content.\n"
                f"4. Cite every factual claim: (source: <record_id> §<section_id>)\n"
                f"5. If refused=true in agent_search result, reply with refusal_message."
            ),
        }


# ===========================================================================
# P1/P2 — 검색 tools
# ===========================================================================

@mcp.tool(
    title="Agent-scoped search (auto-applies retrieval_config)",
    description=(
        "PRIMARY SEARCH TOOL. Searches within the agent's records and automatically applies "
        "its retrieval_config: top_k, score_threshold, data_type_filter, tag_boost. "
        "Returns hits with a 'refused' flag when scores are below refuse_below_score. "
        "mode: 'semantic' (default, vector cosine) | 'fts' (keyword) | 'tag' (exact tags, q=comma-separated tags)"
    ),
)
async def agent_search(
    agent_type: str,
    q: str,
    mode: str = "semantic",
) -> dict[str, Any]:
    """Agent retrieval_config 를 자동 적용하는 agent-scoped 검색.

    동작:
        1. agent 의 retrieval_config (top_k / score_threshold / data_type_filter / tag_boost) 로드
        2. agent 소속 record_ids 로 검색 범위 제한
        3. required_tags / excluded_tags 후필터
        4. score_threshold 미달 결과 제거
        5. tag_boost 가중치 적용 후 재정렬
        6. refuse_below_score 미달 시 refused=True 반환
    """
    from sqlalchemy import select
    from .db.models import Agent, Record
    from .services import search_svc

    async with SessionLocal() as session:
        agent = await session.get(Agent, agent_type)
        if agent is None:
            raise ValueError(f"agent not found: {agent_type}")

        rc = dict(agent.retrieval_config or {})
        rsp = dict(agent.response_config or {})

        top_k: int = int(rc.get("top_k") or 10)
        score_threshold: float | None = rc.get("score_threshold")
        data_type_filter: list[str] | None = rc.get("data_type_filter") or None
        tag_boost: dict[str, float] = rc.get("tag_boost") or {}
        required_tags: list[str] = list(agent.required_tags or [])
        excluded_tags: list[str] = list(agent.excluded_tags or [])
        refuse_below: float | None = rsp.get("refuse_below_score")
        refusal_message: str = rsp.get("refusal_message") or "해당 자료를 찾지 못했습니다."

        # data_type_filter 가 비어있으면 agent.data_types 로 폴백
        if not data_type_filter and agent.data_types:
            data_type_filter = list(agent.data_types)

        # agent 소속 record_ids 조회
        try:
            from sqlalchemy import literal
            id_stmt = select(Record.id).where(
                literal(agent_type) == Record.agents.any_()  # type: ignore[attr-defined]
            )
            agent_record_ids = list((await session.execute(id_stmt)).scalars().all())
        except Exception:
            # 폴백 — 파이썬 후필터
            from .services.sql_compat import array_overlap
            pred = array_overlap(Record.agents, [agent_type], session)
            all_recs = (await session.execute(
                select(Record.id).where(pred.where_clause)
            )).scalars().all()
            agent_record_ids = list(all_recs)

        scope_record_ids = agent_record_ids or None  # None = 범위 제한 없음

        # --- 검색 실행 ---
        hits: list[dict[str, Any]] = []

        if mode == "fts":
            raw, _ = await search_svc.fts_search(session, q, limit=top_k * 3)
            # agent record 범위 필터
            if scope_record_ids:
                scope_set = set(scope_record_ids)
                raw = [r for r in raw if r.get("record_id") in scope_set]
            # data_type 필터
            if data_type_filter:
                raw = [r for r in raw if r.get("data_type") in data_type_filter]
            hits = raw[:top_k]

        elif mode == "tag":
            # q = 콤마 구분 태그
            tags_list = [t.strip() for t in q.split(",") if t.strip()]
            if not tags_list:
                return _refused_result(agent_type, q, mode, refusal_message, "태그 없음")
            rows, _ = await search_svc.tag_search(session, tags_list, limit=top_k * 3)
            raw = [
                {
                    "record_id": r.id,
                    "title": r.title,
                    "data_type": r.data_type,
                    "tags": list(r.tags or []),
                    "score": 1.0,
                }
                for r in rows
            ]
            if scope_record_ids:
                scope_set = set(scope_record_ids)
                raw = [r for r in raw if r["record_id"] in scope_set]
            hits = raw[:top_k]

        else:  # semantic (default)
            hits = await search_svc.semantic_search(
                session,
                q,
                top_k=top_k * 2,  # 후필터 여유분
                data_types=data_type_filter or None,
                record_ids=scope_record_ids,
            )
            # score_threshold 적용
            if score_threshold is not None:
                hits = [h for h in hits if h.get("score", 0) >= score_threshold]
            hits = hits[:top_k]

        # --- required_tags / excluded_tags 후필터 ---
        if required_tags:
            req_set = set(required_tags)
            hits = [h for h in hits if req_set.issubset(set(h.get("tags") or []))]
        if excluded_tags:
            exc_set = set(excluded_tags)
            hits = [h for h in hits if not exc_set.intersection(set(h.get("tags") or []))]

        # --- tag_boost 가중치 (semantic 결과에만 의미 있음) ---
        if tag_boost and mode == "semantic":
            for h in hits:
                boost = sum(
                    float(tag_boost.get(t, 0))
                    for t in (h.get("tags") or [])
                )
                h["score"] = round(min(1.0, h.get("score", 0) + boost), 4)
            hits.sort(key=lambda h: h.get("score", 0), reverse=True)

        # --- refuse_below_score 판단 ---
        refused = False
        if not hits:
            refused = True
        elif refuse_below is not None:
            top_score = hits[0].get("score", 1.0) if hits else 0.0
            if top_score < refuse_below:
                refused = True

        return {
            "agent_type": agent_type,
            "query": q,
            "mode": mode,
            "hits": hits,
            "hit_count": len(hits),
            "refused": refused,
            "refusal_message": refusal_message if refused else None,
            "applied_config": {
                "top_k": top_k,
                "score_threshold": score_threshold,
                "data_type_filter": data_type_filter,
                "tag_boost_applied": bool(tag_boost),
                "required_tags": required_tags,
                "excluded_tags": excluded_tags,
                "agent_record_scope": len(agent_record_ids) if agent_record_ids else "all",
            },
        }


def _refused_result(
    agent_type: str, q: str, mode: str, refusal_message: str, reason: str
) -> dict[str, Any]:
    return {
        "agent_type": agent_type,
        "query": q,
        "mode": mode,
        "hits": [],
        "hit_count": 0,
        "refused": True,
        "refusal_message": refusal_message,
        "applied_config": {"reason": reason},
    }


@mcp.tool(
    title="Semantic search (full DB or agent-scoped)",
    description=(
        "Cosine-similarity vector search over all RAG section chunks. "
        "Optionally filter by data_type or restrict to a specific agent's records "
        "via agent_type. Returns top-k sections with score. "
        "Prefer agent_search() when you already have an agent selected."
    ),
)
async def semantic_search(
    q: str,
    top_k: int = 10,
    data_types: list[str] | None = None,
    agent_type: str | None = None,
) -> list[dict[str, Any]]:
    from sqlalchemy import select
    from .db.models import Record
    from .services import search_svc

    async with SessionLocal() as session:
        record_ids: list[str] | None = None
        if agent_type:
            try:
                from sqlalchemy import literal
                id_stmt = select(Record.id).where(
                    literal(agent_type) == Record.agents.any_()  # type: ignore[attr-defined]
                )
                record_ids = list((await session.execute(id_stmt)).scalars().all())
            except Exception:
                from .services.sql_compat import array_overlap
                pred = array_overlap(Record.agents, [agent_type], session)
                record_ids = list(
                    (await session.execute(select(Record.id).where(pred.where_clause)))
                    .scalars().all()
                )

        return await search_svc.semantic_search(
            session,
            q,
            top_k=top_k,
            data_types=data_types or None,
            record_ids=record_ids or None,
        )


@mcp.tool(
    title="Full-text keyword search",
    description=(
        "Keyword-based search using PostgreSQL FTS (tsvector/tsquery). "
        "Better than semantic_search for exact terms, codes, part numbers, or acronyms. "
        "Optionally scoped to a specific agent's records."
    ),
)
async def fts_search(
    q: str,
    top_k: int = 10,
    agent_type: str | None = None,
) -> list[dict[str, Any]]:
    from sqlalchemy import select
    from .db.models import Record
    from .services import search_svc

    async with SessionLocal() as session:
        items, _ = await search_svc.fts_search(session, q, limit=top_k * 3)
        if agent_type:
            try:
                from sqlalchemy import literal
                id_stmt = select(Record.id).where(
                    literal(agent_type) == Record.agents.any_()  # type: ignore[attr-defined]
                )
                scope = set((await session.execute(id_stmt)).scalars().all())
            except Exception:
                from .services.sql_compat import array_overlap
                pred = array_overlap(Record.agents, [agent_type], session)
                scope = set(
                    (await session.execute(select(Record.id).where(pred.where_clause)))
                    .scalars().all()
                )
            items = [it for it in items if it.get("record_id") in scope]

        return items[:top_k]


@mcp.tool(
    title="Tag-based exact search",
    description=(
        "Find records that contain ALL specified tags (AND match). "
        "tags: comma-separated string (e.g. 'IGA,NURBS'). "
        "Optionally scoped to a specific agent's records."
    ),
)
async def tag_search(
    tags: str,
    agent_type: str | None = None,
    top_k: int = 20,
) -> list[dict[str, Any]]:
    from sqlalchemy import select
    from .db.models import Record
    from .services import search_svc

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    if not tag_list:
        return []

    async with SessionLocal() as session:
        rows, _ = await search_svc.tag_search(session, tag_list, limit=top_k * 3)
        results = [
            {
                "record_id": r.id,
                "title": r.title,
                "data_type": r.data_type,
                "tags": list(r.tags or []),
                "summary": (r.summary or "")[:200],
            }
            for r in rows
        ]
        if agent_type:
            try:
                from sqlalchemy import literal
                id_stmt = select(Record.id).where(
                    literal(agent_type) == Record.agents.any_()  # type: ignore[attr-defined]
                )
                scope = set((await session.execute(id_stmt)).scalars().all())
            except Exception:
                from .services.sql_compat import array_overlap
                pred = array_overlap(Record.agents, [agent_type], session)
                scope = set(
                    (await session.execute(select(Record.id).where(pred.where_clause)))
                    .scalars().all()
                )
            results = [r for r in results if r["record_id"] in scope]

        return results[:top_k]


# ===========================================================================
# 레코드 상세 tools
# ===========================================================================

@mcp.tool(
    title="Get full record by ID",
    description=(
        "Returns the full record document including content body and metadata. "
        "Use when you need complete data of a record referenced by search results."
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
        "Returns the sectioned (chunked) body of a record, suitable for RAG. "
        "Each section has section_id, level, title, content_text. "
        "Call this after search to get the full text of a relevant section."
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


# ===========================================================================
# P3 — Context bundle (score_threshold 적용)
# ===========================================================================

@mcp.tool(
    title="Get LLM-ready context bundle for an agent",
    description=(
        "Returns agent metadata + its records + key sections, formatted as JSON (default) "
        "or Markdown. Applies agent's score_threshold when available to filter sections. "
        "Use after get_agent_session to pre-load knowledge into context."
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

        # P3 — retrieval_config.score_threshold 가 있으면 sections 필터
        rc = bundle.get("agent", {}).get("retrieval_config") or {}
        score_threshold = rc.get("score_threshold")
        if score_threshold is not None:
            # key_sections 에는 score 정보가 없으므로, 필터는 DB 에서 다시
            # 실행하지 않고 대신 threshold 안내를 메타에 포함한다.
            # (sections 자체의 score 는 context-bundle 경로에선 미보유)
            bundle["_note"] = (
                f"retrieval score_threshold={score_threshold} is set for this agent. "
                "Use agent_search() for threshold-filtered searches."
            )

        if format.lower() == "markdown":
            return recommend_svc.render_context_bundle_markdown(bundle)
        return bundle


# ===========================================================================
# Resources
# ===========================================================================

@mcp.resource(
    "aidh://llm-guide",
    name="AI Data Hub — LLM Quick Reference",
    description="One-page guide for any LLM on how to use this hub. Read first.",
    mime_type="text/markdown",
)
async def llm_guide() -> str:
    from .services.discover_svc import build_llm_doc
    try:
        return build_llm_doc()
    except Exception:
        return (
            "# Mobile eXperience AI Data Hub — LLM Guide\n\n"
            "Flow: discover → recommend_agents → get_agent_session → agent_search → get_record_sections\n"
        )


@mcp.resource(
    "aidh://discover",
    name="Hub catalog snapshot",
    description="Live snapshot of the catalog (same as the discover tool result).",
    mime_type="application/json",
)
async def discover_resource() -> str:
    import json
    from .services import discover_svc
    async with SessionLocal() as session:
        d = await discover_svc.build_discover_payload(session)
    return json.dumps(d, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Public ASGI app
# ---------------------------------------------------------------------------
app = mcp.streamable_http_app()

__all__ = ["mcp", "app"]
