"""Agent 추천 + LLM-ready context bundle / system prompt 생성 서비스.

설계 노트:
    - 추천: 자연어 쿼리 → ``/api/search`` 의미검색 결과 → record→agents 카운트 +
      score 가중 합산. 단순 의미검색 집계 정책 (Plan: agent-discovery-console).
    - context-bundle: agent 의 records + top sections 를 LLM 친화 markdown 또는
      JSON 으로 묶음. ``max_records`` 로 토큰 절약.
    - system-prompt: Cline / Qwen 등에 그대로 붙여넣을 텍스트. 도구 호출
      가이드 + 본 agent 의 역할 + 호스트 URL 자리표시자 포함.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Agent, Record, RecordSection
from ..services import sample_embedding_svc, search_svc

# v0.14.0 — recommend_agents 점수 정책 (실데이터 적재로 스케일 불균형 발견 후 개정).
# 과거: record 항 = sum/candidate_sections (전역 50 으로 나눠 항상 과소),
#       sample 항 = sum(sims) × 5.0 (개수에 비례 + 5배 → ~17배 지배).
# 개정: 두 항 모두 [0,1] 평균값으로 정규화.
# - record 항 = 해당 agent 의 매칭 section 평균 유사도 (∈[0,1]).
# - sample 항 = 해당 agent 의 (cap 적용) sample 평균 유사도 (∈[0,1], sum 아님).
# - 합산 score = record_mean + _SAMPLE_WEIGHT × sample_mean.
#   기본 1.0 → "완벽한 sample 매칭 ≈ 완벽한 콘텐츠 매칭". 환경변수로 조정.
# - per-agent sample cap: 한 agent 가 sample 로 top-k 를 독점하지 못하게 제한.
_SAMPLE_WEIGHT = float(os.environ.get("AGENT_SAMPLE_WEIGHT", "1.0"))
_SAMPLE_TOP_K = int(os.environ.get("AGENT_SAMPLE_TOP_K", "20"))
_SAMPLE_PER_AGENT_CAP = int(os.environ.get("AGENT_SAMPLE_PER_AGENT_CAP", "3"))


# ---------------------------------------------------------------------------
# 1) Agent 추천
# ---------------------------------------------------------------------------
async def recommend_agents(
    session: AsyncSession,
    *,
    query: str,
    top_k: int = 5,
    candidate_sections: int = 50,
) -> list[dict[str, Any]]:
    """자연어 쿼리 → ranked agents.

    의미검색 top-N sections 가져와서 각 section 의 record 에 등록된 agents
    별로 score 합산. agent 의 record_count / matched_sections / why 도 함께.
    """
    if not query or not query.strip():
        return []

    # 1) 의미검색 top-N sections
    search_results = await search_svc.semantic_search(
        session, query, top_k=candidate_sections
    )

    # 2) record_id 모음 → records + agents 조회
    record_ids: set[str] = set()
    section_score_by_rid: dict[str, list[float]] = defaultdict(list)
    for item in search_results or []:
        rid = item.get("record_id") or item.get("id")
        if not rid:
            continue
        record_ids.add(rid)
        section_score_by_rid[rid].append(float(item.get("score") or 0.0))

    rec_rows = []
    if record_ids:
        rec_rows = (
            await session.execute(
                select(Record).where(Record.id.in_(list(record_ids)))
            )
        ).scalars().all()

    # 3) agent 집계 — record-section 기여분.
    # section 유사도 합과 개수를 모은 뒤(아래 3c) 에서 agent별 평균 = mean
    # section similarity ∈ [0,1] 로 환산한다. 전역 candidate_sections 로
    # 나누던 과거 방식은 콘텐츠가 풍부한 agent 를 부당하게 과소평가했다.
    agent_section_sim_sum: dict[str, float] = defaultdict(float)
    agent_records: dict[str, set[str]] = defaultdict(set)
    agent_sections: dict[str, int] = defaultdict(int)
    for r in rec_rows:
        rid_scores = section_score_by_rid.get(r.id, [])
        rid_score_sum = sum(rid_scores)
        for at in r.agents or []:
            agent_section_sim_sum[at] += rid_score_sum
            agent_records[at].add(r.id)
            agent_sections[at] += len(rid_scores)

    # 3b) v0.13.0 — agent_sample_embeddings 기여분. per-agent cap 적용으로 한
    # agent 가 sample 을 과적재해도 routing 점수를 독점하지 못하게 한다.
    sample_hits = await sample_embedding_svc.search_samples(
        session, query, top_k=_SAMPLE_TOP_K
    )
    agent_sample_score: dict[str, float] = defaultdict(float)
    agent_sample_hits: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for h in sample_hits or []:
        at = h.get("agent_type")
        if not at:
            continue
        if len(agent_sample_hits[at]) >= _SAMPLE_PER_AGENT_CAP:
            continue  # per-agent cap — 추가 sample 은 점수에 합산하지 않음
        s = float(h.get("score") or 0.0)
        agent_sample_score[at] += s
        agent_sample_hits[at].append(
            {"sample_text": h.get("sample_text") or "", "score": s}
        )

    # 3c) 두 항 모두 [0,1] 평균으로 환산 후 합산.
    # record_mean  = agent 매칭 section 의 평균 유사도.
    # sample_mean  = agent (cap 적용) sample 의 평균 유사도.
    agent_score: dict[str, float] = defaultdict(float)
    all_ats = set(list(agent_section_sim_sum.keys()) + list(agent_sample_score.keys()))
    for at in all_ats:
        n_sec = agent_sections.get(at, 0)
        record_mean = (agent_section_sim_sum[at] / n_sec) if n_sec else 0.0
        n_smp = len(agent_sample_hits.get(at, []))
        sample_mean = (agent_sample_score[at] / n_smp) if n_smp else 0.0
        agent_score[at] = record_mean + _SAMPLE_WEIGHT * sample_mean

    if not agent_score:
        return []

    # 4) agent 메타 일괄 조회
    agent_metas = (
        await session.execute(
            select(Agent).where(Agent.agent_type.in_(list(agent_score.keys())))
        )
    ).scalars().all()
    meta_by_type = {a.agent_type: a for a in agent_metas}

    # 5) ranked list — why 에 sample 매칭이 있으면 함께 표기
    ranked: list[dict[str, Any]] = []
    for at, sc in sorted(agent_score.items(), key=lambda kv: kv[1], reverse=True):
        meta = meta_by_type.get(at)
        why_parts = []
        if agent_sections[at] or agent_records[at]:
            why_parts.append(
                f"의미검색 top-{candidate_sections} 결과 중 "
                f"{agent_sections[at]} sections / {len(agent_records[at])} records 가 "
                f"이 agent 소속"
            )
        if agent_sample_hits[at]:
            top_sample = agent_sample_hits[at][0]
            why_parts.append(
                f"sample 매칭 {len(agent_sample_hits[at])}건 (top: "
                f"\"{top_sample['sample_text'][:40]}\" sim={top_sample['score']:.2f})"
            )
        ranked.append(
            {
                "agent_type": at,
                "name": (meta.name if meta else at),
                "description": (meta.description if meta else "") or "",
                "common_tags": list((meta.common_tags if meta else []) or []),
                "data_types": list((meta.data_types if meta else []) or []),
                "score": round(sc, 4),
                "matched_records": len(agent_records[at]),
                "matched_sections": agent_sections[at],
                "matched_samples": len(agent_sample_hits[at]),
                "why": " · ".join(why_parts) if why_parts else "(no direct evidence)",
            }
        )

    return ranked[:top_k]


# ---------------------------------------------------------------------------
# 2) Context Bundle
# ---------------------------------------------------------------------------
async def build_context_bundle(
    session: AsyncSession,
    *,
    agent_type: str,
    max_records: int = 10,
    max_sections_per_record: int = 8,
) -> dict[str, Any] | None:
    """agent 의 records + 핵심 sections 를 LLM 친화 JSON 으로 묶는다.

    Returns:
        ``None`` if agent 미존재.
    """
    agent = await session.get(Agent, agent_type)
    if agent is None:
        return None

    # v0.13.0 — context-bundle 에는 라이브 쿼리가 없으므로 score_threshold 가
    # 의미를 가지려면 relevance anchor 가 필요하다. agent.sample_queries 를
    # anchor 로 삼아 각 section embedding 과의 MAX 코사인 유사도를 relevance
    # 로 쓰고, threshold 미달 section 을 드롭한다 (BOTH 조건일 때만).
    rc_cfg = dict(getattr(agent, "retrieval_config", None) or {})
    sample_queries: list[str] = [
        s for s in (list(getattr(agent, "sample_queries", None) or [])) if s
    ]
    _raw_thr = rc_cfg.get("score_threshold")
    score_threshold: float | None = None
    if isinstance(_raw_thr, (int, float)) and not isinstance(_raw_thr, bool):
        score_threshold = float(_raw_thr)
    do_filter = score_threshold is not None and bool(sample_queries)

    # sample_query 벡터는 per-section 루프 전에 1회만 인코딩 (캐시).
    query_vecs: list[Any] = []
    if do_filter:
        import numpy as np

        from .embedding import get_embedder

        embedder = get_embedder()
        for sq in sample_queries:
            # sample query 는 section passage 벡터와 대조되는 질의 역할 — query prefix.
            qv = np.asarray(embedder.encode_query(sq), dtype="float32")
            qn = float(np.linalg.norm(qv))
            if qn < 1e-12:
                continue
            query_vecs.append(qv / qn)
        # 모든 anchor 벡터가 zero-norm 이면 의미있는 스코어링 불가 → 필터 비활성.
        if not query_vecs:
            do_filter = False

    def _relevance(emb: Any) -> float | None:
        """search_svc.semantic_search 의 SQLite 경로와 동일한 코사인 수식.

        normalize → dot → cosine ∈ [-1,1] 를 (sim+1)/2 로 [0,1] 매핑.
        sample_queries 전체에 대한 MAX 유사도를 반환. emb None → None.
        """
        if emb is None:
            return None
        import numpy as np

        v = np.asarray(emb, dtype="float32")
        vnorm = float(np.linalg.norm(v))
        if vnorm < 1e-12:
            return None
        best: float | None = None
        for qv in query_vecs:
            if v.shape != qv.shape:
                continue
            sim = float(np.dot(qv, v) / vnorm)
            sim01 = max(0.0, min(1.0, (sim + 1.0) / 2.0))
            best = sim01 if best is None else max(best, sim01)
        return best

    # agent 소속 records — agents ARRAY 에 포함된 것
    # GIN index 활용을 위해 PostgreSQL 의 ANY 연산 사용 (dialect-agnostic 폴백 X).
    from sqlalchemy import literal
    stmt = (
        select(Record)
        .where(literal(agent_type) == Record.agents.any_())  # type: ignore[attr-defined]
        .order_by(Record.created_at.desc())
        .limit(max_records)
    )
    try:
        rec_rows = (await session.execute(stmt)).scalars().all()
    except Exception:
        # 폴백 — 파이썬 후필터
        rec_rows = []
        all_rows = (
            await session.execute(select(Record).order_by(Record.created_at.desc()))
        ).scalars().all()
        for r in all_rows:
            if agent_type in (r.agents or []):
                rec_rows.append(r)
                if len(rec_rows) >= max_records:
                    break

    records_payload: list[dict[str, Any]] = []
    for r in rec_rows:
        if do_filter:
            # relevance 필터 활성 — level cap 전에 후보를 넓게 가져와서
            # sample_queries anchor 와의 MAX 코사인으로 스코어링 후
            # threshold 미달을 드롭, relevance 내림차순 정렬 → cap.
            sec_rows = (
                await session.execute(
                    select(RecordSection)
                    .where(RecordSection.record_id == r.id)
                    .order_by(RecordSection.level.asc(), RecordSection.id.asc())
                )
            ).scalars().all()
            scored_secs: list[tuple[float | None, RecordSection]] = []
            for s in sec_rows:
                rel = _relevance(s.embedding)
                if rel is None:
                    # embedding 없음 → 스코어 불가. 드롭하지 않고 유지.
                    scored_secs.append((None, s))
                elif rel >= score_threshold:  # type: ignore[operator]
                    scored_secs.append((rel, s))
                # rel < threshold → 드롭.
            # relevance 내림차순. None(미스코어)은 뒤로.
            scored_secs.sort(
                key=lambda t: (t[0] is not None, t[0] or 0.0), reverse=True
            )
            key_sections: list[dict[str, Any]] = []
            for rel, s in scored_secs[:max_sections_per_record]:
                item: dict[str, Any] = {
                    "section_id": s.section_id,
                    "level": s.level,
                    "title": s.title,
                    "excerpt": (s.content_text or "")[:500],
                }
                if rel is not None:
                    item["relevance"] = round(rel, 4)
                key_sections.append(item)
        else:
            # 핵심 sections — record_sections 에서 상위 N (level 정렬)
            secs = (
                await session.execute(
                    select(RecordSection)
                    .where(RecordSection.record_id == r.id)
                    .order_by(RecordSection.level.asc(), RecordSection.id.asc())
                    .limit(max_sections_per_record)
                )
            ).scalars().all()
            key_sections = [
                {
                    "section_id": s.section_id,
                    "level": s.level,
                    "title": s.title,
                    "excerpt": (s.content_text or "")[:500],
                }
                for s in secs
            ]
        records_payload.append(
            {
                "id": r.id,
                "data_type": r.data_type,
                "team": r.team,
                "group": r.group,
                "title": r.title,
                "summary": r.summary or "",
                "tags": list(r.tags or []),
                "doc_type": r.doc_type,
                "key_sections": key_sections,
            }
        )

    return {
        "agent": {
            "agent_type": agent.agent_type,
            "name": agent.name,
            "description": agent.description or "",
            "common_tags": list(agent.common_tags or []),
            "data_types": list(agent.data_types or []),
            "required_doc_type": agent.required_doc_type,
            "required_tags": list(agent.required_tags or []),
            # v0.13.0 — RAG recipe (Migration 0014). Admin-controlled.
            "system_prompt": getattr(agent, "system_prompt", None) or None,
            "retrieval_config": dict(getattr(agent, "retrieval_config", None) or {}),
            "response_config": dict(getattr(agent, "response_config", None) or {}),
            "sample_queries": list(getattr(agent, "sample_queries", None) or []),
        },
        "records": records_payload,
        # v0.13.0 — context-bundle 에 relevance 필터가 적용됐는지 노출.
        # applied=True 면 key_sections 가 sample_queries anchor MAX-코사인
        # 으로 score_threshold 필터 + relevance 내림차순 정렬된 결과.
        "retrieval_filter": {
            "applied": bool(do_filter),
            "score_threshold": score_threshold if do_filter else None,
            "anchor": "sample_queries",
        },
        "totals": {
            "records_returned": len(records_payload),
            "max_records": max_records,
            "max_sections_per_record": max_sections_per_record,
        },
        # v0.13.x — URL 경로 대신 MCP 도구 이름으로 안내한다. (raw /api/ URL 을
        # 노출하면 WebFetch 가 http→https 승격 후 무인증서 내부서버에서 실패.)
        "next_steps": {
            "record_detail": "MCP tool: get_record(record_id)",
            "record_sections": "MCP tool: get_record_sections(record_id)",
            "semantic_search": f'MCP tool: agent_search("{agent_type}", q, mode="semantic")',
            "natural_language": f'MCP tool: agent_search("{agent_type}", q)',
        },
    }


def render_context_bundle_markdown(bundle: dict[str, Any]) -> str:
    """JSON bundle → LLM-ready markdown."""
    a = bundle["agent"]
    out: list[str] = []
    out.append(f"# Agent: {a['name']} (`{a['agent_type']}`)")
    out.append("")
    out.append("## Description")
    out.append(a.get("description") or "_no description_")
    out.append("")
    out.append("## Metadata")
    out.append(f"- common_tags: {', '.join(a.get('common_tags', [])) or '(none)'}")
    out.append(f"- data_types: {', '.join(a.get('data_types', [])) or '(none)'}")
    if a.get("required_doc_type"):
        out.append(f"- required_doc_type: {a['required_doc_type']}")
    if a.get("required_tags"):
        out.append(f"- required_tags: {', '.join(a['required_tags'])}")
    # v0.13.0 — RAG recipe (admin-controlled). Only render non-empty sections.
    rc = a.get("retrieval_config") or {}
    rsp = a.get("response_config") or {}
    samples = a.get("sample_queries") or []
    sys_p = (a.get("system_prompt") or "").strip()
    if rc.get("top_k") is not None or rc.get("score_threshold") is not None:
        bits = []
        if rc.get("top_k") is not None:
            bits.append(f"top_k={rc['top_k']}")
        if rc.get("score_threshold") is not None:
            bits.append(f"score_threshold={rc['score_threshold']}")
        out.append(f"- retrieval_config: {', '.join(bits)}")
    if rsp:
        bits = []
        if rsp.get("max_tokens") is not None:
            bits.append(f"max_tokens={rsp['max_tokens']}")
        if rsp.get("citation_required"):
            bits.append("citation_required=true")
        if rsp.get("refusal_message"):
            bits.append(f"refusal_message={rsp['refusal_message']!r}")
        if bits:
            out.append(f"- response_config: {', '.join(bits)}")
    if samples:
        out.append(f"- sample_queries ({len(samples)}): " + "; ".join(f"\"{s}\"" for s in samples[:5]))
    out.append("")
    if sys_p:
        out.append("## System prompt (admin-defined)")
        out.append("```")
        out.append(sys_p)
        out.append("```")
        out.append("")

    records = bundle.get("records", [])
    out.append(f"## Records ({len(records)})")
    out.append("")
    for r in records:
        out.append(f"### `{r['id']}` — {r['title']}")
        out.append(f"- team/group: **{r['team']} / {r['group']}**")
        out.append(f"- data_type: **{r['data_type']}**" + (f", doc_type: **{r['doc_type']}**" if r.get('doc_type') else ""))
        if r.get("tags"):
            out.append(f"- tags: {', '.join(r['tags'])}")
        out.append(f"- summary: {r.get('summary') or '_no summary_'}")
        out.append("")
        if r.get("key_sections"):
            out.append("**Key sections:**")
            for s in r["key_sections"]:
                t = s.get("title") or s.get("section_id")
                excerpt = (s.get("excerpt") or "").replace("\n", " ").strip()
                out.append(f"- `§{s.get('section_id')}` _{t}_ — {excerpt[:200]}...")
            out.append("")

    eps = bundle.get("next_steps", {})
    out.append("## How to query for more — call these MCP tools by name")
    out.append("(Do NOT WebFetch a URL — http→https 승격으로 무인증서 내부서버에서 실패.)")
    out.append("```")
    for k, v in eps.items():
        out.append(f"{k:20s} → {v}")
    out.append("```")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# 3) System Prompt (Cline / Qwen 붙여넣기용)
# ---------------------------------------------------------------------------
# admin-set system_prompt 본문에 이 마커가 있으면 도구 가이드를 append 하지 않는다.
_NO_TOOL_GUIDE_MARKER = "<!-- no-tool-guide -->"


def _tool_guide_block(agent_meta: Agent, base_url: str) -> str:
    """admin 의 짧은 persona prompt 뒤에 자동 append 되는 도구·관례 가이드.

    Haiku 급 LLM 이 API 호출을 환각하지 않도록 도구 surface 와 record-id 규약을
    명시한다. ``out-of-domain`` fallback 문구도 포함.
    """
    common_tags = ", ".join(agent_meta.common_tags or []) or "(none)"
    at = agent_meta.agent_type
    return f"""## How to access this hub — use the MCP tools (NOT web fetch)

This hub is registered as an MCP server. Call these MCP tools BY NAME via
your MCP client. They already work over the (possibly plain-HTTP) MCP
transport — do not turn them into URL fetches.

- get_agent_session("{at}")  — call FIRST: persona + retrieval/response config
- agent_search("{at}", q, mode="semantic"|"fts"|"tag")  — primary search,
  auto-applies this agent's retrieval_config
- get_record_sections(record_id)  — full RAG chunks of a hit
- get_record(record_id)           — full record
- discover() / list_agents()      — catalog / agents
- recommend_agents(q)             — re-route when out of this agent's domain

## CRITICAL — do NOT use WebFetch / browser fetch on this hub
WebFetch (and similar) auto-upgrade http:// to https://. Internal hub
servers often have NO TLS certificate, so the TLS handshake fails and the
call drops. Always use the MCP tools above instead.

If — and only if — you must hit the REST API directly, use a shell
command (curl) with the LITERAL http:// scheme and do not let it switch
to https:
  curl -s "{base_url}/api/discover"
  curl -s "{base_url}/api/records/<id>/sections"
(Use `--http1.1` if needed; never add https://.)

## Conventions
- Record ID format: `{{DATA_TYPE}}-{{TEAM}}-{{GROUP}}-{{YEAR}}-{{SEQ:010d}}` (e.g. `DOC-HE-CAE-2026-0000000001`).
- Cite the source after every factual claim: `(source: <record_id> §<section_id>)`.
- Korean and English are both supported in queries.
- The hub is read-mostly: avoid writes unless the user explicitly authorizes them.
- Out of this agent's domain ({common_tags})? Say so and call recommend_agents().

## Numerical calculations — use the calc tools, never compute by hand
For any numerical engineering calculation (stiffness, laminate/ABD, material
properties, warpage, fastening, buckling, etc.), do NOT compute or estimate by
hand — call the gateway's calculation MCP tools exposed alongside this hub. If
the calculation is outside your domain, say so and call recommend_agents() to
hand off to a calculation-specialist persona."""


def build_system_prompt(
    agent_meta: Agent, *, base_url: str = "http://<host>:8001"
) -> str:
    # v0.13.0 — admin 이 직접 system_prompt 를 세팅했으면 그것을 우선 사용하고,
    # 도구 가이드 블록을 자동 append. ``<!-- no-tool-guide -->`` 마커가 본문에
    # 있으면 append 를 생략 (admin 이 완전 통제하고 싶을 때 escape hatch).
    # ``{base_url}`` 플레이스홀더는 admin prompt 본문에서도 치환된다.
    admin_prompt = (getattr(agent_meta, "system_prompt", None) or "").strip()
    if admin_prompt:
        # v0.13.0 — 지원하는 placeholder 들 (둘 다 미사용해도 무영향).
        rendered = (
            admin_prompt
            .replace("{base_url}", base_url)
            .replace("{agent_type}", agent_meta.agent_type)
            .replace("{agent_name}", agent_meta.name or agent_meta.agent_type)
        )
        if _NO_TOOL_GUIDE_MARKER in rendered:
            return rendered.replace(_NO_TOOL_GUIDE_MARKER, "").strip()
        guide = _tool_guide_block(agent_meta, base_url)
        return f"{rendered}\n\n---\n\n{guide}\n"

    common_tags = ", ".join(agent_meta.common_tags or []) or "(none)"
    data_types = ", ".join(agent_meta.data_types or []) or "(none)"
    desc = agent_meta.description or "(no description)"
    guide = _tool_guide_block(agent_meta, base_url)

    return f"""You are an assistant for "{agent_meta.name}" (`{agent_meta.agent_type}`) inside the Mobile eXperience AI Data Hub.

## Your role
- Help the user with: {desc}
- Authoritative data domain: tags=[{common_tags}], data_types=[{data_types}]
- All factual answers MUST cite the source record id from this hub.

## First step on every conversation
1. Call the MCP tool  get_agent_session("{agent_meta.agent_type}")  — loads
   your persona + retrieval/response config.
2. For the user's question, call  agent_search("{agent_meta.agent_type}", q)
   then  get_record_sections(record_id)  on the hits.
   (These are MCP tools — call them by name, do NOT fetch URLs.)

{guide}

## Response style
- Lead with the answer in ≤3 sentences.
- Cite source: `(source: DOC-HE-CAE-2026-0000000001 §4)` after each factual claim.
- Quote section excerpts only when directly relevant.
- If RAG returned 0 results, say so explicitly and call the MCP tool recommend_agents(q).
"""
