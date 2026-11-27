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

import asyncio
import os
from datetime import date, datetime
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from .config import settings
from .db.base import SessionLocal


# JSON Schema enum 으로 노출되어 클라이언트(Claude/Cline) 자동완성 + 오타 차단.
SearchMode = Literal["semantic", "fts", "tag", "hybrid"]
# data_svc.AGGREGATE_OPS 와 같은 집합이다 — 시험(test_mcp_contract)이 둘을 맞댄다.
AggregateOp = Literal["avg", "max", "min", "sum", "count"]
# recommend_svc.recommend_agents 가 아는 값. 함수를 바로 부르면 모르는 값은 off 로 돈다
# (종전 그대로).
OntoMode = Literal["off", "shadow", "rank"]


# 읽기 · 쓰기 표지(HWAXPortal docs/mcp-modernize 공통 계약 C6) — 판정 근거는 도구마다
# 코드에서 확인했다.
# ⚠ SDK 1.x 의 ToolAnnotations 는 카멜 이름(readOnlyHint …)만 칸으로 받는다. 스네이크
#   이름(read_only_hint=)을 주면 모르는 칸으로 붙어 클라이언트에는 표지가 하나도 안 간다
#   — 시험이 직렬화한 꼴의 readOnlyHint 를 본다.
def _hints(*, read_only: bool, destructive: bool = False, idempotent: bool = True,
           open_world: bool = False) -> ToolAnnotations:
    return ToolAnnotations(readOnlyHint=read_only, destructiveHint=destructive,
                           idempotentHint=idempotent, openWorldHint=open_world)


# 읽기 — 레코드 · 전문가 · 분류를 바꾸지 않는다. api_key 를 받는 도구의 인증이 키의 마지막
# 사용 시각을 고치는 것과 convert_file 의 서버 임시 폴더는 셈하지 않는다(데이터가 아니다).
_READ = _hints(read_only=True)
# 쓰지 않지만 서버에 설정된 바깥 LLM 을 부를 수 있다(draft_agent)
_READ_LLM = _hints(read_only=True, open_world=True)


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
        "허브 카탈로그 전체를 한 번에 요약해 돌려준다 — 총 레코드 수, data_type · 팀 · 공개 "
        "등급(classification)별 수, 등록된 전문가(agent) 전원과 각자의 레코드 수, 동기화 원천의 "
        "신선도.\n"
        "이 허브에 처음 붙었을 때 전체 모양을 볼 때 쓴다. 전문가 목록이 커서 읽기 버거우면 "
        "compact=true 로 전문가 배열 대신 도메인별 묶음을 받고 list_agents(compact=true, domain=…) "
        "로 좁힌다. 도메인 묶음만 필요하면 list_agent_domains 가 더 가볍고, 질문에 맞는 전문가를 "
        "고르는 일은 recommend_agents 다.\n"
        "돌려주는 꼴 — {total_records, by_data_type, by_division(팀별), by_classification, "
        "agents[], data_types_explained, sources[], freshness_warning?, 진입 안내 칸들}. "
        "freshness_warning 은 동기화가 오래 멈춘 원천이 있을 때만 붙는다. compact=true 면 agents "
        "가 빠지고 agents_by_domain · compact_note 가 붙는다.\n"
        "한도 — 자르지 않는다(전문가 전원). 같은 결과를 서버가 60초 동안 다시 쓴다."
    ),
    annotations=_READ,
)
async def discover(
    compact: Annotated[bool, Field(
        description="true 면 agents 배열(전문가 전원 — 응답의 대부분)을 빼고 도메인 접두별 묶음 "
                    "agents_by_domain ({domain, agent_count, record_count})을 싣는다. 기본 false — "
                    "전체 응답.")] = False,
) -> dict[str, Any]:
    # compact 는 additive 옵션 — 기본 호출은 기존 전체 응답 그대로(하위호환).
    from .services import discover_svc
    async with SessionLocal() as session:
        payload = await discover_svc.build_discover_payload(session)
    if not compact:
        return payload
    slim = {k: v for k, v in payload.items() if k != "agents"}
    slim["agents_by_domain"] = discover_svc.build_domain_rollup(payload.get("agents") or [])
    slim["compact_note"] = "agent 상세는 list_agents(compact=true, domain=...) 로 조회하라."
    return slim


@mcp.tool(
    title="List all agents",
    description=(
        "등록된 전문가(agent) 목록을 메타데이터와 함께 돌려준다 — agent_type(좌석 키) · name · "
        "description · common_tags · data_types · updated_at.\n"
        "카탈로그를 훑거나 키 · 이름 · 도메인으로 전문가를 찾을 때 쓴다. 질문 내용으로 고를 때는 "
        "recommend_agents, 도메인별 수만 볼 때는 list_agent_domains, 고른 뒤 역할 · 검색 설정을 "
        "받을 때는 get_agent_session 이다.\n"
        "돌려주는 꼴 — 전문가 객체의 배열(agent_type 오름차순). compact=true 면 각 항목이 "
        "{agent_type, name, common_tags} 뿐이다.\n"
        "한도 — 페이지가 없고 자르지 않는다. 전문가가 수백 명이라 기본(전체 메타) 응답은 수십만 "
        "자가 될 수 있다 — 한 번에 읽기 버거우면 compact=true 로 줄이고 domain · tag 로 좁힌다. "
        "도메인 이름은 list_agent_domains 로 먼저 본다."
    ),
    annotations=_READ,
)
async def list_agents(
    compact: Annotated[bool, Field(
        description="true 면 description · data_types · updated_at 을 빼고 agent_type · name · "
                    "common_tags 만 싣는다(응답이 크게 준다). 기본 false — 전체 메타.")] = False,
    domain: Annotated[str | None, Field(
        description="도메인 접두(예 'cam' · 'pwr'). agent_type 이 이 값과 같거나 '<domain>-' 로 "
                    "시작하는 전문가만 돌려준다. 비우면(null) 거르지 않는다. 도메인 목록은 "
                    "list_agent_domains.")] = None,
    tag: Annotated[str | None, Field(
        description="common_tags 에 이 태그가 글자 그대로(대소문자 구분) 든 전문가만. 비우면(null) "
                    "거르지 않는다.")] = None,
) -> list[dict[str, Any]]:
    # compact/domain/tag 는 additive 옵션 — 인자 없이 부르면 기존 전체 메타 반환(하위호환).
    from sqlalchemy import select
    from .db.models import Agent
    async with SessionLocal() as session:
        rows = (
            await session.execute(select(Agent).order_by(Agent.agent_type))
        ).scalars().all()
        out: list[dict[str, Any]] = []
        for a in rows:
            if domain and a.agent_type != domain and not a.agent_type.startswith(f"{domain}-"):
                continue
            tags = list(a.common_tags or [])
            if tag and tag not in tags:
                continue
            if compact:
                out.append({"agent_type": a.agent_type, "name": a.name, "common_tags": tags})
            else:
                out.append(
                    {
                        "agent_type": a.agent_type,
                        "name": a.name,
                        "description": a.description or "",
                        "common_tags": tags,
                        "data_types": list(a.data_types or []),
                        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
                    }
                )
        return out


@mcp.tool(
    title="Agent domain rollup",
    description=(
        "전문가(agent)를 agent_type 의 도메인 접두(첫 '-' 앞 — cam · pwr · rf …)로 묶어 도메인마다 "
        "전문가 수와 레코드 수를 돌려준다.\n"
        "카탈로그의 모양을 가장 싸게 보는 첫걸음이다. 도메인을 고른 뒤 list_agents(compact=true, "
        "domain=…) 로 그 도메인의 전문가를 보고, 질문으로 바로 고르려면 recommend_agents 를 쓴다. "
        "값은 discover(compact=true) 의 agents_by_domain 과 같다.\n"
        "돌려주는 꼴 — [{domain, agent_count, record_count}] 배열, agent_count 내림차순(같으면 "
        "domain 이름순). record_count 는 그 도메인 전문가들의 레코드 수를 더한 값이라 여러 "
        "전문가에 묶인 레코드는 거듭 센다.\n"
        "한도 — 인자 없음 · 자르지 않는다. discover 와 같은 60초 캐시를 쓴다."
    ),
    annotations=_READ,
)
async def list_agent_domains() -> list[dict[str, Any]]:
    from .services import discover_svc

    async with SessionLocal() as session:
        payload = await discover_svc.build_discover_payload(session)
    return discover_svc.build_domain_rollup(payload.get("agents") or [])


@mcp.tool(
    title="Tag vocabulary catalog",
    description=(
        "레코드에 쓰인 태그 어휘를 사용 횟수 · data_type 분포 · 그 태그를 가진 전문가와 함께 "
        "돌려준다.\n"
        "tag_search · list_records(tags=…) · agent_search(mode='tag') 전에 쓴다 — 태그 검색은 글자 "
        "그대로 일치(AND)라 어휘를 먼저 확인하지 않으면 0건이 되기 쉽다. 뜻으로 찾으려면 "
        "hybrid_search · semantic_search 를 쓴다.\n"
        "돌려주는 꼴 — {total, count, items:[{tag, count, data_types:{DOC: n, …}, agents:[…]}], "
        "filters}. items 는 count 내림차순(같으면 태그 이름순)이다.\n"
        "한도 — limit 개에서 자른다. total 은 자르기 전 맞은 태그 수이고 count 는 돌려준 수라 "
        "total > count 면 더 있다 — limit 을 올리거나 q 로 좁힌다. limit 의 상한은 없다."
    ),
    annotations=_READ,
)
async def list_tags(
    q: Annotated[str, Field(
        description="태그 앞부분(접두)으로 거른다, 대소문자 무시(예 'batt' → battery …). 빈 "
                    "글(기본)이면 거르지 않는다.")] = "",
    min_count: Annotated[int, Field(
        ge=0,
        description="이 수 이상의 레코드에 쓰인 태그만. 기본 1(0 과 같다 — 쓰인 태그는 모두 1 "
                    "이상).")] = 1,
    limit: Annotated[int, Field(
        ge=1,
        description="돌려줄 태그 수, 1 이상(상한 없음). 기본 100. 자른 사실은 total 과 count 로 "
                    "안다.")] = 100,
) -> dict[str, Any]:
    from .routes.taxonomy import _aggregate_tags

    async with SessionLocal() as session:
        bucket = await _aggregate_tags(session)
    q_norm = q.strip().lower()
    items: list[dict[str, Any]] = []
    for tag_name, entry in bucket.items():
        if entry["count"] < min_count:
            continue
        if q_norm and not tag_name.lower().startswith(q_norm):
            continue
        items.append(
            {
                "tag": tag_name,
                "count": entry["count"],
                "data_types": dict(entry["data_types"]),
                "agents": sorted(entry["agents"]),
            }
        )
    items.sort(key=lambda x: (-x["count"], x["tag"]))
    # total 은 자르기 **전** 수다 — 예전엔 자른 뒤 len(items) 라 더 있는지 알 수 없었다.
    # 건수는 호출자가 정한다(예전 최대 500 은 걷었다).
    total = len(items)
    items = items[: max(1, int(limit))]
    return {
        "total": total,
        "count": len(items),
        "items": items,
        "filters": {"q": q or None, "min_count": min_count, "limit": limit},
    }


@mcp.tool(
    title="Recommend agents",
    description=(
        "자연어 질문에 맞는 전문가(agent)를 순위로 돌려준다 — 지식카드 절 의미검색 점수를 "
        "전문가별로 모으고 전문가의 예시 질의(sample_queries) 맞춤을 더해 선다.\n"
        "사용자가 의도를 말했고 누구에게 물을지 골라야 할 때 쓴다. 키 · 이름으로 찾을 때는 "
        "list_agents, 고른 뒤에는 get_agent_session → agent_search 로 잇는다. 긴 질문은 한 "
        "덩어리로 임베딩돼 어느 긴 질문이나 비슷한 이웃이 나오므로, 주제를 짧은 도메인 구절로 나눠 "
        "구절마다 부르는 편이 낫다.\n"
        "돌려주는 꼴 — {query, agents:[{agent_type, name, description, score, sample_queries, …}], "
        "ranked_total, more_available, relevant_tools, coverage, fallback:false, "
        "query_over_model_limit, onto?}. 목록 키는 agents (recommendations · results 가 "
        "아니다)이고 좌석 키는 agent_type 이다. 맞는 전문가가 없으면 fallback:true · agents:[] 와 "
        "함께 전문가 전원 목록 catalog(예시 질의 포함)와 hint 를 준다 — 사용자에게 되물을 단서다.\n"
        "한도 — top_k 명에서 자른다. ranked_total 은 자르기 전 후보 수이고 more_available 이 true "
        "면 top_k 를 올려 더 받는다. relevant_tools 는 이 허브에 올라온 도구 가운데 질문과 가까운 "
        "것을 top_k_tools(최대 10)개까지 싣는다. 질문이 임베딩 모델 입력 한도를 넘으면 앞부분만 "
        "쓰였고 query_over_model_limit 에 그 사실이 실린다."
    ),
    annotations=_READ,
)
async def recommend_agents(
    q: Annotated[str, Field(
        description="추천받을 자연어 질문 또는 짧은 도메인 구절(예 '배터리 스웰링 가스 발생'). 빈 "
                    "글이면 추천 없이 fallback 목록이 온다.")],
    top_k: Annotated[int, Field(
        ge=1,
        description="돌려줄 전문가 수, 1 이상(상한 없음). 기본 5. 더 있는지는 ranked_total · "
                    "more_available 로 안다.")] = 5,
    top_k_tools: Annotated[int, Field(
        ge=0, le=10,
        description="relevant_tools 로 함께 돌려줄 허브 도구 수, 0 ~ 10. 0 이면 도구 검색을 하지 "
                    "않는다. 기본 3.")] = 3,
    agent_type: Annotated[str | None, Field(
        description="부르는 쪽의 전문가 문맥(좌석 키). 주면 relevant_tools 를 그 전문가에게 보이는 "
                    "도구로만 거른다(도구 매니페스트의 restrict · require · exclude 정책). 전문가 "
                    "순위(agents)는 거르지 않는다 — 범위 제한이 아니다. 비우면(null) 정책을 "
                    "적용하지 않는다.")] = None,
    onto: Annotated[OntoMode, Field(
        description="온톨로지 신호 — 'off'(기본, 응답이 그대로) · 'shadow'(전문가 줄마다 onto 칸만 "
                    "싣고 순위는 그대로) · 'rank'(질문의 객체에 지식카드가 이어진 전문가에게 "
                    "덧점). 박스에 연결 데이터가 없으면 어느 값이든 순위가 같다.")] = "off",
) -> dict[str, Any]:
    """자연어 → ranked agents (+ Wave-7 P1: relevant_tools 동봉, P2: agent context 필터).

    0건일 때는 catalog 전체에서 후보 + sample_queries 를 함께 반환해 LLM 이
    사용자에게 재질의할 단서가 되도록 한다. 인자 뜻은 위 Field 설명이 정본이다(스키마로 나간다).
    """
    from sqlalchemy import select

    from .db.models import Agent, MCPUpload
    from .services import recommend_svc, tool_embedding_svc, tool_visibility_svc

    async with SessionLocal() as session:
        rank_stats: dict[str, Any] = {}
        onto = onto if onto in ("shadow", "rank") else "off"
        agents = await recommend_svc.recommend_agents(
            session, query=q, top_k=top_k, stats=rank_stats, onto=onto)
        ranked_total = int(rank_stats.get("ranked_total") or len(agents))
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
            # additive — 각 후보에 sample_queries(질의 정렬용 힌트)를 붙이고,
            # coverage(단일 도메인 vs 교차도메인) 신호로 single 직행 vs 패널 소집을 돕는다.
            ats = [a.get("agent_type") for a in agents if a.get("agent_type")]
            if ats:
                smp_rows = (
                    await session.execute(
                        select(Agent.agent_type, Agent.sample_queries).where(
                            Agent.agent_type.in_(ats)
                        )
                    )
                ).all()
                smp_by_at = {at: list(sq or [])[:3] for at, sq in smp_rows}
                for a in agents:
                    if "sample_queries" not in a:
                        a["sample_queries"] = smp_by_at.get(a.get("agent_type"), [])
            prefixes = sorted(
                {
                    str(a.get("agent_type", "")).split("-", 1)[0]
                    for a in agents
                    if a.get("agent_type")
                }
            )
            if len(prefixes) <= 1:
                cov_hint = "단일 도메인 — 상위 1명 직행 고려."
            else:
                cov_hint = f"{len(prefixes)}개 도메인에 걸침 — 교차도메인 협진(패널) 고려."
            coverage = {
                "domains": prefixes,
                "domain_count": len(prefixes),
                "hint": cov_hint,
            }
            return {
                "query": q,
                "agents": agents,
                # top_k 로 자르기 전 후보 수 — 더 받으려면 top_k 를 올린다.
                "ranked_total": ranked_total,
                "more_available": ranked_total > len(agents),
                # 질의가 임베딩 모델 입력 한도를 넘었으면 {tokens, max_tokens, chars} — 앞부분만 썼다.
                "query_over_model_limit": rank_stats.get("query_over_model_limit"),
                "relevant_tools": relevant_tools,
                "coverage": coverage,
                "fallback": False,
                **({"onto": rank_stats.get("onto")} if onto != "off" else {}),
            }

        # 0건 폴백 — 전체 agent 의 메타 + sample_queries 상위 일부.
        rows = (
            await session.execute(select(Agent).order_by(Agent.agent_type))
        ).scalars().all()
        catalog = [
            {
                "agent_type": a.agent_type,
                "name": a.name,
                "description": a.description or "",      # 전문 — 고를 근거라 자르지 않는다
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
            "query_over_model_limit": rank_stats.get("query_over_model_limit"),
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
        "고른 전문가(agent)의 세션을 초기화할 설정을 한 번에 돌려준다 — 렌더링된 "
        "system_prompt(페르소나), retrieval_config(검색 설정), response_config(답변 규칙), "
        "sample_queries, 다루는 범위(scope).\n"
        "recommend_agents · list_agents 로 전문가를 고른 직후 가장 먼저 부른다. 받은 system_prompt "
        "를 그 뒤 답변의 페르소나로 채택하고(서버가 세션을 쥐지 않으므로 채택은 부르는 쪽 몫이다), "
        "검색은 agent_search(agent_type, q) 로 한다 — retrieval_config 는 agent_search 가 저절로 "
        "적용한다. 지식카드 본문을 미리 묶어 받으려면 get_context_bundle 이다.\n"
        "돌려주는 꼴 — {agent_type, name, description, system_prompt, retrieval_config, "
        "response_config, sample_queries, scope:{common_tags, data_types, required_doc_type, "
        "required_tags, excluded_tags}, usage_hint}. 레코드 본문은 싣지 않아 가볍다.\n"
        "오류 — 없는 agent_type 이면 'agent not found' 오류다."
    ),
    annotations=_READ,
)
async def get_agent_session(
    agent_type: Annotated[str, Field(
        description="전문가 키(좌석 키) — list_agents · recommend_agents 가 돌려주는 agent_type 값 "
                    "그대로(예 'pwr-swelling').")],
) -> dict[str, Any]:
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
        "고른 전문가(agent) 한 명의 지식카드 안에서 찾는 주 검색 도구다 — 그 전문가의 "
        "retrieval_config(top_k · score_threshold · data_type_filter · tag_boost · max_depth)와 "
        "required_tags · excluded_tags 를 저절로 적용한다.\n"
        "get_agent_session 다음에 질문마다 부른다. 전문가를 정하지 않고 허브 전체에서 찾을 때는 "
        "hybrid_search · semantic_search · fts_search · tag_search 다. 찾은 레코드의 다른 절은 "
        "get_record_sections 로 읽는다.\n"
        "mode — 'semantic'(기본, 벡터 코사인) · 'fts'(키워드) · 'hybrid'(semantic + fts 를 RRF 로 "
        "결합 — 가장 견고) · 'tag'(q 에 쉼표로 가른 정확 태그). tag 가 아니면 구어 질문에 현장 "
        "용어를 덧붙여 찾는다(응답의 query 는 원문).\n"
        "돌려주는 꼴 — {agent_type, query, mode, hits:[{record_id, section_id, title, snippet, "
        "score, tags, causal_status, …}], hit_count, refused, refusal_message, near_miss, "
        "more_available, query_over_model_limit, applied_config}. snippet 은 절 본문 전문이다. "
        "0건이거나 1위 점수가 전문가의 거절 문턱 아래면 refused:true 와 refusal_message 를 준다 — "
        "near_miss 는 문턱 아래 참고 후보일 뿐 근거로 쓰지 않는다.\n"
        "한도 — top_k 개에서 자르고 더 맞은 것이 있으면 more_available:true 다. "
        "applied_config.agent_record_scope 는 찾은 범위의 레코드 수다. 전문가에게 묶인 레코드가 "
        "0건이면 전역으로 넓히지 않고 refused 로 끝난다.\n"
        "기준일 — as_of 를 주면 valid_from(유효 시작일)이 그 날 뒤인 카드를 빼고, 히트마다 "
        "valid_from(null = 날짜 없음)을, applied_config 에 as_of · as_of_excluded_later · "
        "as_of_undated_kept(또는 as_of_undated_excluded)를 싣는다. 날짜 없는 카드를 남길지 뺄지는 "
        "as_of_undated 로 고른다."
    ),
    annotations=_READ,
)
async def agent_search(
    agent_type: Annotated[str, Field(
        description="검색할 전문가 키(좌석 키) — list_agents · recommend_agents 의 agent_type. "
                    "없으면 'agent not found' 오류다.")],
    q: Annotated[str, Field(
        description="검색 질의. mode='tag' 면 쉼표로 가른 정확 태그(예 'IGA,NURBS').")],
    mode: Annotated[SearchMode, Field(
        description="검색 방식 — 'semantic'(기본, 뜻) · 'fts'(낱말 일치) · 'hybrid'(둘을 RRF 로 "
                    "결합 — 개념과 정확한 낱말이 섞인 질의에 가장 견고) · 'tag'(정확 "
                    "태그).")] = "semantic",
    top_k: Annotated[int | None, Field(
        ge=1,
        description="돌려줄 히트 수, 1 이상(상한 없음). 비우면(null) 전문가의 "
                    "retrieval_config.top_k(없으면 10). applied_config.top_k_source 가 caller · "
                    "seat 로 어느 값을 썼는지 알린다.")] = None,
    as_of: Annotated[str, Field(
        description="기준일 — ISO 날짜 'YYYY-MM-DD' 또는 일시 'YYYY-MM-DDTHH:MM:SS[+HH:MM]'(적힌 "
                    "그대로의 날짜만 쓰고 시간대를 바꾸지 않는다). valid_from 이 그 날 뒤인 카드를 "
                    "뺀다. 빈 글(기본)이면 거르지 않는다. 형식이 틀리면(글 'null' 포함) 거르지 "
                    "않은 채 넘어가지 않고 오류다.")] = "",
    as_of_undated: Annotated[Literal["keep", "exclude"], Field(
        description="as_of 를 줄 때 valid_from 이 없는 카드 처리 — 'keep'(기본, 남기고 센다) · "
                    "'exclude'(함께 뺀다). as_of 가 비면 쓰지 않는다.")] = "keep",
) -> dict[str, Any]:
    """Agent retrieval_config 를 자동 적용하는 agent-scoped 검색.

    동작:
        1. agent 의 retrieval_config (top_k / score_threshold / data_type_filter / tag_boost) 로드
        2. agent 소속 record_ids 로 검색 범위 제한 (as_of 면 valid_from 이 그 날 뒤인 카드는 뺀다 —
           날짜 없는 카드는 as_of_undated 가 'exclude' 일 때만 뺀다)
        3. required_tags / excluded_tags 후필터
        4. score_threshold 미달 결과 제거
        5. tag_boost 가중치 적용 후 재정렬
        6. refuse_below_score 미달 시 refused=True 반환
    """
    from sqlalchemy import select
    from .db.models import Agent, Record
    from .services import search_svc
    from .services.recommend_svc import expand_query

    # 날짜가 틀리면 DB 를 열기 전에 오류로 돌려준다 — 거르지 못한 결과를 거른 것처럼 주면
    # 소급 심의가 그 날 뒤의 카드를 모른 채 근거로 쓴다. as_of 를 `str | None` 으로 두면 MCP 층
    # (FastMCP)이 글을 JSON 으로 먼저 풀어 글 "null" 이 None(안 줌)이 됐다 — 그래서 `str` 이다.
    as_of_date = _parse_as_of(as_of)

    # 구어로 물으면 **전문가가 거절한다** — 자료는 있는데 말이 안 맞아 점수가 임계 밑으로 떨어진다
    # (실측: pwr-swelling 에 '배터리가 부풀어 올랐어요' → hits 0 · refused=true, 같은 사람에게
    # '배터리 스웰링 가스 발생' 은 히트가 나온다). 검색에만 현장 용어를 덧붙인다 — 응답의 query 는
    # 사용자가 쓴 원문 그대로 둔다. tag 모드는 콤마 구분 **정확 태그**라 손대면 안 된다.
    search_q = q if mode == "tag" else expand_query(q)

    async with SessionLocal() as session, search_svc.statement_limit(session):
        agent = await session.get(Agent, agent_type)
        if agent is None:
            raise ValueError(f"agent not found: {agent_type}")

        rc = dict(agent.retrieval_config or {})
        rsp = dict(agent.response_config or {})

        # 건수 — 호출자가 주면 그것, 아니면 좌석 설정(없으면 10). 예전엔 호출자가 올릴 길이 없어
        # 카드 수천 장 좌석도 질의 한 번에 10건이었다. 1백만 토큰 창에서 건수는 호출자가 정한다.
        top_k_source = "caller" if top_k is not None else "seat"
        top_k = max(1, int(top_k)) if top_k is not None else int(rc.get("top_k") or 10)
        # 자른 뒤에도 맞은 것이 더 남았나 — 모드마다 아래에서 정한다(hit_count 는 자른 뒤의 수다).
        more_available = False
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
        # ⚠ `x = ANY(col)` 로 쓰면 GIN 인덱스(idx_records_agents)를 **못 쓴다**. 실측 —
        # `= ANY` Seq Scan 13.9ms vs `@> ARRAY[x]` Bitmap Index Scan 3.8ms. 인덱스는 처음부터
        # 있었고 쿼리 모양이 그것을 죽여 놓고 있었다(2026-09-09). 되돌리지 말 것.
        # valid_from 을 함께 받는다(as_of 거름 · 셈용) — 같은 행에서 읽으니 조회는 늘지 않는다.
        try:
            id_stmt = select(Record.id, Record.valid_from).where(
                Record.agents.op("@>")([agent_type])  # type: ignore[attr-defined]
            )
            if max_depth is not None:
                id_stmt = id_stmt.where(Record.depth <= max_depth)
            seat_rows = list((await session.execute(id_stmt)).all())
        except Exception:
            # 폴백 — 파이썬 후필터
            from .services.sql_compat import array_overlap
            pred = array_overlap(Record.agents, [agent_type], session)
            fb_stmt = select(Record.id, Record.valid_from).where(pred.where_clause)
            if max_depth is not None:
                fb_stmt = fb_stmt.where(Record.depth <= max_depth)
            seat_rows = list((await session.execute(fb_stmt)).all())
        agent_record_ids = [rid for rid, _vf in seat_rows]

        # as_of — 그 날까지 있던 카드만 찾는다. 믿을 날짜는 valid_from(유효 시작 — 동기화는
        # 원천 날짜로 채우고(sync_svc transform_record 10.), 업로드 · 변환은 올린 사람이 적은
        # 날이라 작성일보다 이를 수 있다)이고, created_at 은 허브에 적재한 날이라 쓰지 않는다.
        # 날짜 없는 카드는 기본으로 **남기고 센다** — 업로드 · 가져오기 카드는 올린 쪽이 날짜를
        # 줄 때만 차고 논문 카드 길은 주지 않아, 빼면 지식만 가진 좌석이 통째로 빈다. 어떻게
        # 볼지는 사람 결정으로 남아(HWAXPortal docs/delib-engine-feedback D-15) 부른 쪽이
        # as_of_undated 로 고른다. 그 날 뒤로 날짜가 찍힌 카드는 늘 뺀다.
        as_of_cfg: dict[str, Any] = {}
        keep_undated = as_of_undated == "keep"
        if as_of_date is not None:
            undated = "as_of_undated_kept" if keep_undated else "as_of_undated_excluded"
            as_of_cfg = {
                "as_of": as_of_date.isoformat(),
                "as_of_excluded_later": sum(
                    1 for _rid, vf in seat_rows if vf is not None and vf > as_of_date),
                undated: sum(1 for _rid, vf in seat_rows if vf is None),
            }

        # ⚠ 빈 목록을 None(=범위 제한 없음)으로 접으면 안 된다. 이 도구는 agent_type 이
        #   **필수**라 빈 목록은 언제나 '이 좌석에 바인딩된 문서가 0건'이라는 뜻이지
        #   '전역으로 찾아라'가 아니다. 접어 버리면 그 좌석이 **남의 문서를 자기 근거로**
        #   refused:false 로 돌려준다(실측 2026-09-10 — sim-thermal-sed 가 sw-app-messages 의
        #   MMS 문서 10건을 받았다). 같은 상황의 올바른 처리가 preview_svc.py:67-83 에 있다.
        if not agent_record_ids:
            return _refused_result(
                agent_type, q, mode, refusal_message, f"agent '{agent_type}' has 0 mapped records",
                as_of_cfg,
            )
        if as_of_date is not None:
            agent_record_ids = [rid for rid, vf in seat_rows
                                if (vf <= as_of_date if vf is not None else keep_undated)]
            if not agent_record_ids:
                # '0 mapped' 와 다른 말이어야 한다 — HWAXAgentServer card_review
                # (_EMPTY_SCOPE_RE)가 그 낱말을 '묶인 자료 0건 — 역할만으로 답한다' 로 읽는다.
                # 여기는 묶인 카드가 다 그 날 뒤다(엄격이면 날짜 없는 카드도 뺐다).
                dropped = f"{as_of_cfg['as_of_excluded_later']} later"
                if not keep_undated:
                    dropped += f", {as_of_cfg['as_of_undated_excluded']} undated"
                return _refused_result(
                    agent_type, q, mode, refusal_message,
                    f"agent '{agent_type}' has no records dated on or before as_of "
                    f"{as_of_cfg['as_of']} ({dropped} excluded)",
                    as_of_cfg,
                )
        scope_record_ids = agent_record_ids

        # --- 검색 실행 ---
        hits: list[dict[str, Any]] = []

        if mode == "fts":
            # 범위를 SQL 로 넘긴다. 전역으로 뽑고 나서 파이썬으로 거르면 상위 N 이 범위 밖에서
            # 정해져 좌석 결과가 거의 항상 0건이 된다(hybrid 에서 실제로 그랬다).
            raw, _ = await search_svc.fts_search(
                session, search_q, limit=top_k * 3,
                record_ids=scope_record_ids,
                data_types=data_type_filter or None)
            hits = raw[:top_k]
            more_available = len(raw) > top_k

        elif mode == "tag":
            # q = 콤마 구분 태그
            tags_list = [t.strip() for t in q.split(",") if t.strip()]
            if not tags_list:
                return _refused_result(agent_type, q, mode, refusal_message, "태그 없음", as_of_cfg)
            # 범위를 SQL 로 넘긴다 — fts·hybrid 와 같은 규율. 전역으로 뽑고 파이썬에서
            # 거르면 상위 N 이 범위 밖에서 정해져 좌석 결과가 거의 항상 0건이 된다.
            rows, tag_total = await search_svc.tag_search(
                session, tags_list, limit=top_k * 3, record_ids=scope_record_ids
            )
            more_available = int(tag_total or 0) > top_k
            hits = [
                {
                    "record_id": r.id,
                    "title": r.title,
                    "data_type": r.data_type,
                    "tags": list(r.tags or []),
                    "score": 1.0,
                }
                for r in rows
            ][:top_k]

        elif mode == "hybrid":
            # semantic + fts RRF 결합 — 패러프레이즈와 정확 키워드 모두 강함.
            # tag_boost / score_threshold 는 RRF 후 파이썬에서 적용.
            raw_hits = await search_svc.hybrid_search(
                session,
                search_q,
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
                # RRF score(통상 0.01~0.05)와 cosine threshold(~0.3)는 스케일이 달라
                # 원 semantic 점수(score_semantic) 기준으로 필터한다. semantic 점수가
                # 없는 FTS-only hit 은 통과시킨다 (키워드 정확 매칭 보존).
                raw_hits = [
                    h
                    for h in raw_hits
                    if h.get("score_semantic") is None
                    or float(h.get("score_semantic") or 0.0) >= score_threshold
                ]
            hits = raw_hits[:top_k]
            more_available = len(raw_hits) > top_k

        else:  # semantic (default)
            # tag_boost / score_threshold 는 search_svc.semantic_search 의
            # 1급 파라미터로 위임 — 가산+재정렬+필터를 한 곳에서 일관 처리.
            # 한 건 더 받아 '더 있다' 를 정확히 안다(순서가 같으므로 앞 top_k 는 종전 그대로다).
            sem = await search_svc.semantic_search(
                session,
                search_q,
                top_k=top_k + 1,
                data_types=data_type_filter or None,
                record_ids=scope_record_ids,
                tag_boost=tag_boost or None,
                min_score=score_threshold,
            )
            hits = list(sem or [])[:top_k]
            more_available = len(sem or []) > top_k

        # --- required_tags / excluded_tags 후필터 ---
        if required_tags:
            req_set = set(required_tags)
            hits = [h for h in hits if req_set.issubset(set(h.get("tags") or []))]
        if excluded_tags:
            exc_set = set(excluded_tags)
            hits = [h for h in hits if not exc_set.intersection(set(h.get("tags") or []))]

        # additive — 각 hit 에 causal_status 노출 (근거 3등급). tag 부재 시 validated.
        for h in hits:
            if "causal_status" not in h:
                h["causal_status"] = next(
                    (
                        t.split(":", 1)[1]
                        for t in (h.get("tags") or [])
                        if isinstance(t, str) and t.startswith("causal_status:")
                    ),
                    "validated",
                )

        # as_of 면 히트마다 valid_from 을 싣는다(null = 날짜 없이 남긴 카드) — 좌석 전체의 수로는
        # 인용한 근거 가운데 어느 것이 날짜 미확인인지 모른다. 안 주면 히트 꼴은 종전 그대로다.
        if as_of_date is not None:
            vf_by_id = {rid: vf for rid, vf in seat_rows}
            for h in hits:
                vf = vf_by_id.get(h.get("record_id"))
                h["valid_from"] = vf.isoformat() if vf is not None else None

        # 질의가 임베딩 모델 입력 한도(e5 512 토큰)를 넘으면 앞부분만 의미검색에 쓰였다 — 모델
        # 한도라 늘릴 수 없으니 응답에 싣는다(fts·tag 는 임베딩을 쓰지 않는다).
        query_over_model_limit = None
        if mode in ("semantic", "hybrid"):
            from .services import embed_slots
            from .services.embedding import input_overflow

            # 활성 슬롯의 모델로 센다(의미검색이 그 모델로 인코딩했다). 셈은 토크나이저 잠금을 쓰므로 스레드로.
            query_over_model_limit = await asyncio.to_thread(
                input_overflow, embed_slots.embedder(embed_slots.active()), search_q, is_query=True)

        # --- refuse_below_score 판단 ---
        refused = False
        if not hits:
            refused = True
        elif refuse_below is not None:
            top_score = hits[0].get("score", 1.0) if hits else 0.0
            if top_score < refuse_below:
                refused = True

        # additive — 거부가 막다른 길이 되지 않게, 문턱 미달 후보를 참고용으로만 제공한다
        # (판정 근거 아님). 거부 시에만 채워지고, 실패해도 응답을 막지 않는다.
        near_miss: list[dict[str, Any]] = []
        if refused and not hits and mode in ("semantic", "hybrid"):
            try:
                nm = await search_svc.semantic_search(
                    session,
                    search_q,
                    top_k=3,
                    data_types=data_type_filter or None,
                    record_ids=scope_record_ids,
                    tag_boost=None,
                    min_score=None,
                )
                near_miss = [
                    {
                        "record_id": h.get("record_id"),
                        "title": h.get("title"),
                        "score": h.get("score"),
                        "note": "score_threshold 미달 — 참고용 후보(판정 근거로 쓰지 마라)",
                    }
                    for h in (nm or [])[:3]
                ]
            except Exception:  # pragma: no cover — defensive
                near_miss = []

        return {
            "agent_type": agent_type,
            "query": q,
            "mode": mode,
            "hits": hits,
            "hit_count": len(hits),
            "refused": refused,
            "refusal_message": refusal_message if refused else None,
            "near_miss": near_miss,
            "more_available": more_available,
            "query_over_model_limit": query_over_model_limit,
            "applied_config": {
                "top_k": top_k,
                "top_k_source": top_k_source,
                "score_threshold": score_threshold,
                "threshold_basis": "semantic" if mode in ("semantic", "hybrid") else None,
                "data_type_filter": data_type_filter,
                "tag_boost_applied": bool(tag_boost),
                "max_depth": max_depth,
                "required_tags": required_tags,
                "excluded_tags": excluded_tags,
                "agent_record_scope": len(agent_record_ids) if agent_record_ids else "all",
                **as_of_cfg,
            },
        }


async def _agent_record_ids(session, agent_type: str) -> list[str]:
    """그 좌석에 바인딩된 레코드 id. 없으면 **빈 리스트**(전역이라는 뜻이 아니다).

    ⚠ `x = ANY(col)` 로 쓰면 GIN 인덱스(idx_records_agents)를 **못 쓴다**. 실측 —
    `= ANY` Seq Scan 13.9ms vs `@> ARRAY[x]` Bitmap Index Scan 3.8ms. 되돌리지 말 것.
    비-PG(SQLite 테스트)는 sql_compat 폴백을 탄다.
    """
    from types import SimpleNamespace

    from sqlalchemy import select

    from .db.models import Record

    try:
        stmt = select(Record.id).where(
            Record.agents.op("@>")([agent_type])  # type: ignore[attr-defined]
        )
        return list((await session.execute(stmt)).scalars().all())
    except Exception:  # noqa: BLE001 — 배열 연산자 미지원 백엔드(SQLite 테스트)
        from .services.sql_compat import array_overlap

        # ⚠ 폴백에서 `select(Record.id).where(pred.where_clause)` 만 하면 안 된다.
        #   비-PG 의 array_overlap 은 where_clause 가 `true()` 라 **전 코퍼스**가 그 좌석의
        #   범위가 된다. 좁히는 일은 python_filter 가 하므로 행을 받아 걸러야 하고,
        #   그러려면 id 만이 아니라 agents 컬럼도 함께 가져와야 한다.
        pred = array_overlap(Record.agents, [agent_type], session)
        rows = (await session.execute(
            select(Record.id, Record.agents).where(pred.where_clause))).all()
        if pred.python_filter is None:
            return [r[0] for r in rows]
        return [r[0] for r in rows if pred.python_filter(SimpleNamespace(agents=r[1]))]


def _refused_result(
    agent_type: str, q: str, mode: str, refusal_message: str, reason: str,
    as_of_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "agent_type": agent_type,
        "query": q,
        "mode": mode,
        "hits": [],
        "hit_count": 0,
        "refused": True,
        "refusal_message": refusal_message,
        "near_miss": [],
        # as_of 를 받았으면 거절에도 싣는다 — 부른 쪽이 '거름이 걸렸다' 를 응답마다 확인한다.
        "applied_config": {"reason": reason, **(as_of_cfg or {})},
    }


def _parse_as_of(raw: str | None) -> date | None:
    """agent_search 의 as_of → 날짜. 없거나 빈 글이면 None(거르지 않는다 — 종전 그대로).

    ISO 날짜('2025-06-01')와 일시('2025-06-01T09:30:00+09:00' · 끝 Z)를 받는다. 일시는
    **적힌 그대로의 날짜**로 센다(시간대를 바꾸지 않는다) — valid_from 이 원천 시각 글의
    앞 10자라(sync_svc transform_record 10.) 같은 셈이다. 그 밖의 글은 오류다.
    """
    s = "" if raw is None else str(raw).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).date()
    except ValueError as exc:
        raise ValueError(
            "as_of must be an ISO date 'YYYY-MM-DD' or datetime 'YYYY-MM-DDTHH:MM:SS[+HH:MM]', "
            f"got {raw!r} ({exc})"
        ) from None


_DATA_TYPES_DESC = "'DOC' · 'DATA' · 'SIM' · 'CAD' · 'LOG' · 'FORM' · 'OTHER'"
_SCOPE_AGENT_DESC = ("이 전문가 키(agent_type)에 묶인 레코드 안에서만 찾는다. 묶인 레코드가 "
                     "0건이면 빈 결과다(전역으로 넓히지 않는다). 비우면(null) 허브 전체.")


@mcp.tool(
    title="Semantic search (full DB or agent-scoped)",
    description=(
        "허브 전체의 지식카드 절(RAG 청크)을 뜻(벡터 코사인 유사도)으로 찾는다. data_type 으로 "
        "좁히거나 agent_type 으로 한 전문가의 레코드로 한정할 수 있다.\n"
        "말을 바꿔 물어도 뜻이 같으면 잡아야 할 때 쓴다. 전문가를 이미 골랐으면 그 설정까지 "
        "적용하는 agent_search 가 낫고, 약어 · 부품 번호 · 모델 코드처럼 정확한 낱말이 섞이면 "
        "hybrid_search, 낱말 일치만이면 fts_search 다.\n"
        "돌려주는 꼴 — 히트 배열 [{section_id, record_id, title, section_title, snippet, score, "
        "data_type, tags}], score 내림차순. score 는 0 ~ 1 코사인 유사도(1 이 가장 가깝다)이며 "
        "무관한 글도 높게 나오므로 절대값이 아니라 순위로 읽는다. snippet 은 절 본문 전문이다.\n"
        "한도 — top_k 개에서 자르고 더 있는지는 알려 주지 않는다(필요하면 top_k 를 올리거나 "
        "more_available 을 주는 agent_search 를 쓴다). 서버 운영 상한이 걸려 있으면 그 수에서 더 "
        "잘리고 응답에 표시가 없다. 질의가 빈 글이면 빈 배열이다."
    ),
    annotations=_READ,
)
async def semantic_search(
    q: Annotated[str, Field(description="자연어 질의. 빈 글이면 빈 배열이다.")],
    top_k: Annotated[int, Field(ge=1, description="돌려줄 히트 수, 1 이상. 기본 10.")] = 10,
    data_types: Annotated[list[str] | None, Field(
        description=f"data_type 거름 — {_DATA_TYPES_DESC} 가운데 하나 이상(글자 그대로 일치). "
                    "비우면(null) 거르지 않는다.")] = None,
    agent_type: Annotated[str | None, Field(description=_SCOPE_AGENT_DESC)] = None,
) -> list[dict[str, Any]]:
    from sqlalchemy import select
    from .db.models import Record
    from .services import search_svc

    async with SessionLocal() as session, search_svc.statement_limit(session):
        record_ids: list[str] | None = None
        if agent_type:
            try:
                id_stmt = select(Record.id).where(
                    Record.agents.op("@>")([agent_type])  # type: ignore[attr-defined]
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
            # ⚠ `or None` 금지 — record_ids 는 agent_type 이 있을 때만 채워지므로 []는
            #   '그 좌석이 0건'이다. 접으면 전역 검색이 되어 남의 문서가 돌아온다.
            record_ids=record_ids,
        )


@mcp.tool(
    title="Hybrid search (semantic + FTS via RRF)",
    description=(
        "허브 전체의 지식카드 절을 뜻(벡터) 검색과 낱말(FTS) 검색으로 함께 찾아 Reciprocal Rank "
        "Fusion(RRF, k=60)으로 합친다. data_type 으로 좁히거나 agent_type 으로 한 전문가의 "
        "레코드로 한정할 수 있다.\n"
        "개념과 정확한 낱말(약어 · 모델 코드 · 부품 번호 · 바꿔 말한 의도)이 섞인 질의에 가장 "
        "견고하다. 전문가를 골랐으면 agent_search(mode='hybrid') 가 그 전문가 설정까지 적용한다. "
        "뜻만이면 semantic_search, 낱말 일치만이면 fts_search 다.\n"
        "돌려주는 꼴 — 히트 배열 [{section_id, record_id, title, section_title, snippet, score, "
        "score_semantic?, score_fts_rank?, data_type, tags}], score 내림차순. score 는 RRF "
        "점수(보통 0.01 ~ 0.05 — 코사인이 아니다), score_semantic 은 원래 뜻 점수(있을 때만), "
        "score_fts_rank 는 낱말 결과 안의 순위(1부터, 있을 때만)다. 제목 · 요약에서만 맞은 "
        "레코드는 section_id 가 null 이다.\n"
        "한도 — top_k 개에서 자르고 더 있는지는 알려 주지 않는다(필요하면 top_k 를 올린다). 질의가 "
        "빈 글이면 빈 배열이다."
    ),
    annotations=_READ,
)
async def hybrid_search(
    q: Annotated[str, Field(description="검색 질의 — 자연어와 정확한 낱말(코드 · 약어)을 섞어도 "
                                        "된다. 빈 글이면 빈 배열이다.")],
    top_k: Annotated[int, Field(ge=1, description="돌려줄 히트 수, 1 이상. 기본 10.")] = 10,
    data_types: Annotated[list[str] | None, Field(
        description=f"data_type 거름 — {_DATA_TYPES_DESC} 가운데 하나 이상(글자 그대로 일치). "
                    "비우면(null) 거르지 않는다.")] = None,
    agent_type: Annotated[str | None, Field(description=_SCOPE_AGENT_DESC)] = None,
) -> list[dict[str, Any]]:
    from sqlalchemy import select
    from .db.models import Record
    from .services import search_svc

    async with SessionLocal() as session, search_svc.statement_limit(session):
        record_ids: list[str] | None = None
        if agent_type:
            try:
                id_stmt = select(Record.id).where(
                    Record.agents.op("@>")([agent_type])  # type: ignore[attr-defined]
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
            # ⚠ `or None` 금지 — record_ids 는 agent_type 이 있을 때만 채워지므로 []는
            #   '그 좌석이 0건'이다. 접으면 전역 검색이 되어 남의 문서가 돌아온다.
            record_ids=record_ids,
        )


@mcp.tool(
    title="Full-text keyword search",
    description=(
        "PostgreSQL 전문 검색(tsvector · tsquery)으로 지식카드의 절 본문과 레코드 제목 · 요약을 "
        "낱말 일치로 찾는다. agent_type 으로 한 전문가의 레코드로 한정할 수 있다.\n"
        "정확한 낱말 · 코드 · 부품 번호 · 약어를 찾을 때 semantic_search 보다 낫다. 낱말이 모두 "
        "맞는 것이 없고 질의가 여러 낱말이면 낱말 가운데 하나라도 맞는 것으로 한 번 더 "
        "찾는다(사전이 한국어 형태소를 모른다). 뜻이 섞인 질의는 hybrid_search, 전문가를 골랐으면 "
        "agent_search(mode='fts') 를 쓴다.\n"
        "돌려주는 꼴 — 히트 배열 [{record_id, section_id, section_title, title, snippet, "
        "data_type, tags, …}], 관련도 순. 절에서 맞으면 snippet 이 절 본문 전문이고, 제목 · "
        "요약에서만 맞은 레코드는 section_id 가 null 이고 snippet 이 요약이다.\n"
        "한도 — top_k 개에서 자르고 더 있는지는 알려 주지 않는다(필요하면 top_k 를 올린다). 질의가 "
        "빈 글이면 빈 배열이다."
    ),
    annotations=_READ,
)
async def fts_search(
    q: Annotated[str, Field(description="찾을 낱말(공백으로 가른다). 코드 · 부품 번호 · 약어를 "
                                        "그대로 넣는다.")],
    top_k: Annotated[int, Field(ge=1, description="돌려줄 히트 수, 1 이상. 기본 10.")] = 10,
    agent_type: Annotated[str | None, Field(description=_SCOPE_AGENT_DESC)] = None,
) -> list[dict[str, Any]]:
    from sqlalchemy import select
    from .db.models import Record
    from .services import search_svc

    async with SessionLocal() as session, search_svc.statement_limit(session):
        # ⚠ 좌석 범위는 **SQL 로** 넘긴다. 전역 상위 N 을 뽑고 파이썬에서 거르면 그 좌석의
        #   문서가 전역 상위에 없을 때 결과가 통째로 0건이 된다 — agent_search 가 정확히
        #   그 이유로 0건이었다(bf57f06). agent_type 이 있는데 바인딩이 0건이면 []가 넘어가
        #   search_svc 의 빈-범위 가드가 결과 없음으로 끝낸다(전역으로 넓히지 않는다).
        scope_ids = await _agent_record_ids(session, agent_type) if agent_type else None
        items, _ = await search_svc.fts_search(session, q, limit=top_k * 3, record_ids=scope_ids)
        return items[:top_k]


@mcp.tool(
    title="Tag-based exact search",
    description=(
        "주어진 태그를 모두 가진 레코드를 찾는다(AND, 글자 그대로 일치 — 대소문자 구분). "
        "agent_type 으로 한 전문가의 레코드로 한정할 수 있다.\n"
        "태그 어휘를 이미 알 때 쓴다 — 모르면 list_tags 로 정확한 태그를 먼저 확인한다(한 글자만 "
        "달라도 0건이다). 팀 · 연도 · doc_type 같은 조건과 함께 거르거나 총수 · 쪽 넘김이 필요하면 "
        "list_records(tags=…), 전문가 안에서는 agent_search(mode='tag') 다.\n"
        "돌려주는 꼴 — [{record_id, title, data_type, tags, summary}] 배열. summary 는 자르지 "
        "않는다. 절 본문은 싣지 않으니 get_record_sections 로 읽는다.\n"
        "한도 — top_k 건에서 자르고 더 있는지는 알려 주지 않는다(총수가 필요하면 "
        "list_records(tags=…) 의 total). 태그가 비면 빈 배열이다."
    ),
    annotations=_READ,
)
async def tag_search(
    tags: Annotated[str, Field(
        description="찾을 태그를 쉼표로 가른 글(예 'IGA,NURBS'). 앞뒤 공백은 지운다. 모든 태그를 "
                    "가진 레코드만 맞는다.")],
    agent_type: Annotated[str | None, Field(description=_SCOPE_AGENT_DESC)] = None,
    top_k: Annotated[int, Field(ge=1, description="돌려줄 레코드 수, 1 이상. 기본 20.")] = 20,
) -> list[dict[str, Any]]:
    from sqlalchemy import select
    from .db.models import Record
    from .services import search_svc

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    if not tag_list:
        return []

    async with SessionLocal() as session:
        scope_ids = await _agent_record_ids(session, agent_type) if agent_type else None
        rows, _ = await search_svc.tag_search(
            session, tag_list, limit=top_k * 3, record_ids=scope_ids
        )
        return [
            {
                "record_id": r.id,
                "title": r.title,
                "data_type": r.data_type,
                "tags": list(r.tags or []),
                "summary": r.summary or "",      # 요약 전문 — 자르지 않는다(list_records 와 같다)
            }
            for r in rows
        ][:top_k]


# ===========================================================================
# 레코드 상세 tools
# ===========================================================================

@mcp.tool(
    title="정형 필터로 레코드 목록 조회",
    description=(
        "정형 조건과 낱말 검색으로 레코드 목록을 거른다 — team · group · doc_type · data_type · "
        "year 는 글자 그대로 일치, tags 는 모두 포함(AND), agents 는 하나라도 겹침(OR), q 는 "
        "공백으로 나눈 낱말을 모두 담은 카드(낱말마다 제목·요약에서"
        + (", agents 를 주면 본문에서도" if settings.aidh_records_q_body_search else "")
        + " 부분 일치).\n"
          "뜻으로 찾는 검색이 아니라 '조건에 맞는 것 나열'이 필요할 때 쓴다 — 예 'HE 팀 manual "
          "문서 목록' · '2026년 VOC 데이터' · 한 전문가의 카드 전부를 쪽으로 넘기기. 뜻으로 "
          "찾으려면 hybrid_search · semantic_search, 태그 어휘는 list_tags, doc_type code 는 "
          "list_doc_types, 한 레코드의 본문은 get_record · get_record_sections 다.\n"
          "돌려주는 꼴 — {total, count, offset, items:[{id, title, data_type, team, group, year, "
          "doc_type, tags, agents, summary}]}. 본문(content)은 빼서 가볍고 요약은 자르지 않는다.\n"
          "한도 — 한 번에 limit(1 ~ 100)건. total 은 조건에 맞는 전체 수라 offset + count < total "
          "이면 offset 을 올려 다음 쪽을 받는다."
    ),
    annotations=_READ,
)
async def list_records(
    team: Annotated[str, Field(description="팀 코드, 글자 그대로 일치(예 'HE'). 빈 글(기본)이면 "
                                           "거르지 않는다.")] = "",
    group: Annotated[str, Field(description="그룹 코드, 글자 그대로 일치(예 'CAE'). 빈 "
                                            "글(기본)이면 거르지 않는다.")] = "",
    doc_type: Annotated[str, Field(
        description="doc_type(의미 분류) code, 글자 그대로 일치 — 목록은 list_doc_types. 빈 "
                    "글(기본)이면 거르지 않는다.")] = "",
    data_type: Annotated[str, Field(
        description=f"data_type, 글자 그대로 일치 — {_DATA_TYPES_DESC}(대문자). 빈 글(기본)이면 "
                    "거르지 않는다.")] = "",
    tags: Annotated[list[str] | None, Field(
        description="이 태그를 모두 가진 레코드만(AND, 글자 그대로 일치). 비우면(null) 거르지 "
                    "않는다.")] = None,
    agents: Annotated[list[str] | None, Field(
        description="전문가 키(agent_type) 목록 — 그 가운데 하나라도 묶인 레코드(OR). 비우면(null) "
                    "거르지 않는다.")] = None,
    year: Annotated[int, Field(ge=0, description="연도, 글자 그대로 일치(예 2026). 0(기본)이면 "
                                                 "거르지 않는다.")] = 0,
    q: Annotated[str, Field(
        description="낱말 검색 — 공백으로 나눈 낱말을 모두 담은 레코드(부분 일치, 어디를 보는지는 "
                    "위 설명). 빈 글(기본)이면 쓰지 않는다.")] = "",
    limit: Annotated[int, Field(ge=1, le=100, description="한 쪽의 건수, 1 ~ 100. 기본 20.")] = 20,
    offset: Annotated[int, Field(ge=0, description="건너뛸 건수(쪽 넘김), 0 이상. 기본 0.")] = 0,
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
        "DATA 레코드(표) 하나의 통계를 서버가 계산해 돌려준다 — 평균 · 최대 · 최소 · 합 · 개수, "
        "그룹별 집계와 사전 거름도 된다.\n"
        "전체 행을 받아 직접 셀 필요가 없어 표가 클수록 get_record 로 행을 다 받는 것보다 싸다. "
        "어떤 열이 있는지 모르면 get_record 의 content.headers · units 를 본다(열 이름이 틀리면 "
        "오류 응답에 headers · units · row_count 가 함께 온다). 표의 꼴은 "
        "describe_data_capability('DATA') 에 있다.\n"
        "예 — 레코드의 stress 평균: op='avg', column='stress'. region 별 yield 최대: op='max', "
        "column='yield', group_by='region'.\n"
        "돌려주는 꼴 — 그룹 없음: {record_id, op, column, result, unit, rows_considered}. 그룹: "
        "{record_id, op, column, group_by, unit, result:[{<group_by>: 값, '<op>_<column>': 값}], "
        "groups, rows_considered} — 값 내림차순.\n"
        "오류(응답 안에) — 없는 record_id 면 code 'not_found', DATA 가 아니면 'not_data', 열 · "
        "조건이 틀리면 'bad_request'(recoverable:true)."
    ),
    annotations=_READ,
)
async def data_aggregate(
    record_id: Annotated[str, Field(
        description="집계할 DATA 레코드의 id(검색 결과의 record_id · list_records 의 id).")],
    op: Annotated[AggregateOp, Field(
        description="집계 — 'avg'(평균) · 'max' · 'min' · 'sum' · 'count'(행 수).")],
    column: Annotated[str, Field(
        description="대상 열 이름(content.headers 의 값 그대로). op='count' 가 아니면 필수. 빈 "
                    "글(기본)이면 쓰지 않는다.")] = "",
    group_by: Annotated[str, Field(
        description="그룹 열 이름(content.headers 의 값). 주면 그 열의 값마다 따로 집계한다. 빈 "
                    "글(기본)이면 그룹 없이.")] = "",
    where: Annotated[str, Field(
        description="사전 거름 '열:값' — 그 열의 값이 글자 그대로 같은 행만 센다(예 'region:KR'). "
                    "빈 글(기본)이면 거르지 않는다.")] = "",
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
            # 레코드가 이미 로드돼 있으므로 복구에 필요한 컬럼 정보를 직접 동봉한다
            # (구 안내의 data_columns 는 MCP 에 존재하지 않는 REST 전용 도구였음).
            return {
                "error": str(exc), "code": "bad_request", "recoverable": True,
                "headers": list(c.get("headers") or []),
                "units": c.get("units") if isinstance(c.get("units"), list) else None,
                "row_count": len(list(c.get("rows") or [])),
                "suggestion": "위 headers/units 기준으로 op/column/group_by 를 맞춰 재호출하세요.",
            }
        return {"record_id": rec.id, **result}


@mcp.tool(
    title="Get full record by ID",
    description=(
        "레코드 하나를 본문(content)과 메타데이터 전체로 돌려준다.\n"
        "검색 결과가 가리키는 레코드의 데이터를 통째로 봐야 할 때 쓴다 — 표의 모든 행, 문서의 구조 "
        "전체. 긴 문서에서 몇 절만 필요하면 get_record_sections(sections=[…]) 가 싸고, 표의 "
        "통계만이면 data_aggregate, 여러 레코드의 메타데이터만이면 list_records 다.\n"
        "돌려주는 꼴 — {id, data_type, team, group, year, seq, title, summary, tags, agents, "
        "doc_type, content}. content 의 꼴은 data_type 마다 다르다(DATA 는 headers · rows · units, "
        "DOC 는 sections · blocks — describe_data_capability).\n"
        "한도 — 자르지 않는다(큰 표 · 긴 문서는 응답이 크다). 없는 id 면 'record not found' "
        "오류다. 읽기만 한다."
    ),
    annotations=_READ,
)
async def get_record(
    record_id: Annotated[str, Field(
        description="레코드 id — '{DATA_TYPE}-{TEAM}-{GROUP}-{YEAR}-{SEQ 10자리}' 꼴. 검색 결과의 "
                    "record_id · list_records 의 id 를 그대로 넣는다.")],
) -> dict[str, Any]:
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
        "AI 데이터 허브 레코드(record_id)의 본문을 절(RAG 청크) 단위로 돌려준다.\n"
        "record_id 를 쥐고 본문을 읽을 때 쓰는 도구다 — 문서 편집 앱의 get_section 은 문서 slug 를 "
        "받는 다른 도구다. 검색 히트의 section_id 를 sections 로 주면 그 절만, 빼면 레코드의 모든 "
        "절을 차례대로 준다. 메타데이터와 본문을 한 덩어리로 보려면 get_record, 한 전문가의 카드를 "
        "미리 묶어 받으려면 get_context_bundle 이다.\n"
        "돌려주는 꼴 — [{section_id, level, title, content_text}] 배열. sections 를 주면 준 "
        "차례대로(없는 id 는 빠진다), 안 주면 저장 순서다. content_text 는 자르지 않는다.\n"
        "한도 — sections 를 안 주면 기본이 전부다. 긴 레코드를 나눠 읽으려면 limit · offset 으로 "
        "쪽을 넘긴다 — 받은 수가 limit 보다 적으면 끝이다. 총 절 수는 따로 주지 않는다. 없는 "
        "record_id 면 빈 배열이다."
    ),
    annotations=_READ,
)
async def get_record_sections(
    record_id: Annotated[str, Field(description="레코드 id — 검색 히트의 record_id 를 그대로 "
                                                "넣는다.")],
    sections: Annotated[list[str] | None, Field(
        description="가져올 section_id 목록(검색 히트의 section_id). 주면 이 절만 준 차례대로 주고 "
                    "limit · offset 은 쓰지 않는다. 비우면(null) 전부(또는 limit · offset "
                    "쪽).")] = None,
    limit: Annotated[int | None, Field(
        ge=1,
        description="sections 를 안 줄 때 한 번에 받을 절 수, 1 이상. 비우면(null, 기본) 끝까지 "
                    "전부.")] = None,
    offset: Annotated[int, Field(
        ge=0, description="sections 를 안 줄 때 앞에서 건너뛸 절 수, 0 이상. 기본 0.")] = 0,
) -> list[dict[str, Any]]:
    """선택적 부분 조회.

    - ``sections`` 지정 → 해당 section_id 만 반환 (순서는 입력 순서 유지).
    - ``sections`` 미지정 → 전부(id 오름차순). ``limit``·``offset`` 을 주면 그만큼 나눠 준다.

    ⚠ 예전 기본은 앞 10절·최대 50절이었고 offset 도 total 도 없어서, 50절 넘는 문서의 뒤는
    sections 를 알지 못하는 한 읽을 길이 없었다(말도 없이 끊겼다). 운영 창은 1,048,576 토큰이라
    특허 한 건(평균 101,242자)도 들어간다 — 나눠 읽을지는 호출자가 정한다.
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

        # 부분 미지정 — 전부, 또는 호출자가 준 쪽(offset·limit)
        stmt = (
            select(RecordSection)
            .where(RecordSection.record_id == record_id)
            .order_by(RecordSection.id.asc())
        )
        if offset and int(offset) > 0:
            stmt = stmt.offset(int(offset))
        if limit is not None:
            stmt = stmt.limit(max(1, int(limit)))
        secs = (await session.execute(stmt)).scalars().all()
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
        "한 전문가(agent)의 메타데이터와 묶인 레코드 · 핵심 절을 LLM 이 바로 읽을 꼴로 묶어 "
        "돌려준다 — JSON(기본) 또는 Markdown.\n"
        "get_agent_session 다음에 그 전문가의 지식을 미리 문맥에 실을 때 쓴다. 질문마다 맞는 "
        "카드를 찾는 것은 agent_search, 특정 레코드의 절은 get_record_sections 다. 같은 전문가로 "
        "거듭 부를 까닭이 없다 — 한 번 받은 묶음을 다시 쓴다.\n"
        "전문가에게 score_threshold 와 sample_queries 가 함께 있으면 sample_queries 를 기준으로 "
        "관련도가 문턱 아래인 절을 뺀다(적용 여부 · 문턱은 retrieval_filter 에).\n"
        "돌려주는 꼴 — JSON 이면 {agent, records:[{…, sections:[…], sections_total}], "
        "retrieval_filter, totals:{records_bound, …}}, markdown 이면 같은 내용을 담은 글 하나. 절 "
        "본문은 자르지 않는다.\n"
        "한도 — 레코드 max_records 건, 레코드마다 절 max_sections_per_record 개까지. 넘친 것은 "
        "totals.records_bound(전문가의 레코드 총수)와 레코드마다 sections_total 로 알린다. 없는 "
        "agent_type 이면 'agent not found' 오류다."
    ),
    annotations=_READ,
)
async def get_context_bundle(
    agent_type: Annotated[str, Field(description="전문가 키(agent_type) — list_agents · "
                                                 "recommend_agents 의 값 그대로.")],
    format: Annotated[str, Field(
        description="'json'(기본) 또는 'markdown'(대소문자 무시). 그 밖의 값은 json 으로 "
                    "돈다.")] = "json",
    max_records: Annotated[int, Field(ge=1, description="묶을 레코드 수, 1 이상. 기본 10.")] = 10,
    max_sections_per_record: Annotated[int, Field(
        ge=1, description="레코드마다 묶을 절 수, 1 이상. 기본 8.")] = 8,
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
        "data_type 하나의 content 꼴 규칙(필수 · 선택 필드, shape)과 그 꼴에 적용할 수 있는 분석 "
        "도구(capability_tools)를 돌려준다.\n"
        "데이터를 저장하거나 분석하기 전에 '이 타입은 이렇게 생겼고 이 도구로 분석한다' 를 확인할 "
        "때 쓴다. 저장할 때 채울 메타데이터 보강 안내는 describe_record_schema, 비슷한 기존 데이터 "
        "찾기는 find_similar_data, 표의 통계는 data_aggregate 다.\n"
        "돌려주는 꼴 — {data_type, description, required, optional, shape, "
        "capability_tools:[{name, when_to_use, produces, accepts, graph_type}]}. DATA 면 "
        "graph_types(권장 그래프 어휘)와 analysis_hint 가 붙는다. capability_tools 는 이 허브에 "
        "올라온 도구 가운데 이 data_type 을 받거나 내는 것이고 호출마다 새로 찾는다. DATA 에 "
        "graph_type 을 주면 입력 graph_type 이 같은 도구를 더한다(좁히지 않는다).\n"
        "오류(응답 안에) — 모르는 data_type 이면 {error, known:[…]}."
    ),
    annotations=_READ,
)
async def describe_data_capability(
    data_type: Annotated[str, Field(
        description=f"{_DATA_TYPES_DESC} 가운데 하나(대소문자 무시).")],
    graph_type: Annotated[str, Field(
        description="DATA 의 그래프 꼴(예 'stress_strain' · 'time_series' — 어휘는 DATA 응답의 "
                    "graph_types). data_type 이 DATA 일 때만 쓰고, 입력 graph_type 이 같은 도구를 "
                    "capability_tools 에 더한다. 빈 글(기본)이면 쓰지 않는다.")] = "",
) -> dict[str, Any]:
    from .services import discover_svc

    async with SessionLocal() as session:
        return await discover_svc.build_data_capability(
            session, data_type, graph_type=graph_type or None
        )


@mcp.tool(
    title="레코드 메타데이터·보강 가이드",
    description=(
        "레코드를 저장(import_record)하기 전에 채울 수 있는 메타데이터와 채우는 법을 안내한다 — "
        "읽기만 하고 아무것도 저장하지 않는다.\n"
        "필수 필드(title · team · group)만 채우지 말고 doc_type · tags · summary 등을 보강하면 "
        "검색 품질이 크게 오른다. find_similar_data 의 confidence 가 low · none 이라 분류를 짐작할 "
        "수 없을 때, 고를 수 있는 값을 사용자에게 보여 주며 물을 때도 쓴다. content 꼴 규칙은 "
        "describe_data_capability, 전문가(agent) 정의 필드는 describe_agent_schema 다.\n"
        "돌려주는 꼴 — 보강 필드(doc_type · tags · summary · agents · classification · "
        "subject_keywords · agent_hints 등) 설명, 고를 수 있는 값(data_type · classification · "
        "status), 등록된 doc_type 목록, 전문가별 권장 태그, 예시 레코드. agent_type 을 주면 그 "
        "전문가(챗봇)에 맞는 보강 규칙으로 좁혀 안내한다.\n"
        "한도 — 자르지 않는다."
    ),
    annotations=_READ,
)
async def describe_record_schema(
    agent_type: Annotated[str, Field(
        description="이 전문가 키(agent_type)에 맞는 보강 규칙으로 좁힌다. 빈 글(기본)이면 전체 "
                    "안내.")] = "",
) -> dict[str, Any]:
    from .services import ingest_guide_svc

    async with SessionLocal() as session:
        return await ingest_guide_svc.build_guide(
            session, agent_type=agent_type or None
        )


@mcp.tool(
    title="비슷한 기존 데이터 찾기 (제안형 자동분류)",
    description=(
        "새 표 · 문서가 기존 어떤 데이터와 같은 종류인지 임베딩 유사도로 찾아 분류를 제안한다 — "
        "doc_type · graph_type · tags 는 제안값으로, team · group 은 사람이 고를 후보로. 저장하지 "
        "않는다.\n"
        "import_record 전에 불러 레코드에 채울 분류를 얻는 용도다. 제안은 확정이 아니다 — "
        "confidence 가 low · medium 이면 반드시 사용자에게 확인하고, team · group 은 needs_human "
        "의 후보를 보여 주고 사람이 고르게 한다(비슷해도 다른 팀 데이터일 수 있고, 틀리면 영영 못 "
        "찾는다). 비슷한 것이 거의 없으면(low · none) describe_record_schema 로 고를 수 있는 값을 "
        "보여 주며 묻는다.\n"
        "돌려주는 꼴 — {neighbors:[{id, title, team, group, score, …}], suggested:{doc_type?, "
        "graph_type?, tags?: {value, source, score}}, needs_human:{team?, group?: {candidates, "
        "most_similar, ask}}, confidence:'high'|'medium'|'low'|'none', note}.\n"
        "한도 — 같은 data_type 의 레코드 가운데 가까운 top_k 건으로 다수결한다. 비교할 레코드가 "
        "많아 최근 일부만 견줬으면 note 에 그 수를 적는다."
    ),
    annotations=_READ,
)
async def find_similar_data(
    headers: Annotated[list[str] | None, Field(
        description="표의 열 이름 목록 — DATA 에서 가장 강한 단서다. 비우면(null) 쓰지 "
                    "않는다.")] = None,
    caption: Annotated[str, Field(description="표 · 그림 캡션 글. 빈 글(기본)이면 쓰지 "
                                              "않는다.")] = "",
    title: Annotated[str, Field(description="새 데이터의 제목. 빈 글(기본)이면 쓰지 않는다.")] = "",
    data_type: Annotated[str, Field(
        description=f"견줄 data_type — 같은 data_type 의 레코드와만 견준다({_DATA_TYPES_DESC}). "
                    "기본 'DATA'.")] = "DATA",
    top_k: Annotated[int, Field(ge=1, description="견줄 가까운 이웃 수, 1 이상. 기본 5.")] = 5,
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

_API_KEY_DESC = ("허브 API 키(REST 의 X-API-Key 와 같은 값). 서버가 인증을 요구할 때만 필요하다 — "
                 "빈 글(기본)이면 익명으로 시도하고, 인증이 필요한 서버면 status='error' · "
                 "code='auth_failed' 가 온다.")


@mcp.tool(
    title="Import a record (drag&drop 표/문서 → 저장)",
    description=(
        "사용자가 붙여넣은 표 · 문서를 허브의 레코드 규격으로 검증(dry_run, 기본)하거나 저장한다.\n"
        "흐름 — ① 첨부를 record 객체로 옮긴다(DATA 면 content={headers:[…], rows:[[…]]}, DOC 면 "
        "content={sections:[…]}). ② dry_run=true 로 부른다 — 모자란 필드가 있으면 "
        "status='incomplete' 와 ask_user(예 ['title','team','group']) · suggestions 가 온다. 그 "
        "필드만 사용자에게 물어 같은 record 에 합친다. ③ 다 차면 status='ready'(id · "
        "would='create'|'update'). ④ 같은 record 로 dry_run=false 를 부르면 status='saved' · id · "
        "action. 실패는 status='error' · code · recoverable · suggestion.\n"
        "주의 — team · group 은 절대 짐작해 채우지 말고 사용자에게 확인한다(틀리면 엉뚱한 곳에 "
        "저장돼 못 찾는다). 먼저 find_similar_data 로 비슷한 기존 데이터를 보고, "
        "describe_record_schema 로 doc_type · tags · summary 를 보강한다. 바이너리 파일(docx · "
        "xlsx · pdf · pptx)은 convert_file 로 초안을 받는다. 호출마다 독립이라 record 전체를 매번 "
        "보낸다.\n"
        "쓰기 — dry_run=false 면 레코드를 저장한다. 이미 있는 id 를 주면 그 레코드를 "
        "덮어쓴다(would='update'). id 가 없으면 새 번호를 매기므로 같은 호출을 되풀이하면 레코드가 "
        "거듭 생긴다. year 가 없으면 올해로 채우고 auto_filled 로 알린다."
    ),
    annotations=_hints(read_only=False, destructive=True, idempotent=False),
)
async def import_record(
    record: Annotated[dict[str, Any], Field(
        description="저장할 레코드 객체. 자주 쓰는 키 — data_type(" + _DATA_TYPES_DESC
                    + "), title, team, group, year, summary, tags, doc_type, agents, "
                      "content(data_type 마다 꼴이 다르다 — describe_data_capability), id(있으면 "
                      "그 레코드를 덮어쓴다). 채울 수 있는 키 전체는 describe_record_schema.")],
    dry_run: Annotated[bool, Field(
        description="true(기본)면 검증만 하고 저장하지 않는다. false 면 저장한다.")] = True,
    api_key: Annotated[str, Field(description=_API_KEY_DESC)] = "",
    agent_type: Annotated[str, Field(
        description="받기만 하고 지금은 쓰지 않는다(옛 호출과의 호환용). 레코드를 전문가에 "
                    "묶으려면 record 의 agents 를 채우거나 bind_records_to_agent 를 쓴다.")] = "",
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
        "전문가(agent — 검색 챗봇 페르소나)를 정의할 때 채울 필드와 각 필드의 뜻을 돌려준다.\n"
        "create_agent · patch_agent 를 부르기 전에 무엇을 채울지 확인할 때 쓴다. 기존 레코드에서 "
        "초안을 지으려면 draft_agent, 한정할 doc_type code 는 list_doc_types 로 고른다. "
        "레코드(데이터) 쪽 메타데이터 안내는 describe_record_schema 로 다른 도구다.\n"
        "돌려주는 꼴 — {fields:{agent_type, name, description, data_types, required_doc_type, "
        "common_tags, required_tags, excluded_tags, system_prompt, sample_queries, "
        "retrieval_config — 필드마다 뜻}, note}. agent_type(kebab-case 고유 키)과 system_prompt 가 "
        "필수다. sample_queries 는 저장할 때 저절로 임베딩돼 recommend_agents 의 맞춤에 쓰인다.\n"
        "인자 없음 · 고정 안내라 DB 를 읽지도 쓰지도 않는다."
    ),
    annotations=_READ,
)
async def describe_agent_schema() -> dict[str, Any]:
    from .services import mcp_write_svc
    return await mcp_write_svc.describe_agent_schema()


@mcp.tool(
    title="Agent 정의 초안 생성 (저장 안 함)",
    description=(
        "기존 레코드의 신호(태그 · data_type · 제목)와 자연어 의도(hint)로 전문가(agent) 정의 "
        "초안을 짓는다 — 저장하지 않는다.\n"
        "예 hint='배터리 셀 시험보고서만 찾는 분석가'. 신호를 모을 레코드는 record_ids 로 바로 "
        "주거나 filter_tags · filter_data_types 로 좁힌다. 결과의 system_prompt · sample_queries "
        "를 사람이 읽기 좋게 보여 주고 다듬을지 물은 뒤 create_agent 로 저장한다. 필드 뜻은 "
        "describe_agent_schema 다.\n"
        "서버에 LLM 이 설정돼 있으면 LLM 으로 짓고(draft._source='llm'), 없거나 실패하면 규칙으로 "
        "짓는다(_source='heuristic') — 같은 인자라도 LLM 초안은 부를 때마다 다를 수 있다.\n"
        "돌려주는 꼴 — {status:'draft', draft:{agent_type, name, description, system_prompt, "
        "sample_queries, data_types, …}, note}. 인증이 필요한 서버에서 키가 맞지 않으면 "
        "status='error' · code='auth_failed'."
    ),
    annotations=_READ_LLM,
)
async def draft_agent(
    hint: Annotated[str, Field(
        description="지을 전문가의 의도를 적은 자연어(예 '배터리 셀 시험보고서만 찾는 분석가'). 빈 "
                    "글(기본)이면 레코드 신호만으로 짓는다.")] = "",
    record_ids: Annotated[list[str] | None, Field(
        description="신호를 모을 레코드 id 목록 — 주면 아래 거름보다 앞선다. 비우면(null) 거름으로 "
                    "고른다.")] = None,
    filter_tags: Annotated[list[str] | None, Field(
        description="이 태그 가운데 하나라도 가진 레코드에서 신호를 모은다(겹침). 비우면(null) "
                    "거르지 않는다.")] = None,
    filter_data_types: Annotated[list[str] | None, Field(
        description=f"이 data_type 의 레코드에서 신호를 모은다({_DATA_TYPES_DESC}). 비우면(null) "
                    "거르지 않는다.")] = None,
    api_key: Annotated[str, Field(description=_API_KEY_DESC)] = "",
) -> dict[str, Any]:
    from .services import mcp_write_svc
    return await mcp_write_svc.draft_agent(
        hint=hint or None, record_ids=record_ids, filter_tags=filter_tags,
        filter_data_types=filter_data_types, api_key=api_key or None,
    )


@mcp.tool(
    title="Agent 신규 등록",
    description=(
        "새 전문가(agent — 검색 챗봇 페르소나)를 저장한다.\n"
        "agent 객체에 최소 agent_type 과 system_prompt, 권장으로 sample_queries(3 ~ 5개)와 "
        "data_types 를 담는다 — 필드 뜻은 describe_agent_schema, 초안은 draft_agent 로 짓는다. "
        "이미 있는 agent_type 이면 저장하지 않고 code='duplicate' 를 돌려준다 — 그때는 patch_agent "
        "로 고친다. 저장한 뒤 그 전문가가 찾을 레코드를 이으려면 bind_records_to_agent 를 부른다. "
        "sample_queries 는 저장할 때 저절로 임베딩돼 recommend_agents 맞춤에 쓰인다.\n"
        "돌려주는 꼴 — {status:'created', agent_type, samples_indexed, samples_stale}. 실패는 "
        "status='error' · code(missing_field · invalid_field · duplicate · auth_failed) · "
        "suggestion — invalid_field 면 어느 칸이 왜 틀렸는지 error · fields 에 적는다.\n"
        "쓰기 — 전문가 하나를 새로 만든다. 기존 전문가는 바꾸지 않는다(같은 호출을 다시 하면 "
        "duplicate)."
    ),
    annotations=_hints(read_only=False, destructive=False, idempotent=False),
)
async def create_agent(
    agent: Annotated[dict[str, Any], Field(
        description="전문가 정의 객체. 필수 agent_type(kebab-case 고유 키, 예 "
                    "'battery-test-analyst')과 system_prompt. 그 밖 name(생략하면 agent_type) · "
                    "description · data_types · required_doc_type · common_tags · required_tags · "
                    "excluded_tags · sample_queries · retrieval_config({top_k, score_threshold}) — "
                    "형은 describe_agent_schema.")],
    api_key: Annotated[str, Field(description=_API_KEY_DESC)] = "",
) -> dict[str, Any]:
    from .services import mcp_write_svc
    return await mcp_write_svc.create_agent(agent=agent, api_key=api_key or None)


@mcp.tool(
    title="Agent 부분 수정",
    description=(
        "기존 전문가(agent)의 필드 일부만 고친다 — patch 에 담은 필드만 바꾸고 나머지는 그대로 "
        "둔다.\n"
        "빈 껍데기 전문가에 페르소나를 채우거나 system_prompt · sample_queries · 태그 거름을 "
        "다듬을 때 쓴다. 새로 만들 때는 create_agent, 필드 뜻은 describe_agent_schema 다. "
        "sample_queries 를 바꾸면 다시 임베딩된다. 태그 · data_types 거름을 바꾼 뒤 레코드를 다시 "
        "이으려면 bind_records_to_agent 를 부른다.\n"
        "돌려주는 꼴 — {status:'patched', agent_type, samples_indexed, samples_stale}. 없는 "
        "agent_type 이면 code='not_found', 필드 형이 틀리면 'invalid_field'(어느 칸이 왜 틀렸는지 "
        "error · fields 에), 인증 실패면 'auth_failed'.\n"
        "쓰기 — 준 필드의 기존 값을 덮어쓴다(되돌리기 없음). 같은 patch 를 다시 보내도 결과는 같다."
    ),
    annotations=_hints(read_only=False, destructive=True, idempotent=True),
)
async def patch_agent(
    agent_type: Annotated[str, Field(description="고칠 전문가 키(agent_type).")],
    patch: Annotated[dict[str, Any], Field(
        description="바꿀 필드만 담은 객체(예 {\"sample_queries\": [\"…\"], \"system_prompt\": "
                    "\"…\"}). 쓸 수 있는 키는 create_agent 의 agent 와 "
                    "같다(describe_agent_schema). 담지 않은 필드는 그대로다.")],
    api_key: Annotated[str, Field(description=_API_KEY_DESC)] = "",
) -> dict[str, Any]:
    from .services import mcp_write_svc
    return await mcp_write_svc.patch_agent(agent_type=agent_type, patch=patch, api_key=api_key or None)


@mcp.tool(
    title="Agent 에 매칭 레코드 연결",
    description=(
        "전문가(agent) 정의의 거름 조건에 맞는 기존 레코드를 그 전문가에 잇는다 — 레코드의 agents "
        "에 이 agent_type 을 더한다.\n"
        "create_agent 로 전문가를 정의한 뒤 '이 전문가가 검색할 데이터를 잇는' 마지막 단계다. 이은 "
        "레코드는 agent_search · get_context_bundle 이 찾는다. 맞춤은 doc_type "
        "일치(required_doc_type 이 있으면) AND data_type 포함(data_types 가 있으면) AND "
        "required_tags 모두 포함, required_tags 가 없으면 common_tags 가운데 하나 이상이다. 주의 — "
        "태그 조건이 하나도 없으면 태그로 거르지 않아 doc_type · data_type 거름을 지난 레코드를 "
        "모두 잇고, 거름이 하나도 없으면 훑은 레코드 전부를 잇는다. 부르기 전에 정의의 거름을 "
        "확인한다.\n"
        "돌려주는 꼴 — {status:'bound', agent_type, scanned, scan_limit, scan_truncated, "
        "bound_count, bound_record_ids(앞 50개), bound_ids_omitted}. 없는 agent_type 이면 "
        "code='not_found', 인증 실패면 'auth_failed'.\n"
        "한도 — 후보를 정렬 없이 최대 500건만 훑는다(태그 거름 전). scan_truncated:true 면 뒤쪽 "
        "후보는 보지 않았다.\n"
        "쓰기 — 더하기만 한다. 이미 이어진 레코드는 건너뛰고 기존 연결을 풀지 않아, 다시 불러도 "
        "결과가 같다."
    ),
    annotations=_hints(read_only=False, destructive=False, idempotent=True),
)
async def bind_records_to_agent(
    agent_type: Annotated[str, Field(description="레코드를 이을 전문가 키(agent_type) — 먼저 "
                                                 "create_agent 로 만든다.")],
    api_key: Annotated[str, Field(description=_API_KEY_DESC)] = "",
) -> dict[str, Any]:
    from .services import mcp_write_svc
    return await mcp_write_svc.bind_records_to_agent(agent_type=agent_type, api_key=api_key or None)


@mcp.tool(
    title="DocType 목록",
    description=(
        "등록된 doc_type(레코드의 의미 분류) 목록을 돌려준다 — code · name · description · "
        "expected_sections.\n"
        "create_agent 의 required_doc_type, list_records 의 doc_type 거름, import_record 의 "
        "doc_type 에 넣을 code 를 고를 때 쓴다. 목록에 없는 새 분류를 더하려면 create_doc_type "
        "이다. 레코드 쪽 메타데이터 전반의 안내(고를 수 있는 값 · 예시)는 describe_record_schema "
        "가 함께 준다.\n"
        "돌려주는 꼴 — {doc_types:[{code, name, description, expected_sections}]}. "
        "expected_sections 는 그 분류의 문서가 가질 절 제목 목록이다.\n"
        "한도 — 전부 · 자르지 않는다. 읽기만 하고 인증을 보지 않는다."
    ),
    annotations=_READ,
)
async def list_doc_types(
    api_key: Annotated[str, Field(
        description="받기만 하고 쓰지 않는다 — 목록 조회는 인증을 보지 않는다(옛 호출과의 "
                    "호환용).")] = "",
) -> dict[str, Any]:
    from .services import mcp_write_svc
    return await mcp_write_svc.list_doc_types(api_key=api_key or None)


@mcp.tool(
    title="DocType 신규 등록",
    description=(
        "새 doc_type(레코드의 의미 분류)을 등록한다.\n"
        "새 종류의 문서를 분류 체계에 더할 때 쓴다. 먼저 list_doc_types 로 이미 있는 code 인지 "
        "확인한다. 등록한 code 는 import_record · list_records 의 doc_type, create_agent 의 "
        "required_doc_type 에 쓴다.\n"
        "돌려주는 꼴 — {status:'created', code}. code 가 없으면 status='error' · "
        "code='missing_field', 이미 있거나 저장에 실패하면 'create_failed'(error 에 까닭 앞 "
        "160자), 인증 실패면 'auth_failed'.\n"
        "쓰기 — 분류 하나를 새로 만든다. 기존 분류는 바꾸지 않고, 같은 code 로 다시 부르면 "
        "create_failed 다."
    ),
    annotations=_hints(read_only=False, destructive=False, idempotent=False),
)
async def create_doc_type(
    doc_type: Annotated[dict[str, Any], Field(
        description="doc_type 정의 객체. 필수 code(식별자)와 name, 권장 description · "
                    "expected_sections(그 분류의 문서가 가질 절 제목 목록).")],
    api_key: Annotated[str, Field(description=_API_KEY_DESC)] = "",
) -> dict[str, Any]:
    from .services import mcp_write_svc
    return await mcp_write_svc.create_doc_type(doc_type=doc_type, api_key=api_key or None)


@mcp.tool(
    title="파일 정밀 변환 (docx/xlsx/pdf/pptx → record)",
    description=(
        "서버 inbox 폴더에 둔 docx · xlsx · pdf · pptx · md 파일을 허브의 레코드 꼴로 정밀 변환해 "
        "record 초안을 돌려준다 — 저장하지 않는다.\n"
        "MCP 로는 바이너리를 직접 받지 못한다. 표 · CSV · 텍스트처럼 직접 옮길 수 있으면 "
        "import_record 로 바로 보내고, 수식 · 병합 셀 · 복잡한 표가 있어 정밀 변환이 필요하면 이 "
        "도구를 쓴다 — 사용자가 inbox 에 파일을 두고 파일명만 넘긴다. team · group · year 를 알면 "
        "함께 주고(모르면 'TBD' · 올해로 채워 import_record 가 다시 묻는다), 결과 record 를 검토한 "
        "뒤 import_record 로 저장한다.\n"
        "돌려주는 꼴 — {status:'converted', format, record, note}. auto_save=true 면 "
        "import_record(dry_run=true) 미리보기 import_preview 가 붙는다. 실패는 status='error' · "
        "code(missing_field · not_in_inbox · unsupported_format · drm_protected · convert_failed · "
        "auth_failed) · suggestion. 암호화(DRM) 문서는 변환기에 넣지 않고 drm_protected 로 "
        "알린다.\n"
        "보안 — inbox 아래 파일만 읽는다('..' 나 절대경로로 벗어나면 not_in_inbox). 변환 중간 "
        "파일은 서버 임시 폴더에만 쓴다."
    ),
    annotations=_READ,
)
async def convert_file(
    file: Annotated[str, Field(
        description="inbox 안의 파일명(inbox 아래 하위 경로까지 된다, 예 'report.docx'). 확장자로 "
                    "형식을 정한다.")],
    team: Annotated[str, Field(
        description="레코드에 넣을 팀 코드(대문자로 바꾼다). 빈 글(기본)이면 'TBD' — import_record "
                    "가 다시 묻는다.")] = "",
    group: Annotated[str, Field(
        description="레코드에 넣을 그룹 코드(대문자로 바꾼다). 빈 글(기본)이면 'TBD' — "
                    "import_record 가 다시 묻는다.")] = "",
    year: Annotated[int, Field(ge=0, description="레코드 연도. 0(기본)이면 올해.")] = 0,
    auto_save: Annotated[bool, Field(
        description="true 면 변환 뒤 import_record(dry_run=true) 미리보기를 함께 돌려준다 — 이름과 "
                    "달리 저장은 하지 않는다(저장은 import_record(dry_run=false)). 기본 "
                    "false.")] = False,
    api_key: Annotated[str, Field(description=_API_KEY_DESC)] = "",
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
