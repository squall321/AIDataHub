"""``/api/groups`` + ``/api/records/{id}/cluster`` + ``/api/records/bulk`` —
의미 그룹 (Semantic Groups) 라우터.

같은 의미의 record 군을 자동으로 묶어서 작은 AI 가 한 번에 가져갈 수
있게 한다. ``record_sections.embedding`` (vector(384)) + 태그 + 메타를
활용해 클러스터링한다.

엔드포인트:
    - ``POST /api/groups/auto``                : 자연어 질의 → 자동 그룹화
    - ``GET  /api/records/{id}/cluster``       : 한 record 의 의미 그룹
    - ``POST /api/records/bulk``               : 여러 ID 한 번에 조회

핵심 비즈니스 로직은 :mod:`api.services.cluster_svc`.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import get_session
from api.services.cluster_svc import (
    build_query_groups,
    cluster_around_record,
    fetch_records_bulk,
)

log = logging.getLogger(__name__)

# /api/groups 와 /api/records 두 경로를 모두 다루므로 prefix 는 공유 prefix
# (``/api``) 로 두고 각 핸들러에서 풀 경로를 명시한다.
router = APIRouter(prefix="/api", tags=["groups"])


# ---------------------------------------------------------------------------
# POST /api/groups/auto
# ---------------------------------------------------------------------------
class AutoGroupsRequest(BaseModel):
    """``POST /api/groups/auto`` 요청 바디."""

    q: str = Field(..., min_length=1, description="자연어 질의 (한/영).")
    n_groups: int = Field(3, ge=1, le=20, description="목표 그룹 개수.")
    limit_per_group: int = Field(
        5, ge=1, le=20, description="그룹당 노출 record 최대 수."
    )
    min_score: float = Field(
        0.4,
        ge=0.0,
        le=1.0,
        description="시맨틱 검색 score 컷오프 (0..1).",
    )
    sim_threshold: float = Field(
        0.85,
        ge=0.0,
        le=1.0,
        description="같은 그룹으로 묶을 코사인 임계.",
    )
    top_k: int = Field(
        50, ge=1, le=200, description="시맨틱 검색 후보 풀 크기."
    )


@router.post(
    "/groups/auto",
    summary="자연어 질의 → 자동 클러스터링된 의미 그룹",
)
async def post_groups_auto(
    payload: AutoGroupsRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """자연어 ``q`` 를 시맨틱 검색 후 그리디 클러스터링하여 그룹별 결과 반환.

    응답 형태:

    ```json
    {
      "query": "AI 도입 현황",
      "total_records": 23,
      "n_groups_requested": 3,
      "groups": [
        {
          "label": "AI 도입 현황 — AI·DigitalTwin",
          "common_tags": ["AI", "DigitalTwin"],
          "common_agents": [],
          "common_domain": "strategy",
          "size": 7,
          "representative_record": {"id": "...", "title": "...", "score": 0.92},
          "records": [{"id": "...", "title": "...", "score": 0.92, ...}, ...]
        },
        ...
      ]
    }
    ```

    임베딩이 비어 있는 환경에서는 ``total_records=0`` 빈 그룹으로 응답.
    """
    log.info(
        "groups.auto: q=%s n=%s limit=%s min=%.2f",
        payload.q, payload.n_groups, payload.limit_per_group, payload.min_score,
    )
    try:
        return await build_query_groups(
            session,
            query=payload.q,
            n_groups=payload.n_groups,
            limit_per_group=payload.limit_per_group,
            min_score=payload.min_score,
            top_k=payload.top_k,
            sim_threshold=payload.sim_threshold,
        )
    except RuntimeError as exc:
        log.warning("groups.auto embedder error: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"semantic groups unavailable: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# GET /api/records/{id}/cluster
# ---------------------------------------------------------------------------
ClusterMode = Literal["semantic", "tag", "hybrid"]


@router.get(
    "/records/{record_id}/cluster",
    summary="이 record 의 의미 그룹 (semantic / tag / hybrid)",
)
async def get_record_cluster(
    record_id: str,
    mode: ClusterMode = Query(
        "hybrid", description="semantic | tag | hybrid"
    ),
    sim_threshold: float = Query(
        0.85, ge=0.0, le=1.0, description="semantic / hybrid 컷오프."
    ),
    tag_threshold: float = Query(
        0.6, ge=0.0, le=1.0, description="tag jaccard 컷오프."
    ),
    limit: int = Query(20, ge=1, le=100, description="최대 반환 record 수."),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """anchor record 와 같은 의미 그룹의 모든 record.

    응답 형태:

    ```json
    {
      "anchor_record": {"id": "...", "title": "...", "data_type": "DOC", "tags": [...]},
      "mode": "semantic",
      "cluster_size": 8,
      "items": [
        {"id": "...", "title": "...", "score": 0.91,
         "shared_tags": ["IGA", "NURBS"],
         "tag_jaccard": 0.5, "semantic_sim": 0.93}
      ]
    }
    ```
    """
    try:
        return await cluster_around_record(
            session,
            anchor_id=record_id,
            mode=mode,
            sim_threshold=sim_threshold,
            tag_threshold=tag_threshold,
            limit=limit,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"record not found: {record_id}",
        ) from exc
    except ValueError as exc:
        # mode literal 검증은 FastAPI 단계에서 끝나지만 방어적.
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# POST /api/records/bulk
# ---------------------------------------------------------------------------
class BulkRecordsRequest(BaseModel):
    """``POST /api/records/bulk`` 요청 바디."""

    ids: list[str] = Field(
        ..., min_length=1, max_length=200, description="조회할 record id 목록."
    )
    include_sections: bool = Field(
        False,
        description="True 면 ``record_sections`` 도 동봉 (DOC variant 위주).",
    )


@router.post(
    "/records/bulk",
    summary="여러 record id 한 번에 조회 (그룹 fetch N+1 회피)",
)
async def post_records_bulk(
    payload: BulkRecordsRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """여러 record id 를 한 번에 가져온다.

    응답 형태:

    ```json
    {
      "items": [{"id": "...", "title": "...", "tags": [...], "sections": [...]}],
      "missing": ["DOC-..."]
    }
    ```

    ``ids`` 가 빈 리스트면 422 (Pydantic min_length).
    """
    log.info(
        "records.bulk: count=%s include_sections=%s",
        len(payload.ids), payload.include_sections,
    )
    return await fetch_records_bulk(
        session,
        ids=payload.ids,
        include_sections=payload.include_sections,
    )


__all__ = ["router"]
