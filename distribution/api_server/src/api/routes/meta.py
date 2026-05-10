"""``/api/meta`` — 클라이언트(특히 VSCode 확장) 가 폼 옵션을 일관되게 받기 위한
**권위 메타 카탈로그** 엔드포인트.

extension_integration_plan.md §2 / metadata_spec.md §6 에 정의된 필드 집합을
권위적으로 내려준다 — 클라이언트는 이 응답을 5분 캐시하고 셀렉트박스를 채운다.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.base import get_session
from ..db.models import Agent
from ..schemas.common import CLASSIFICATIONS, DERIVATIONS, STATUSES
from ..schemas.id_format import DATA_TYPES
from ..seed.teams import GROUPS, TEAMS
from ..services.converter_dispatch import EXTENSION_MAP

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meta", tags=["meta"])


# ---------------------------------------------------------------------------
# 정적 옵션 (확장 가능)
# ---------------------------------------------------------------------------
LANGUAGES: list[str] = ["ko", "en", "ja", "zh"]

ALLOW_CUSTOM: dict[str, bool] = {
    "team": False,
    "group": True,
    "domain": True,
}


# ---------------------------------------------------------------------------
# 라우트
# ---------------------------------------------------------------------------
@router.get(
    "/options",
    summary="클라이언트 메타 옵션 카탈로그 (VSCode 확장용)",
)
async def options(
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """폼 셀렉트박스/제약 옵션을 한 번에 내려준다.

    - 응답에 ``Cache-Control: public, max-age=300`` 헤더를 붙인다.
    - 인증 비요구. (``AUTH_REQUIRED=true`` 환경에서도 메타데이터는 개방.)
    """
    # 에이전트 목록 — DB SELECT 후 서빙 형태로 투영.
    rows = (
        await session.execute(select(Agent).order_by(Agent.agent_type))
    ).scalars().all()
    agents_payload = [
        {
            "agent_type": a.agent_type,
            "name": a.name,
            "description": a.description or "",
            "data_types": list(a.data_types or []),
        }
        for a in rows
    ]

    payload = {
        "version": "1.0",
        "teams": list(TEAMS),
        "groups": {k: list(v) for k, v in GROUPS.items()},
        "agents": agents_payload,
        "classifications": list(CLASSIFICATIONS),
        "statuses": list(STATUSES),
        "derivations": list(DERIVATIONS),
        "languages": list(LANGUAGES),
        "data_types": list(DATA_TYPES),
        "supported_extensions": sorted(EXTENSION_MAP.keys()),
        "max_upload_mb": settings.max_upload_mb,
        "allow_custom": dict(ALLOW_CUSTOM),
    }

    # 5분 캐시 (정적 옵션이므로 클라이언트에서 in-memory 캐시 가능).
    response.headers["Cache-Control"] = "public, max-age=300"
    return payload


__all__ = ["router"]
