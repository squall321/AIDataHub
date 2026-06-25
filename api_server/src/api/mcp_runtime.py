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
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .db.base import SessionLocal


# JSON Schema enum 으로 노출되어 클라이언트(Claude/Cline) 자동완성 + 오타 차단.
SearchMode = Literal["semantic", "fts", "tag", "hybrid"]


_INSTRUCTIONS = """\
Mobile eXperience AI Data Hub — MCP server.

이 허브는 사업부 문서/측정/시뮬레이션/CAD/로그/양식을 통합한 데이터 카탈로그다.
정상적인 흐름:
  1. discover() 또는 list_agents() 로 시스템 전모를 파악한다.
  2. recommend_agents(q="자연어") 로 가장 적합한 agent 를 받는다.
  3. get_agent_session(agent_type=...) 으로 persona + 검색 설정을 초기화한다.
  4. agent_search(agent_type, q) 로 검색한다 — retrieval_config 자동 적용.
  5. get_record_sections(record_id) 로 상세 RAG 청크를 가져온다.

[중요 — agent persona 적용 규약]
get_agent_session() 이 반환하는 ``system_prompt`` 는 그 시점부터 이 세션의
지배 규칙(governing instructions)이다. 그 안의 역할/응답 형식/인용 규약/거부
규칙을 이후 모든 답변에 그대로 적용하라. agent 를 바꾸려면 get_agent_session
을 다시 호출하고 새 system_prompt 로 교체하라. (MCP stateless 특성상 서버가
세션 instructions 를 자동 교체하지 못하므로, 이 채택은 클라이언트 LLM 책임이다.)

모든 답변은 record id 를 출처로 인용해라 (예: source: DOC-HE-CAE-2026-0000000001 §4).

[중요 — 데이터 주입(등록) 규약: 일관 분류 유도]
새 데이터를 import_record 로 넣기 전에 반드시:
  1. find_similar_data(headers/caption) 로 기존 비슷한 데이터를 먼저 확인한다.
     수많은 데이터가 쌓여도 서로 일관되게 분류돼야 나중에 찾을 수 있다.
  2. 응답의 suggested(doc_type/tags/graph_type)는 유사 데이터 기준 제안이다 —
     검토해서 record 에 채워라 (비슷한 데이터는 비슷하게 분류해 일관성 유지).
  3. needs_human(team/group)은 자동으로 채우지 마라. 후보(candidates)를
     사용자에게 보여주고 "이 데이터는 어느 팀/그룹인가요?" 물어 사람이 정하게 하라.
     유사도가 높아도 다른 팀 데이터일 수 있어 team/group 오분류는 데이터를
     영영 못 찾게 만든다.
  4. 유사 데이터가 거의 없으면(confidence=low/none) 분류를 추측하지 말고
     describe_record_schema 로 가능한 값을 보여주며 사용자에게 직접 물어라.
이렇게 하면 자동화/대량 주입에서도 데이터가 일관 분류되어 상호 검색이 된다.
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
async def recommend_agents(
    q: str,
    top_k: int = 5,
    top_k_tools: int = 3,
    agent_type: str | None = None,
) -> dict[str, Any]:
    """자연어 → ranked agents (+ Wave-7 P1: relevant_tools 동봉, P2: agent context 필터).

    0건일 때는 catalog 전체에서 후보 + sample_queries 를 함께 반환해 LLM 이
    사용자에게 재질의할 단서가 되도록 한다.

    Args:
        agent_type: 호출자 agent context (Wave-7 P2). 설정 시 매니페스트 정책
                    (restrict/require/exclude) 적용 후 relevant_tools 필터.
    """
    from sqlalchemy import select

    from .db.models import Agent, MCPUpload
    from .services import recommend_svc, tool_embedding_svc, tool_visibility_svc

    async with SessionLocal() as session:
        agents = await recommend_svc.recommend_agents(session, query=q, top_k=top_k)
        # Wave-7 P1+P2 — 도구 검색 + 정책 필터 (별도 실패해도 agent 추천은 유지)
        relevant_tools: list[dict[str, Any]] = []
        try:
            k = max(0, min(int(top_k_tools), 10))
            if k > 0:
                over_fetch = min(k * 3, 20)
                raw = await tool_embedding_svc.search_tools(session, q, top_k=over_fetch)
                if agent_type and raw:
                    names = [r["name"] for r in raw]
                    rows = (
                        await session.execute(
                            select(MCPUpload).where(MCPUpload.name.in_(names))
                        )
                    ).scalars().all()
                    by_name = {r.name: r.manifest for r in rows}
                    enriched = [{**r, "manifest": by_name.get(r["name"], {})} for r in raw]
                    filt = await tool_visibility_svc.filter_tools_for_agent(
                        session, enriched, agent_type=agent_type
                    )
                    relevant_tools = [
                        {kk: vv for kk, vv in r.items() if kk != "manifest"}
                        for r in filt
                    ][:k]
                else:
                    relevant_tools = raw[:k]
        except Exception as e:  # pragma: no cover — defensive
            import logging as _l
            _l.getLogger(__name__).warning(
                "relevant_tools 검색 실패 (agent 응답은 유지): %s", e
            )

        if agents:
            return {
                "query": q,
                "agents": agents,
                "relevant_tools": relevant_tools,
                "fallback": False,
            }

        # 0건 폴백 — 전체 agent 의 메타 + sample_queries 상위 일부.
        rows = (
            await session.execute(select(Agent).order_by(Agent.agent_type))
        ).scalars().all()
        catalog = [
            {
                "agent_type": a.agent_type,
                "name": a.name,
                "description": (a.description or "")[:200],
                "common_tags": list(a.common_tags or []),
                "data_types": list(a.data_types or []),
                "sample_queries": list((a.sample_queries or []))[:3],
            }
            for a in rows
        ]
        return {
            "query": q,
            "agents": [],
            "relevant_tools": relevant_tools,
            "fallback": True,
            "hint": (
                "No matching agents for the query. Ask the user to clarify "
                "intent, or pick a candidate from `catalog` below whose "
                "`sample_queries` resemble the user's need."
            ),
            "catalog": catalog,
        }


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
        "mode: 'semantic' (default, vector cosine) | 'fts' (keyword tsvector) | "
        "'hybrid' (semantic + fts via Reciprocal Rank Fusion — most robust) | "
        "'tag' (exact tags, q=comma-separated tags)"
    ),
)
async def agent_search(
    agent_type: str,
    q: str,
    mode: SearchMode = "semantic",
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
        # Migration 0017 — 계층 깊이 필터. retrieval_config.max_depth 가
        # 지정되면 depth <= max_depth 인 record 만 검색 (예: 0 = campaign만,
        # 미지정 = 전체). ID 인코딩 대신 depth 컬럼으로 "조회 여부" 제어.
        _md = rc.get("max_depth")
        max_depth: int | None = int(_md) if isinstance(_md, (int, float)) and not isinstance(_md, bool) else None
        required_tags: list[str] = list(agent.required_tags or [])
        excluded_tags: list[str] = list(agent.excluded_tags or [])
        refuse_below: float | None = rsp.get("refuse_below_score")
        refusal_message: str = rsp.get("refusal_message") or "해당 자료를 찾지 못했습니다."

        # data_type_filter 가 비어있으면 agent.data_types 로 폴백
        if not data_type_filter and agent.data_types:
            data_type_filter = list(agent.data_types)

        # agent 소속 record_ids 조회 (max_depth 지정 시 depth 필터 동시 적용)
        try:
            from sqlalchemy import literal
            id_stmt = select(Record.id).where(
                literal(agent_type) == Record.agents.any_()  # type: ignore[attr-defined]
            )
            if max_depth is not None:
                id_stmt = id_stmt.where(Record.depth <= max_depth)
            agent_record_ids = list((await session.execute(id_stmt)).scalars().all())
        except Exception:
            # 폴백 — 파이썬 후필터
            from .services.sql_compat import array_overlap
            pred = array_overlap(Record.agents, [agent_type], session)
            fb_stmt = select(Record.id).where(pred.where_clause)
            if max_depth is not None:
                fb_stmt = fb_stmt.where(Record.depth <= max_depth)
            all_recs = (await session.execute(fb_stmt)).scalars().all()
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

        elif mode == "hybrid":
            # semantic + fts RRF 결합 — 패러프레이즈와 정확 키워드 모두 강함.
            # tag_boost / score_threshold 는 RRF 후 파이썬에서 적용.
            raw_hits = await search_svc.hybrid_search(
                session,
                q,
                top_k=max(top_k, top_k * 2),  # 후필터 위해 여유
                data_types=data_type_filter or None,
                record_ids=scope_record_ids,
            )
            # tag_boost 가산 + min_score 필터
            if tag_boost:
                for h in raw_hits:
                    delta = sum(tag_boost.get(t, 0.0) for t in h.get("tags") or [])
                    if delta:
                        h["score"] = round(min(1.0, (h.get("score") or 0.0) + delta), 6)
                raw_hits.sort(key=lambda x: x.get("score", 0.0), reverse=True)
            if score_threshold is not None:
                raw_hits = [h for h in raw_hits if (h.get("score") or 0.0) >= score_threshold]
            hits = raw_hits[:top_k]

        else:  # semantic (default)
            # tag_boost / score_threshold 는 search_svc.semantic_search 의
            # 1급 파라미터로 위임 — 가산+재정렬+필터를 한 곳에서 일관 처리.
            hits = await search_svc.semantic_search(
                session,
                q,
                top_k=top_k,
                data_types=data_type_filter or None,
                record_ids=scope_record_ids,
                tag_boost=tag_boost or None,
                min_score=score_threshold,
            )

        # --- required_tags / excluded_tags 후필터 ---
        if required_tags:
            req_set = set(required_tags)
            hits = [h for h in hits if req_set.issubset(set(h.get("tags") or []))]
        if excluded_tags:
            exc_set = set(excluded_tags)
            hits = [h for h in hits if not exc_set.intersection(set(h.get("tags") or []))]

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
                "max_depth": max_depth,
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
    title="Hybrid search (semantic + FTS via RRF)",
    description=(
        "Combines semantic vector search and full-text keyword search using "
        "Reciprocal Rank Fusion (RRF, k=60). Most robust for queries that mix "
        "concepts and exact terms (e.g. acronyms, model codes, paraphrased intent). "
        "Optionally scoped to a specific agent's records."
    ),
)
async def hybrid_search(
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
        return await search_svc.hybrid_search(
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
    title="정형 필터로 레코드 목록 조회",
    description=(
        "team/group/doc_type/data_type/tags/agent/year + 제목·요약 키워드(q) 로 "
        "레코드 목록을 거른다. 의미검색(semantic_search)이 아니라 '조건에 맞는 것 "
        "나열'이 필요할 때 — 예: 'HE 팀 manual 문서 목록', '2026년 VOC 데이터'. "
        "본문은 빼고 경량 요약(id/title/team/group/doc_type/tags/summary)만 반환해 "
        "토큰을 아낀다. 전체 본문은 get_record 로. tags 는 모두 포함(AND), agents 는 "
        "겹침(OR). 결과의 total 로 더 있는지 판단해 offset 으로 페이지."
    ),
)
async def list_records(
    team: str = "",
    group: str = "",
    doc_type: str = "",
    data_type: str = "",
    tags: list[str] | None = None,
    agents: list[str] | None = None,
    year: int = 0,
    q: str = "",
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    from .services import record_query_svc

    async with SessionLocal() as session:
        rows, total = await record_query_svc.query_records(
            session,
            team=team or None, group=group or None, doc_type=doc_type or None,
            data_type=data_type or None, tags=tags or None, agents=agents or None,
            year=year or None, q=q or None,
            limit=max(1, min(int(limit), 100)), offset=max(0, int(offset)),
        )
        return {
            "total": total,
            "count": len(rows),
            "offset": offset,
            "items": [record_query_svc.to_summary(r) for r in rows],
        }


@mcp.tool(
    title="DATA 표 집계 (avg/max/min/sum/count + group_by)",
    description=(
        "DATA 레코드(표)의 통계를 서버가 계산해 돌려준다 — 전체 행을 받아 직접 "
        "계산하지 않아도 된다. op=avg|max|min|sum|count, column=대상 컬럼명(count "
        "외 필수), group_by=그룹 컬럼(옵션), where='컬럼:값' 사전필터(옵션). "
        "예: record_id 의 stress 평균 → op=avg, column=stress. region 별 yield 최대 "
        "→ op=max, column=yield, group_by=region. 큰 표일수록 토큰 절약 효과가 크다."
    ),
)
async def data_aggregate(
    record_id: str,
    op: str,
    column: str = "",
    group_by: str = "",
    where: str = "",
) -> dict[str, Any]:
    from sqlalchemy import select

    from .db.models import Record
    from .services import data_svc

    async with SessionLocal() as session:
        rec = (
            await session.execute(select(Record).where(Record.id == record_id))
        ).scalar_one_or_none()
        if rec is None:
            return {"error": f"record not found: {record_id}", "code": "not_found"}
        if rec.data_type != "DATA":
            return {"error": f"{record_id} is {rec.data_type}, not DATA (집계는 표 데이터만)",
                    "code": "not_data"}
        c = rec.content if isinstance(rec.content, dict) else {}
        try:
            result = data_svc.aggregate(
                headers=list(c.get("headers") or []),
                units=c.get("units") if isinstance(c.get("units"), list) else None,
                rows=list(c.get("rows") or []),
                op=op, column=column or None, group_by=group_by or None, where=where or None,
            )
        except ValueError as exc:
            return {"error": str(exc), "code": "bad_request", "recoverable": True,
                    "suggestion": "op/column/group_by 를 확인하세요 (data_columns 로 컬럼 목록 확인)."}
        return {"record_id": rec.id, **result}


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
        "Returns sectioned (chunked) body of a record, suitable for RAG. "
        "PREFER passing `sections` (list of section_ids from a search hit) to "
        "fetch only what you cite — keeps token cost low. Omit `sections` to "
        "get the first `limit` sections (default 10). Each section returns "
        "section_id, level, title, content_text."
    ),
)
async def get_record_sections(
    record_id: str,
    sections: list[str] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """선택적 부분 조회.

    - ``sections`` 지정 → 해당 section_id 만 반환 (순서는 입력 순서 유지).
    - ``sections`` 미지정 → 처음 ``limit`` 섹션 (id 오름차순).
    """
    from sqlalchemy import select
    from .db.models import RecordSection

    async with SessionLocal() as session:
        if sections:
            # 입력 순서 보존을 위해 id 정렬 대신 매핑 후 재정렬.
            stmt = (
                select(RecordSection)
                .where(RecordSection.record_id == record_id)
                .where(RecordSection.section_id.in_(list(sections)))
            )
            rows = (await session.execute(stmt)).scalars().all()
            by_id: dict[str, Any] = {s.section_id: s for s in rows}
            ordered = [by_id[sid] for sid in sections if sid in by_id]
            return [
                {
                    "section_id": s.section_id,
                    "level": s.level,
                    "title": s.title,
                    "content_text": s.content_text or "",
                }
                for s in ordered
            ]

        # 부분 미지정 — 처음 limit 섹션
        limit_safe = max(1, min(int(limit), 50))
        secs = (
            await session.execute(
                select(RecordSection)
                .where(RecordSection.record_id == record_id)
                .order_by(RecordSection.id.asc())
                .limit(limit_safe)
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

        # P3 — score_threshold 필터는 recommend_svc.build_context_bundle 가
        # agent.sample_queries 를 relevance anchor 로 삼아 실제 수행한다.
        # 결과는 bundle["retrieval_filter"] 에 노출됨 (applied/threshold/anchor).

        if format.lower() == "markdown":
            return recommend_svc.render_context_bundle_markdown(bundle)
        return bundle


@mcp.tool(
    title="데이터 타입별 형태 룰 + 적용 도구",
    description=(
        "특정 data_type(DOC/DATA/SIM/CAD/LOG/FORM/OTHER) 의 content 형태(필수/"
        "선택 필드, shape)와 그 타입에 적용 가능한 분석 도구(capability_tools)를 "
        "반환한다. 데이터를 저장하거나 분석하기 전에 이걸로 '이 타입은 이렇게 "
        "생겼고 이 도구로 분석한다'를 파악하라. DATA 면 graph_type 권장 어휘도 "
        "준다 — graph_type 인자를 주면 그 그래프에 맞는 도구로 좁혀준다."
    ),
)
async def describe_data_capability(data_type: str, graph_type: str = "") -> dict[str, Any]:
    from .services import discover_svc

    async with SessionLocal() as session:
        return await discover_svc.build_data_capability(
            session, data_type, graph_type=graph_type or None
        )


@mcp.tool(
    title="레코드 메타데이터·보강 가이드",
    description=(
        "데이터를 제출(import_record)하기 전에 '어떤 메타데이터로 보강할 수 있고 "
        "어떻게 채우는지'를 안내한다. 반환: 보강 필드(doc_type/tags/summary/agents/"
        "classification/subject_keywords/agent_hints 등) 설명 + 선택 가능한 enum "
        "(data_type/classification/status) + 등록된 doc_type 목록 + agent 별 권장 "
        "태그 + 예시 레코드. agent_type 을 주면 그 챗봇에 맞는 보강 규칙만 좁혀 안내. "
        "필수 필드(title/team/group)만 채우지 말고, 이걸로 doc_type·tags·summary 를 "
        "보강하면 검색 품질이 크게 오른다."
    ),
)
async def describe_record_schema(agent_type: str = "") -> dict[str, Any]:
    from .services import ingest_guide_svc

    async with SessionLocal() as session:
        return await ingest_guide_svc.build_guide(
            session, agent_type=agent_type or None
        )


@mcp.tool(
    title="비슷한 기존 데이터 찾기 (제안형 자동분류)",
    description=(
        "새 표/문서가 '기존 어떤 데이터와 같은 종류인지' 임베딩 유사도로 찾아 "
        "team/group/graph_type 을 제안한다. 룰 없이 데이터가 늘어도 동작. "
        "헤더(컬럼명)와 caption/title 을 주면 같은 data_type 의 비슷한 레코드 "
        "top-k 와 다수결 제안을 반환한다. 중요: 이건 **제안**이지 확정이 아니다 — "
        "confidence 가 low/medium 이면 사용자에게 'team 을 HE 로 할까요?' 처럼 "
        "반드시 확인하라 (유사도 높아도 다른 종류일 수 있음). import_record 전에 "
        "호출해 suggestions 를 채우는 용도."
    ),
)
async def find_similar_data(
    headers: list[str] | None = None,
    caption: str = "",
    title: str = "",
    data_type: str = "DATA",
    top_k: int = 5,
) -> dict[str, Any]:
    from .services import similarity_svc

    async with SessionLocal() as session:
        return await similarity_svc.suggest_by_similarity(
            session, title=title, caption=caption, headers=headers,
            data_type=data_type, top_k=top_k,
        )


# ===========================================================================
# Write tools — Claude Desktop drag&drop → 우리 DB 규격으로 저장
# (DESKTOP_MCP_MIGRATION_PLAN.md v2 Phase 0+1)
# ===========================================================================

@mcp.tool(
    title="Import a record (drag&drop 표/문서 → 저장)",
    description=(
        "사용자가 붙여넣은 표/문서를 우리 DB 규격으로 검증하거나 저장한다. "
        "흐름: (1) 첨부를 record dict 로 파싱 — DATA 면 content={headers:[...],rows:[[...]]}, "
        "DOC 면 content={sections:[...]}. (2) dry_run=true 로 먼저 호출 → 부족한 필드가 "
        "있으면 status='incomplete' 와 ask_user(예: ['title','team','group']) 를 돌려준다. "
        "그 필드만 사용자에게 물어 같은 record 에 합친다. suggestions 에 제안값이 오면 "
        "confidence 를 보고 낮으면 반드시 확인하라. (3) 모두 채워지면 status='ready'. "
        "(4) 그때 같은 record 로 dry_run=false 재호출 → status='saved' + id. "
        "주의: team/group 은 절대 임의로 채우지 말고 사용자에게 확인하라 — 틀리면 "
        "엉뚱한 곳에 저장되어 못 찾는다. 각 호출은 독립이므로 record 전체를 매번 보낸다. "
        "보강 권장: 필수 필드만 채우지 말고 describe_record_schema 로 doc_type·tags·"
        "summary 등을 확인해 함께 채우면 검색 품질이 오른다."
    ),
)
async def import_record(
    record: dict[str, Any],
    dry_run: bool = True,
    api_key: str = "",
    agent_type: str = "",
) -> dict[str, Any]:
    from .services import mcp_write_svc

    return await mcp_write_svc.run_import(
        record=record,
        dry_run=dry_run,
        api_key=api_key or None,
        agent_type=agent_type or None,
    )


# ── Agent / DocType 정의 (VSCode extension → 대화로 이관, Phase 2) ──────────

@mcp.tool(
    title="Agent 정의 폼 안내",
    description=(
        "agent(검색 챗봇 페르소나)를 만들 때 채워야 할 필드와 설명을 반환한다. "
        "create_agent 호출 전에 이걸로 무엇이 필요한지 파악하라."
    ),
)
async def describe_agent_schema() -> dict[str, Any]:
    from .services import mcp_write_svc
    return await mcp_write_svc.describe_agent_schema()


@mcp.tool(
    title="Agent 정의 초안 생성 (저장 안 함)",
    description=(
        "기존 레코드 신호 + 자연어 의도(hint)로 agent 정의 초안을 만든다. "
        "예: hint='배터리 셀 시험보고서만 찾는 분석가'. record_ids/filter_tags/"
        "filter_data_types 로 데이터 군을 한정할 수 있다. 결과의 system_prompt/"
        "sample_queries 를 사람이 읽기 좋게 보여주고, 다듬을지 물은 뒤 create_agent 로 저장."
    ),
)
async def draft_agent(
    hint: str = "",
    record_ids: list[str] | None = None,
    filter_tags: list[str] | None = None,
    filter_data_types: list[str] | None = None,
    api_key: str = "",
) -> dict[str, Any]:
    from .services import mcp_write_svc
    return await mcp_write_svc.draft_agent(
        hint=hint or None, record_ids=record_ids, filter_tags=filter_tags,
        filter_data_types=filter_data_types, api_key=api_key or None,
    )


@mcp.tool(
    title="Agent 신규 등록",
    description=(
        "agent(검색 챗봇 페르소나)를 저장한다. agent dict 는 최소 agent_type + "
        "system_prompt, 권장 sample_queries(3~5개) + data_types. 필드는 "
        "describe_agent_schema 참고. sample_queries 는 저장 시 자동 임베딩된다. "
        "이미 있으면 patch_agent 를 쓰라."
    ),
)
async def create_agent(agent: dict[str, Any], api_key: str = "") -> dict[str, Any]:
    from .services import mcp_write_svc
    return await mcp_write_svc.create_agent(agent=agent, api_key=api_key or None)


@mcp.tool(
    title="Agent 부분 수정",
    description=(
        "기존 agent 의 일부 필드만 수정한다. patch dict 에 바꿀 필드만 담는다 "
        "(예: {sample_queries:[...], system_prompt:'...'}). 빈 stub agent 에 "
        "persona 를 채울 때 유용."
    ),
)
async def patch_agent(agent_type: str, patch: dict[str, Any], api_key: str = "") -> dict[str, Any]:
    from .services import mcp_write_svc
    return await mcp_write_svc.patch_agent(agent_type=agent_type, patch=patch, api_key=api_key or None)


@mcp.tool(
    title="Agent 에 매칭 레코드 연결",
    description=(
        "agent 의 기대 스키마(required_doc_type / required_tags / data_types)에 "
        "부합하는 기존 레코드를 그 agent 에 자동 바인딩한다. create_agent 로 챗봇을 "
        "정의한 뒤 '이 챗봇이 검색할 데이터를 연결'하는 마지막 단계. agent 정의에 "
        "필터 조건이 있어야 매칭된다(없으면 0건)."
    ),
)
async def bind_records_to_agent(agent_type: str, api_key: str = "") -> dict[str, Any]:
    from .services import mcp_write_svc
    return await mcp_write_svc.bind_records_to_agent(agent_type=agent_type, api_key=api_key or None)


@mcp.tool(
    title="DocType 목록",
    description="등록된 doc_type(의미 분류) 목록. create_agent 의 required_doc_type 선택에 참고.",
)
async def list_doc_types(api_key: str = "") -> dict[str, Any]:
    from .services import mcp_write_svc
    return await mcp_write_svc.list_doc_types(api_key=api_key or None)


@mcp.tool(
    title="DocType 신규 등록",
    description=(
        "새 doc_type(의미 분류)을 등록한다. doc_type dict 는 code + name 필수, "
        "description/expected_sections 권장. 새 종류 문서를 분류 체계에 추가할 때."
    ),
)
async def create_doc_type(doc_type: dict[str, Any], api_key: str = "") -> dict[str, Any]:
    from .services import mcp_write_svc
    return await mcp_write_svc.create_doc_type(doc_type=doc_type, api_key=api_key or None)


@mcp.tool(
    title="파일 정밀 변환 (docx/xlsx/pdf/pptx → record)",
    description=(
        "MCP 는 바이너리 파일을 직접 못 받는다. 두 경로 중 택1: "
        "(A) 표/CSV/텍스트는 직접 파싱해 import_record 로. "
        "(B) docx/xlsx/pdf/pptx 처럼 수식·병합셀·복잡한 표가 있어 정밀 변환이 "
        "필요하면 이 도구를 쓴다 — 사용자가 서버의 inbox 폴더에 파일을 두고 "
        "파일명만 전달하면(경로/.. 불가, 보안상 inbox 하위만) 서버가 변환해 record "
        "초안을 돌려준다. team/group/year 를 알면 함께 주고, 모르면 변환 후 "
        "import_record 가 되묻는다. 결과 record 를 검토 후 import_record 로 저장."
    ),
)
async def convert_file(
    file: str,
    team: str = "",
    group: str = "",
    year: int = 0,
    auto_save: bool = False,
    api_key: str = "",
) -> dict[str, Any]:
    from .services import mcp_write_svc
    return await mcp_write_svc.run_convert(
        file=file, team=team, group=group, year=year,
        auto_save=auto_save, api_key=api_key or None,
    )


# ===========================================================================
# Resources
# ===========================================================================

# ===========================================================================
# Prompts — Claude Desktop "/" 메뉴 / Cline 슬래시에서 자주 쓰는 워크플로 노출
# ===========================================================================

@mcp.prompt(
    name="aidh-onboard",
    title="AI Data Hub 사용법 안내",
    description="AI Data Hub MCP 사용 흐름을 LLM 에게 한 번에 설명한다.",
)
async def prompt_onboard() -> str:
    """allow LLM to self-onboard with the hub's tool surface."""
    return (
        "You are connecting to the Mobile eXperience AI Data Hub (MCP server `aidatahub`).\n"
        "Use these tools in order:\n"
        "  1) discover() — see catalog totals + data_types + agents.\n"
        "  2) recommend_agents(q='<user intent>') — get top agents. If `fallback`\n"
        "     is true in the result, read `catalog` and ask the user to clarify.\n"
        "  3) get_agent_session(agent_type=...) — adopt the returned `system_prompt`\n"
        "     as your persona for the rest of the session.\n"
        "  4) agent_search(agent_type, q, mode='hybrid') — primary search. Use\n"
        "     mode='hybrid' for most queries (semantic + FTS via RRF). Fall back\n"
        "     to mode='fts' for exact part numbers / model codes only.\n"
        "  5) get_record_sections(record_id, sections=[...]) — pull only the\n"
        "     specific section_ids you need to keep token cost low.\n"
        "Cite every factual claim as `(source: <RECORD_ID> §<section_id>)`."
    )


@mcp.prompt(
    name="aidh-find",
    title="질의로 적합 에이전트 + 검색 한 번에",
    description="자연어 질의를 받아 recommend_agents → agent_search 흐름 가이드를 생성.",
)
async def prompt_find(q: str) -> str:
    """Compose a one-shot workflow guide for a given user query."""
    return (
        f"User question: \"{q}\"\n\n"
        "Plan:\n"
        f"  1) Call recommend_agents(q='{q}'). If `fallback`=true, ask the user\n"
        "     to clarify using one of the `sample_queries` in `catalog`.\n"
        "  2) Pick the top agent_type, then call get_agent_session(agent_type).\n"
        "  3) Adopt the returned `system_prompt` as your persona.\n"
        f"  4) Call agent_search(agent_type, q='{q}', mode='hybrid'). If the\n"
        "     result has `refused`=true, return `refusal_message` verbatim.\n"
        "  5) For each promising hit, call get_record_sections(record_id,\n"
        "     sections=[<section_id>]) — only the sections you cite.\n"
        "  6) Answer in ≤3 sentences with `(source: <id> §<section_id>)` citations."
    )


@mcp.prompt(
    name="aidh-cite",
    title="허브 인용 규약 리마인더",
    description="record id 형식 + 인용 형식을 LLM 에게 다시 강조.",
)
async def prompt_cite() -> str:
    return (
        "Cite every factual claim using this exact format:\n"
        "  (source: <RECORD_ID> §<section_id>)\n"
        "Where RECORD_ID = {DATA_TYPE}-{TEAM}-{GROUP}-{YEAR}-{SEQ:010d}\n"
        "  e.g. (source: DOC-HE-CAE-2026-0000000001 §4.2)\n"
        "If no source exists in the hub for a claim, say \"허브에 근거 없음\" instead\n"
        "of guessing numbers. Never fabricate record ids."
    )


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


# ===========================================================================
# 동적 셸스크립트 도구 등록 (사이드카 매니페스트 패턴 — mcp_scripts.py)
# AIDH_MCP_SCRIPTS=off 면 비활성. AIDH_MCP_SCRIPTS_DIR 로 디렉토리 override.
# 기본 위치: <repo>/api_server/mcp_scripts/ — 매니페스트가 없으면 no-op.
# ===========================================================================
try:
    from pathlib import Path as _Path
    from .mcp_scripts import register_all_scripts
    _scripts_dir = _Path(
        os.environ.get("AIDH_MCP_SCRIPTS_DIR")
        or (_Path(__file__).resolve().parent.parent.parent / "mcp_scripts")
    )
    _registered_scripts = register_all_scripts(mcp, _scripts_dir)
    if _registered_scripts:
        import logging as _logging
        _logging.getLogger(__name__).info(
            "mcp_scripts: %d tool(s) registered: %s",
            len(_registered_scripts),
            ", ".join(_registered_scripts),
        )
except Exception:  # pragma: no cover — 동적 등록 실패가 서버를 막아선 안 됨
    import logging as _logging
    _logging.getLogger(__name__).exception(
        "mcp_scripts: registration failed (continuing without script tools)"
    )


# ===========================================================================
# Wave-5 P1 — CLI binary 업로드 도구 자동 등록 (apptainer 컨테이너 강제).
# AIDH_MCP_UPLOADS=off 면 비활성.
# ===========================================================================
try:
    if (os.environ.get("AIDH_MCP_UPLOADS") or "").lower() != "off":
        from .services import mcp_upload_svc as _mcp_upload_svc  # type: ignore[attr-defined]
        if hasattr(_mcp_upload_svc, "register_all_uploads"):
            _registered_uploads = _mcp_upload_svc.register_all_uploads(mcp)
            if _registered_uploads:
                import logging as _logging
                _logging.getLogger(__name__).info(
                    "mcp_uploads: %d tool(s) registered: %s",
                    len(_registered_uploads),
                    ", ".join(_registered_uploads),
                )
except Exception:  # pragma: no cover — Wave-5 P1 미구현 단계는 silent skip
    import logging as _logging
    _logging.getLogger(__name__).debug(
        "mcp_uploads: skip (module not yet present)"
    )


# ===========================================================================
# Wave-6 P1 — MCP federation (외부 FastMCP 서버 통합 proxy).
# AIDH_MCP_FEDERATION=off 면 비활성. config: AIDH_UPSTREAM_CONFIG (yaml) 또는 DB.
# ===========================================================================
try:
    if (os.environ.get("AIDH_MCP_FEDERATION") or "").lower() != "off":
        from .services import mcp_federation as _mcp_federation  # type: ignore[attr-defined]
        if hasattr(_mcp_federation, "register_all_upstreams"):
            _registered_upstreams = _mcp_federation.register_all_upstreams(mcp)
            if _registered_upstreams:
                import logging as _logging
                _logging.getLogger(__name__).info(
                    "mcp_federation: %d upstream(s) registered: %s",
                    len(_registered_upstreams),
                    ", ".join(_registered_upstreams),
                )
except Exception:  # pragma: no cover — Wave-6 P1 미구현 단계는 silent skip
    import logging as _logging
    _logging.getLogger(__name__).debug(
        "mcp_federation: skip (module not yet present)"
    )


# ---------------------------------------------------------------------------
# Public ASGI app
# ---------------------------------------------------------------------------
app = mcp.streamable_http_app()

__all__ = ["mcp", "app"]
