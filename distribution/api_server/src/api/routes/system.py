"""``/api/system`` — 시스템 진단/메타.

``/health`` 는 user-edited ``api/main.py`` 에 minimal 형태로 남기고, 풍부한 정보
(``version`` / ``auth_required``) 가 필요한 경우 본 라우터의
``/api/system/health`` 를 사용한다 — 확장 [Test Connection] 버튼이 인증 모드를
사전 판단하기 위함.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter

from .. import __version__
from ..config import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get(
    "/health",
    summary="확장 헬스체크 (version / auth_required 노출)",
)
async def system_health() -> dict:
    """``/health`` 의 상위 호환. 클라이언트가 한 번에 인증 모드를 판단할 수 있게
    추가 메타를 내려준다.

    Response:
        ``{"status": "ok", "version": "0.1.0", "auth_required": false, "build": "..."}``
    """
    # build 식별자: 환경변수 ``BUILD_SHA`` 우선, 없으면 "dev".
    build = os.environ.get("BUILD_SHA") or "dev"
    return {
        "status": "ok",
        "version": __version__,
        "auth_required": bool(settings.auth_required),
        "build": build,
    }


__all__ = ["router"]
