"""``/api/agents`` — 에이전트 메타데이터 CRUD + context-bundle + system-prompt."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import get_session
from api.services import agent_svc, preview_svc, recommend_svc, sample_embedding_svc

from ._schemas import (
    AgentHistoryOut,
    AgentHistoryPruneOut,
    AgentIn,
    AgentOut,
    AgentPatch,
    AgentPreviewIn,
    AgentPreviewOut,
    AgentSamplesResyncAllOut,
    AgentSamplesResyncOut,
    RecordOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get(
    "",
    response_model=list[AgentOut],
    response_model_exclude_none=True,
)
@router.get(
    "/",
    response_model=list[AgentOut],
    response_model_exclude_none=True,
    include_in_schema=False,
)
async def list_agents(
    session: AsyncSession = Depends(get_session),
) -> list[AgentOut]:
    rows = await agent_svc.list_agents(session)
    types = [r.agent_type for r in rows]
    counts = await agent_svc.fetch_samples_indexed_counts(session, types) if types else {}
    out: list[AgentOut] = []
    for r in rows:
        m = AgentOut.model_validate(r)
        m.samples_indexed_count = int(counts.get(r.agent_type, 0))
        m.samples_stale = bool(len(list(r.sample_queries or [])) != m.samples_indexed_count)
        out.append(m)
    return out


@router.get(
    "/{agent_type}", response_model=AgentOut, response_model_exclude_none=True
)
async def get_agent(
    agent_type: str,
    session: AsyncSession = Depends(get_session),
) -> AgentOut:
    agent = await agent_svc.get_agent(session, agent_type)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_type}")
    counts = await agent_svc.fetch_samples_indexed_counts(session, [agent_type])
    out = AgentOut.model_validate(agent)
    out.samples_indexed_count = int(counts.get(agent_type, 0))
    out.samples_stale = bool(len(list(agent.sample_queries or [])) != out.samples_indexed_count)
    return out


@router.get(
    "/{agent_type}/records",
    response_model=list[RecordOut],
    response_model_exclude_none=True,
)
async def get_agent_records(
    agent_type: str,
    session: AsyncSession = Depends(get_session),
) -> list[RecordOut]:
    agent = await agent_svc.get_agent(session, agent_type)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_type}")
    rows = await agent_svc.records_for_agent(session, agent_type)
    return [RecordOut.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=AgentOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/",
    response_model=AgentOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def create_agent(
    payload: AgentIn,
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
) -> AgentOut:
    try:
        agent = await agent_svc.create_agent(
            session, payload.model_dump(), changed_by=x_user_id
        )
    except ValueError as exc:
        log.info("create_agent conflict: %s", payload.agent_type)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AgentOut.model_validate(agent)


@router.patch(
    "/{agent_type}", response_model=AgentOut, response_model_exclude_none=True
)
async def patch_agent(
    agent_type: str,
    patch: AgentPatch,
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
) -> AgentOut:
    agent = await agent_svc.update_agent(
        session,
        agent_type,
        patch.model_dump(exclude_unset=True),
        changed_by=x_user_id,
    )
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_type}")
    return AgentOut.model_validate(agent)


@router.delete("/{agent_type}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_type: str,
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
) -> None:
    ok = await agent_svc.delete_agent(session, agent_type, changed_by=x_user_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_type}")


# ---------------------------------------------------------------------------
# History (Migration 0015) — append-only audit log of agent CRUD
# ---------------------------------------------------------------------------
@router.get(
    "/{agent_type}/history",
    response_model=list[AgentHistoryOut],
    response_model_exclude_none=True,
    summary="agent 변경 이력 (최신순). 삭제된 agent 도 이력은 조회 가능.",
)
async def get_agent_history(
    agent_type: str,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[AgentHistoryOut]:
    rows = await agent_svc.list_agent_history(session, agent_type, limit=limit)
    return [AgentHistoryOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Preview (Migration 0014) — 저장 전 RAG 레시피 미리보기
# ---------------------------------------------------------------------------
@router.post(
    "/preview",
    response_model=AgentPreviewOut,
    response_model_exclude_none=True,
    summary="저장 전 RAG 레시피로 검색 + (가능하면) LLM 답변까지 미리보기",
)
async def preview_agent_recipe(
    payload: AgentPreviewIn,
    session: AsyncSession = Depends(get_session),
) -> AgentPreviewOut:
    result = await preview_svc.preview_recipe(
        session,
        query=payload.query,
        agent_type=payload.agent_type,
        retrieval_config=payload.retrieval_config or {},
        system_prompt=payload.system_prompt,
        response_config=payload.response_config or {},
    )
    return AgentPreviewOut.model_validate(result)


# ---------------------------------------------------------------------------
# Sample embeddings resync (Migration 0016) — recompute embeddings for an
# agent's current sample_queries. UI 가 'Resync samples' 버튼으로 호출.
# ---------------------------------------------------------------------------
@router.post(
    "/{agent_type}/resync-samples",
    response_model=AgentSamplesResyncOut,
    response_model_exclude_none=True,
    summary="agent.sample_queries 의 임베딩을 재계산해 routing 인덱스 갱신",
)
async def resync_agent_samples(
    agent_type: str,
    session: AsyncSession = Depends(get_session),
) -> AgentSamplesResyncOut:
    agent = await agent_svc.get_agent(session, agent_type)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_type}")
    samples = list(agent.sample_queries or [])
    summary = await sample_embedding_svc.sync_agent_samples(
        session, agent_type=agent_type, sample_queries=samples
    )
    return AgentSamplesResyncOut(
        agent_type=agent_type,
        indexed_count=int(summary.get("count", 0)),
        sample_queries=samples,
    )


@router.post(
    "/resync-samples-all",
    response_model=AgentSamplesResyncAllOut,
    response_model_exclude_none=True,
    summary="모든 agent 의 sample_queries 임베딩 일괄 재계산 (EMBEDDING_DIM 변경 후 백필)",
)
async def resync_all_agent_samples(
    session: AsyncSession = Depends(get_session),
) -> AgentSamplesResyncAllOut:
    summary = await agent_svc.resync_all_agent_samples(session)
    return AgentSamplesResyncAllOut.model_validate(summary)


@router.post(
    "/history/prune",
    response_model=AgentHistoryPruneOut,
    response_model_exclude_none=True,
    summary="agents_history 누적 청소 (per-agent keep_last 또는 절대 age)",
)
async def prune_agent_history(
    keep_last: int = Query(50, ge=0, le=10000),
    older_than_days: int | None = Query(None, ge=1, le=3650),
    session: AsyncSession = Depends(get_session),
) -> AgentHistoryPruneOut:
    summary = await agent_svc.prune_agent_history(
        session, keep_last=keep_last, older_than_days=older_than_days
    )
    return AgentHistoryPruneOut.model_validate(summary)


@router.get(
    "/{agent_type}/template",
    summary="agent expected-schema 기반 Word 템플릿 생성·다운로드",
)
async def download_agent_template(
    agent_type: str,
    session: AsyncSession = Depends(get_session),
):
    """agent 의 expected schema 를 반영한 ``.docx`` 즉석 생성.

    Response: ``application/vnd.openxmlformats-officedocument.wordprocessingml.document``
    바이너리. ``Content-Disposition: attachment; filename=agent_<type>_template.docx``.
    """
    from fastapi.responses import Response

    from api.services import doc_type_svc, template_svc

    agent = await agent_svc.get_agent(session, agent_type)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_type}")

    # doc_type 보조 정보 (없으면 None 으로 두고 generic 섹션 사용)
    doc_type_name: str | None = None
    doc_type_desc = ""
    expected_sections: list[str] | None = None
    if agent.required_doc_type:
        dt = await doc_type_svc.get_doc_type(session, agent.required_doc_type)
        if dt is not None:
            doc_type_name = dt.name
            doc_type_desc = dt.description or ""
            expected_sections = list(dt.expected_sections or [])

    docx_bytes = template_svc.generate_agent_template(
        agent_type=agent.agent_type,
        agent_name=agent.name,
        agent_description=agent.description or "",
        required_doc_type=agent.required_doc_type,
        required_tags=list(agent.required_tags or []),
        excluded_tags=list(agent.excluded_tags or []),
        doc_type_name=doc_type_name,
        doc_type_description=doc_type_desc,
        expected_sections=expected_sections,
    )
    filename = template_svc.template_filename(agent.agent_type)
    return Response(
        content=docx_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ---------------------------------------------------------------------------
# Agent context bundle (LLM-ready) + system prompt (Cline/Qwen 붙여넣기용)
# Plan: agent-discovery-console
# ---------------------------------------------------------------------------
@router.get(
    "/{agent_type}/context-bundle",
    summary="agent 의 records + 핵심 sections 를 LLM 친화 묶음으로 반환",
)
async def get_context_bundle(
    agent_type: str,
    max_records: int = Query(10, ge=1, le=50),
    max_sections_per_record: int = Query(8, ge=1, le=40),
    accept: str = Header("text/markdown", alias="Accept"),
    session: AsyncSession = Depends(get_session),
):
    """Accept 헤더로 분기:
    - ``text/markdown`` (default): LLM 친화 markdown
    - ``application/json``: 구조화 JSON
    """
    bundle = await recommend_svc.build_context_bundle(
        session,
        agent_type=agent_type,
        max_records=max_records,
        max_sections_per_record=max_sections_per_record,
    )
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_type}")

    if "application/json" in (accept or "").lower():
        return bundle

    md = recommend_svc.render_context_bundle_markdown(bundle)
    return PlainTextResponse(content=md, media_type="text/markdown; charset=utf-8")


@router.get(
    "/{agent_type}/system-prompt",
    summary="Cline / Qwen 등에 그대로 붙여넣을 system prompt",
    response_class=PlainTextResponse,
)
async def get_system_prompt(
    agent_type: str,
    base_url: str = Query(
        "http://<host>:8001",
        description="이 시스템에 닿을 수 있는 base URL. 예: http://110.15.177.120:8001",
    ),
    session: AsyncSession = Depends(get_session),
) -> str:
    agent = await agent_svc.get_agent(session, agent_type)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_type}")
    return recommend_svc.build_system_prompt(agent, base_url=base_url)


__all__ = ["router"]
