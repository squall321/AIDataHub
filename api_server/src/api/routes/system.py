"""``/api/system`` — 시스템 진단/메타.

``/health`` 는 user-edited ``api/main.py`` 에 minimal 형태로 남기고, 풍부한 정보
(``version`` / ``auth_required``) 가 필요한 경우 본 라우터의
``/api/system/health`` 를 사용한다 — 확장 [Test Connection] 버튼이 인증 모드를
사전 판단하기 위함.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import __version__
from ..config import settings
from ..db.base import get_session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get(
    "/health",
    summary="확장 헬스체크 (version / auth_required / sync·embed 게이지)",
)
async def system_health(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """``/health`` 의 상위 호환. 클라이언트가 한 번에 인증 모드를 판단할 수 있게
    추가 메타를 내려준다.

    ``sync_stale_sources`` / ``embed_backlog`` 게이지 — watchdog/diag 가 이
    endpoint 하나로 '데이터가 신선하고 검색 가능한가' 를 판정한다. DB 조회
    실패 시에도 health 자체는 ok (게이지만 null).

    Response:
        ``{"status": "ok", "version": ..., "auth_required": ..., "build": ...,
           "sync_stale_sources": 0, "embed_backlog": 0}``
    """
    # build 식별자: 환경변수 ``BUILD_SHA`` 우선, 없으면 "dev".
    build = os.environ.get("BUILD_SHA") or "dev"
    embedder = os.environ.get("EMBEDDING_PROVIDER", "hash")
    out: dict = {
        "status": "ok",
        "version": __version__,
        "auth_required": bool(settings.auth_required),
        "build": build,
        "embedder": embedder,
        "sync_stale_sources": None,
        "embed_backlog": None,
    }
    try:
        from ..db.models import RecordSection, SyncSource
        from ..services.sync_svc import interval_minutes_from_cron as _interval_minutes_from_cron

        # 미임베딩 백로그 — skipped-empty 마킹분 (embedded_at 채워짐) 제외.
        backlog = await session.scalar(
            select(func.count())
            .select_from(RecordSection)
            .where(
                RecordSection.embedding.is_(None),
                RecordSection.embedded_at.is_(None),
            )
        )
        out["embed_backlog"] = int(backlog or 0)

        # 신선도 — enabled 소스 중 last_sync_at 이 주기의 4배 이상 지난 것.
        # 스케줄러 1~2회 실패는 무시하고 연속 정체만 잡는다.
        rows = (
            (await session.execute(select(SyncSource).where(SyncSource.enabled.is_(True))))
            .scalars()
            .all()
        )
        now = datetime.now(timezone.utc)
        stale = 0
        for src in rows:
            interval_min = _interval_minutes_from_cron(src.schedule_cron)
            if interval_min is None:
                continue  # 수동 전용 소스는 신선도 판정 제외
            last = src.last_sync_at
            if last is None:
                stale += 1
                continue
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (now - last).total_seconds() > interval_min * 60 * 4:
                stale += 1
        out["sync_stale_sources"] = stale
    except Exception as exc:  # noqa: BLE001 — 게이지는 best-effort
        log.debug("health gauges skipped: %s", exc)
    return out


__all__ = ["router"]
