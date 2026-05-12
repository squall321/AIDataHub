"""조직(team/group) 마스터 서비스.

``org_teams`` / ``org_groups`` 테이블에 대한 비즈니스 로직과 ingest 검증을 제공한다.
모든 함수는 async + ``AsyncSession`` 기반.

설계 노트:
    - team 은 Strict — 마스터에 없는 team 으로 record ingest 시도 시 422 발생.
    - group 은 lenient — 마스터 미존재 시 경고 로그만 출력 (records 적재 허용).
    - records 와의 FK 는 걸지 않는다. 검증은 본 서비스가 단독 책임.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models import OrgGroup, OrgTeam, Record

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ingest 검증 (Strict)
# ---------------------------------------------------------------------------
async def validate_team_group(
    session: AsyncSession, team: str, group: str
) -> None:
    """records ingest 시 team/group 마스터 존재를 검증한다.

    - ``settings.strict_team_validation = False`` 면 검증 자체를 건너뛴다.
    - team 미존재/비활성 → 422 Unprocessable Entity.
    - group 미존재 → 경고 로그만 (lenient).
    """
    if not settings.strict_team_validation:
        return

    t = await session.get(OrgTeam, team)
    if t is None or not t.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"team '{team}' is not registered or inactive",
        )

    g = await session.get(OrgGroup, (team, group))
    if g is None:
        # lenient — 차단하지 않고 로깅만
        log.info(
            "ingest: unknown group '%s/%s' — accepted (lenient policy)",
            team,
            group,
        )


# ---------------------------------------------------------------------------
# 조회 헬퍼 (라우터에서 사용)
# ---------------------------------------------------------------------------
async def team_record_count(session: AsyncSession, team_code: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count()).select_from(Record).where(Record.team == team_code)
            )
        ).scalar_one()
    )


async def group_record_count(
    session: AsyncSession, team_code: str, group_code: str
) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Record)
                .where(Record.team == team_code, Record.group == group_code)
            )
        ).scalar_one()
    )


async def team_group_count(session: AsyncSession, team_code: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(OrgGroup)
                .where(OrgGroup.team_code == team_code)
            )
        ).scalar_one()
    )
