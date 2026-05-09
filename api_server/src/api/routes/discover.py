"""``/api/discover`` / ``/api/schema`` / ``/api/hints`` / ``/api/docs/llm.txt``
/ ``POST /api/ask`` — Self-describing / RAG-friendly 엔드포인트.

본 라우터의 핵심 의도(Agent 30, B1-B6):
    LLM 에이전트가 백엔드 source 코드를 읽지 않고도 허브를 사용할 수 있게 한다.
    -> 모든 enum/필드/관계/ID 포맷을 API 자체에서 노출.
"""
from __future__ import annotations

import json as _json
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import get_session
from api.services import discover_svc

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /api/discover, /api/schema, /api/hints, /api/docs/llm.txt
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api", tags=["discover"])


@router.get("/discover", summary="허브 전체 카탈로그")
async def get_discover(
    no_cache: bool = Query(False, description="True 이면 캐시 무시하고 재계산"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """AI 에이전트가 가장 먼저 호출해야 할 시작점.

    응답에 ``schema_url`` / ``hints_url`` / ``llm_doc_url`` 이 들어 있어
    그 다음 단계로 자연스럽게 드릴다운할 수 있다.

    카운트 집계는 60초 in-memory 캐시 (``?no_cache=true`` 로 우회 가능).
    """
    return await discover_svc.build_discover_payload(
        session, use_cache=not no_cache
    )


@router.get("/schema", summary="머신 리더블 JSON Schema (draft-2020-12)")
async def get_schema() -> dict[str, Any]:
    """Record JSON Schema. enum/필드/oneOf 콘텐츠 변종을 모두 노출.

    ``draft-2020-12`` 사양. 정적 — DB 접근 없음.
    """
    return discover_svc.build_json_schema()


@router.get("/hints", summary="에이전트용 자연어 힌트")
async def get_hints(
    context: str | None = Query(
        None,
        description=(
            "토픽 (getting_started/searching/filtering_by_agent/tabular_data/"
            "time_bounded/attachments/cross_record_relations). "
            "생략하면 전체 힌트."
        ),
    ),
) -> dict[str, Any]:
    """힌트 카탈로그.

    각 항목: ``{hint, sample_endpoint, why_useful, context}``.
    """
    return {
        "context": context,
        "available_contexts": discover_svc.list_hint_contexts(),
        "hints": discover_svc.build_hints(context),
    }


@router.get(
    "/docs/llm.txt",
    summary="LLM 한 번에 읽을 통합 마크다운",
    response_class=PlainTextResponse,
)
async def get_llm_doc() -> str:
    """API 전체를 5-10KB 마크다운으로 압축 — LLM 컨텍스트 1회 주입용."""
    return discover_svc.build_llm_doc()


# ---------------------------------------------------------------------------
# /api/docs/agent-guide  — 모델 사이즈별 친화 가이드 (tiny/small/medium/large)
# ---------------------------------------------------------------------------
AgentGuideSize = Literal["tiny", "small", "medium", "large"]
AgentGuideFormat = Literal["markdown", "json"]


@router.get(
    "/docs/agent-guide",
    summary="모델 사이즈별 친화 가이드 (tiny/small/medium/large)",
    responses={
        200: {
            "description": "마크다운 본문 (또는 format=json 시 JSON 래퍼).",
            "content": {
                "text/markdown": {
                    "schema": {"type": "string"},
                    "example": "# AGENT_API_GUIDE_SMALL — ...",
                },
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "size": {"type": "string"},
                            "content": {"type": "string"},
                        },
                    }
                },
            },
        },
        422: {"description": "size 가 4종 외인 경우."},
        500: {"description": "가이드 파일을 찾을 수 없는 경우."},
    },
)
async def get_agent_guide(
    size: AgentGuideSize = Query(
        "small",
        description=(
            "모델 사이즈 — tiny(1B-3B) / small(3B-7B, default) / "
            "medium(13B-70B) / large(frontier)."
        ),
    ),
    format: AgentGuideFormat = Query(
        "markdown",
        description="응답 포맷. markdown(기본) 또는 json 래퍼.",
    ),
) -> Response:
    """모델 사이즈별로 다른 친화 가이드를 동적으로 서빙.

    파일 위치: ``api_server/docs/AGENT_API_GUIDE_{size.upper()}.md``.

    - ``size`` 가 4종(``tiny|small|medium|large``) 외면 → 422 (FastAPI Query
      validation).
    - 가이드 파일 부재 → 500 (운영 환경에서는 발생하지 말아야 함).
    - 기본 응답 Content-Type: ``text/markdown; charset=utf-8``.
    """
    try:
        body = discover_svc.load_agent_guide(size)
    except FileNotFoundError as exc:
        log.error("agent-guide file missing: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"agent-guide file missing for size={size!r}",
        ) from exc
    except ValueError as exc:
        # 이론상 Literal 검증을 통과하면 ValueError 가 안 나오지만 방어적.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if format == "json":
        return Response(
            content=_json.dumps(
                {"size": size, "content": body}, ensure_ascii=False
            ),
            media_type="application/json",
        )
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
    )


# ---------------------------------------------------------------------------
# POST /api/ask
# ---------------------------------------------------------------------------
class AskRequest(BaseModel):
    """``POST /api/ask`` 요청 바디."""

    query: str = Field(..., min_length=1, description="자연어 검색어 (한/영).")
    limit: int = Field(5, ge=1, le=50, description="최대 결과 수.")


@router.post("/ask", summary="자연어 쿼리 → interpreted_query + results")
async def post_ask(
    payload: AskRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """자연어 쿼리를 구조화된 필터로 해석하고 record 를 반환한다.

    동작:
        - ``OPENAI_API_KEY`` 가 환경에 있으면 LLM 으로 해석
          (``interpreted_query.source = "llm"``).
        - 없거나 실패하면 키워드 폴백 (``source = "keyword"``).
        - 빈 쿼리는 422 (Pydantic min_length).

    응답에는 항상 ``follow_up_queries`` 가 포함되어 다음 단계로 자연스럽게 이어진다.
    """
    log.info("ask: query=%s limit=%s", payload.query, payload.limit)
    return await discover_svc.execute_ask(
        session, query=payload.query, limit=payload.limit
    )


__all__ = ["router"]
