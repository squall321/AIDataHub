"""``/api/org`` — 조직 마스터 (team/group) CRUD 라우터 (Migration 0012).

엔드포인트:
    - GET    /api/org/teams              : team 목록 (group/record 카운트 포함)
    - GET    /api/org/teams/{code}       : 단일 team
    - POST   /api/org/teams              : team 생성
    - PATCH  /api/org/teams/{code}       : team 수정 (code 변경 불가)
    - DELETE /api/org/teams/{code}       : team 삭제 (records/groups 참조 시 409)
    - GET    /api/org/groups             : group 목록 (?team=HE 필터)
    - GET    /api/org/groups/{team}/{code}
    - POST   /api/org/groups
    - PATCH  /api/org/groups/{team}/{code}
    - DELETE /api/org/groups/{team}/{code} (records 참조 시 409)

read-only 는 인증 면제 (메타데이터). 변경(POST/PATCH/DELETE) 은 ``AUTH_REQUIRED``
환경에서 ``require_api_key`` 적용.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import get_principal
from api.db.base import get_session
from api.db.models import OrgGroup, OrgTeam
from api.services import org_svc

from ._schemas import (
    OrgGroupIn,
    OrgGroupOut,
    OrgGroupPatch,
    OrgTeamIn,
    OrgTeamOut,
    OrgTeamPatch,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/org", tags=["org"])


# ===========================================================================
# Teams
# ===========================================================================
@router.get("/teams", response_model=list[OrgTeamOut], response_model_exclude_none=True)
async def list_teams(
    include_inactive: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> list[OrgTeamOut]:
    stmt = select(OrgTeam).order_by(OrgTeam.code)
    if not include_inactive:
        stmt = stmt.where(OrgTeam.is_active)
    rows = (await session.execute(stmt)).scalars().all()
    out: list[OrgTeamOut] = []
    for t in rows:
        out.append(
            OrgTeamOut(
                code=t.code,
                name=t.name,
                description=t.description,
                is_active=t.is_active,
                group_count=await org_svc.team_group_count(session, t.code),
                record_count=await org_svc.team_record_count(session, t.code),
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
        )
    return out


@router.get("/teams/{code}", response_model=OrgTeamOut, response_model_exclude_none=True)
async def get_team(
    code: str, session: AsyncSession = Depends(get_session)
) -> OrgTeamOut:
    t = await session.get(OrgTeam, code)
    if t is None:
        raise HTTPException(status_code=404, detail=f"team '{code}' not found")
    return OrgTeamOut(
        code=t.code,
        name=t.name,
        description=t.description,
        is_active=t.is_active,
        group_count=await org_svc.team_group_count(session, t.code),
        record_count=await org_svc.team_record_count(session, t.code),
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.post(
    "/teams",
    response_model=OrgTeamOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_principal)],
)
async def create_team(
    payload: OrgTeamIn, session: AsyncSession = Depends(get_session)
) -> OrgTeamOut:
    t = OrgTeam(**payload.model_dump())
    session.add(t)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail=f"team '{payload.code}' already exists"
        ) from exc
    await session.refresh(t)
    return OrgTeamOut(
        code=t.code,
        name=t.name,
        description=t.description,
        is_active=t.is_active,
        group_count=0,
        record_count=0,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.patch(
    "/teams/{code}",
    response_model=OrgTeamOut,
    response_model_exclude_none=True,
    dependencies=[Depends(get_principal)],
)
async def update_team(
    code: str,
    payload: OrgTeamPatch,
    session: AsyncSession = Depends(get_session),
) -> OrgTeamOut:
    t = await session.get(OrgTeam, code)
    if t is None:
        raise HTTPException(status_code=404, detail=f"team '{code}' not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(t, k, v)
    await session.commit()
    await session.refresh(t)
    return OrgTeamOut(
        code=t.code,
        name=t.name,
        description=t.description,
        is_active=t.is_active,
        group_count=await org_svc.team_group_count(session, t.code),
        record_count=await org_svc.team_record_count(session, t.code),
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.delete(
    "/teams/{code}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_principal)],
)
async def delete_team(
    code: str, session: AsyncSession = Depends(get_session)
) -> None:
    t = await session.get(OrgTeam, code)
    if t is None:
        raise HTTPException(status_code=404, detail=f"team '{code}' not found")
    # 참조 검사 — records 또는 org_groups
    rec_count = await org_svc.team_record_count(session, code)
    if rec_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"team '{code}' has {rec_count} records — delete blocked",
        )
    grp_count = await org_svc.team_group_count(session, code)
    if grp_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"team '{code}' has {grp_count} groups — delete groups first",
        )
    await session.delete(t)
    await session.commit()


# ===========================================================================
# Groups
# ===========================================================================
@router.get(
    "/groups",
    response_model=list[OrgGroupOut],
    response_model_exclude_none=True,
)
async def list_groups(
    team: str | None = Query(None),
    include_inactive: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> list[OrgGroupOut]:
    stmt = select(OrgGroup).order_by(OrgGroup.team_code, OrgGroup.code)
    if team:
        stmt = stmt.where(OrgGroup.team_code == team)
    if not include_inactive:
        stmt = stmt.where(OrgGroup.is_active)
    rows = (await session.execute(stmt)).scalars().all()
    out: list[OrgGroupOut] = []
    for g in rows:
        out.append(
            OrgGroupOut(
                team_code=g.team_code,
                code=g.code,
                name=g.name,
                description=g.description,
                is_active=g.is_active,
                record_count=await org_svc.group_record_count(
                    session, g.team_code, g.code
                ),
                created_at=g.created_at,
                updated_at=g.updated_at,
            )
        )
    return out


@router.get(
    "/groups/{team}/{code}",
    response_model=OrgGroupOut,
    response_model_exclude_none=True,
)
async def get_group(
    team: str, code: str, session: AsyncSession = Depends(get_session)
) -> OrgGroupOut:
    g = await session.get(OrgGroup, (team, code))
    if g is None:
        raise HTTPException(
            status_code=404, detail=f"group '{team}/{code}' not found"
        )
    return OrgGroupOut(
        team_code=g.team_code,
        code=g.code,
        name=g.name,
        description=g.description,
        is_active=g.is_active,
        record_count=await org_svc.group_record_count(session, team, code),
        created_at=g.created_at,
        updated_at=g.updated_at,
    )


@router.post(
    "/groups",
    response_model=OrgGroupOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_principal)],
)
async def create_group(
    payload: OrgGroupIn, session: AsyncSession = Depends(get_session)
) -> OrgGroupOut:
    # team 존재 검증
    t = await session.get(OrgTeam, payload.team_code)
    if t is None:
        raise HTTPException(
            status_code=422,
            detail=f"team '{payload.team_code}' not found — create team first",
        )
    g = OrgGroup(**payload.model_dump())
    session.add(g)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"group '{payload.team_code}/{payload.code}' already exists",
        ) from exc
    await session.refresh(g)
    return OrgGroupOut(
        team_code=g.team_code,
        code=g.code,
        name=g.name,
        description=g.description,
        is_active=g.is_active,
        record_count=0,
        created_at=g.created_at,
        updated_at=g.updated_at,
    )


@router.patch(
    "/groups/{team}/{code}",
    response_model=OrgGroupOut,
    response_model_exclude_none=True,
    dependencies=[Depends(get_principal)],
)
async def update_group(
    team: str,
    code: str,
    payload: OrgGroupPatch,
    session: AsyncSession = Depends(get_session),
) -> OrgGroupOut:
    g = await session.get(OrgGroup, (team, code))
    if g is None:
        raise HTTPException(
            status_code=404, detail=f"group '{team}/{code}' not found"
        )
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(g, k, v)
    await session.commit()
    await session.refresh(g)
    return OrgGroupOut(
        team_code=g.team_code,
        code=g.code,
        name=g.name,
        description=g.description,
        is_active=g.is_active,
        record_count=await org_svc.group_record_count(session, team, code),
        created_at=g.created_at,
        updated_at=g.updated_at,
    )


@router.delete(
    "/groups/{team}/{code}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_principal)],
)
async def delete_group(
    team: str, code: str, session: AsyncSession = Depends(get_session)
) -> None:
    g = await session.get(OrgGroup, (team, code))
    if g is None:
        raise HTTPException(
            status_code=404, detail=f"group '{team}/{code}' not found"
        )
    rec_count = await org_svc.group_record_count(session, team, code)
    if rec_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"group '{team}/{code}' has {rec_count} records — delete blocked",
        )
    await session.delete(g)
    await session.commit()
