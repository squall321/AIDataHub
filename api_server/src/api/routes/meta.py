"""``/api/meta`` — 클라이언트(특히 VSCode 확장) 가 폼 옵션을 일관되게 받기 위한
**권위 메타 카탈로그** 엔드포인트.

extension_integration_plan.md §2 / metadata_spec.md §6 에 정의된 필드 집합을
권위적으로 내려준다 — 클라이언트는 이 응답을 5분 캐시하고 셀렉트박스를 채운다.
"""
from __future__ import annotations

import hashlib
import json
import logging

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.base import get_session
from ..db.models import Agent, OrgGroup, OrgTeam
from ..schemas.common import CLASSIFICATIONS, DERIVATIONS, STATUSES
from ..schemas.id_format import DATA_TYPES
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
    response_model=None,
)
async def options(
    response: Response,
    session: AsyncSession = Depends(get_session),
    if_none_match: str | None = Header(None, alias="If-None-Match"),
) -> dict | Response:
    """폼 셀렉트박스/제약 옵션을 한 번에 내려준다.

    - ``Cache-Control: public, max-age=300`` + ``ETag`` 헤더.
    - 클라이언트가 ``If-None-Match`` 보내면 일치 시 304.
    - 인증 비요구 (``AUTH_REQUIRED=true`` 환경에서도 메타데이터는 개방).
    - team/group 은 ``org_teams`` / ``org_groups`` 마스터에서 활성 행만 조회
      (Migration 0012 이후). 응답 키 자체는 기존과 동일 — VSCode 확장 호환.
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

    # team/group — DB 조회 (활성만)
    team_rows = (
        await session.execute(
            select(OrgTeam).where(OrgTeam.is_active).order_by(OrgTeam.code)
        )
    ).scalars().all()
    group_rows = (
        await session.execute(
            select(OrgGroup)
            .where(OrgGroup.is_active)
            .order_by(OrgGroup.team_code, OrgGroup.code)
        )
    ).scalars().all()

    teams: list[str] = [t.code for t in team_rows]
    groups: dict[str, list[str]] = {}
    for g in group_rows:
        groups.setdefault(g.team_code, []).append(g.code)

    payload = {
        "version": "1.0",
        "teams": teams,
        "groups": groups,
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

    # ETag — payload 의 SHA256 앞 16자. 변경 시 자동으로 새 ETag 가 발급되어
    # 클라이언트(VSCode 확장)는 5분 캐시가 살아있어도 conditional GET 으로
    # 변경을 즉시 감지한다.
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'

    response.headers["Cache-Control"] = "public, max-age=300"
    response.headers["ETag"] = etag

    if if_none_match and if_none_match.strip() == etag:
        return Response(status_code=304, headers={"ETag": etag})

    return payload


__all__ = ["router"]
