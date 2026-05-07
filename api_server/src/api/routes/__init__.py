"""라우터 패키지.

각 모듈은 `router = APIRouter(prefix=..., tags=...)` 를 노출한다.
`register_routers(app)` 로 메인 앱에 일괄 등록한다.

이 함수는 라우터 등록뿐 아니라 다음도 함께 수행한다 (main.py 비수정 정책):
    1. 구조화 로깅 설정 (configure_logging)
    2. 미들웨어 설치 (RequestLogging, Metrics)
    3. 통합 에러 핸들러 등록
    4. /metrics 라우터 (ENABLE_METRICS=true)
    5. /api/auth/keys 라우터
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ..config import settings
from ..errors import register_exception_handlers
from ..logging_config import configure_logging
from ..middleware.metrics import MetricsMiddleware
from ..middleware.request_logging import RequestLoggingMiddleware
from . import agents, analytics, auth, convert, data, metrics, records, search


def register_routers(app: FastAPI) -> None:
    """모든 API 라우터 / 미들웨어 / 핸들러를 FastAPI 앱에 등록.

    함께 ``/figures`` 정적 마운트도 수행한다 (그림 바이너리 서빙).
    """
    # ------------------------------------------------------------------ logging
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    # -------------------------------------------------------------- middleware
    # 미들웨어는 LIFO 로 적용된다 → request_logging 을 마지막에 추가하면 outer-most.
    if settings.enable_metrics:
        app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestLoggingMiddleware)

    # ----------------------------------------------------------- error handlers
    register_exception_handlers(app)

    # ------------------------------------------------------------------ routers
    app.include_router(records.router)
    app.include_router(data.router)
    app.include_router(search.router)
    app.include_router(agents.router)
    app.include_router(analytics.router)
    app.include_router(auth.router)
    app.include_router(convert.router)
    if settings.enable_metrics:
        app.include_router(metrics.router)

    # 그림 바이너리 서빙: /figures/{doc_id}/F001.png
    settings.figures_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/figures",
        StaticFiles(directory=str(settings.figures_dir)),
        name="figures",
    )


__all__ = [
    "agents",
    "analytics",
    "auth",
    "convert",
    "data",
    "metrics",
    "records",
    "register_routers",
    "search",
]
