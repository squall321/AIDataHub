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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..config import settings
from ..errors import register_exception_handlers
from ..logging_config import configure_logging
from ..middleware.metrics import MetricsMiddleware
from ..middleware.request_logging import RequestLoggingMiddleware
from . import (
    agents,
    analytics,
    attachments,
    auth,
    bundle,
    chat,
    convert,
    data,
    discover,
    doc_types,
    groups,
    jobs,
    meta,
    metrics,
    org,
    portal_sso,
    recommend,
    records,
    search,
    sync,
    system,
    taxonomy,
)

# Wave-5 P1 / Wave-6 P1 — 신규 라우터. 모듈이 아직 없으면 silent skip.
try:
    from . import mcp_tools as _mcp_tools  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    _mcp_tools = None  # type: ignore[assignment]
try:
    from . import mcp_upstreams as _mcp_upstreams  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    _mcp_upstreams = None  # type: ignore[assignment]


def register_routers(app: FastAPI) -> None:
    """모든 API 라우터 / 미들웨어 / 핸들러를 FastAPI 앱에 등록.

    함께 ``/figures`` 정적 마운트도 수행한다 (그림 바이너리 서빙).
    """
    # ------------------------------------------------------------------ logging
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    # -------------------------------------------------------------- middleware
    # CORS — vscode-webview://* + EXTRA_ALLOWED_ORIGINS 추가 허용.
    # 주의: ``api/main.py`` 가 이미 ``allow_origins=["*"]`` 로 등록한 미들웨어가 있으나,
    # 여기서 정규식 기반 미들웨어를 한 번 더 추가해 webview 사전요청 헤더를 보장한다.
    extra_origins = list(settings.extra_allowed_origins or [])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=extra_origins,
        allow_origin_regex=r"^vscode-webview://.*$",
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["X-API-Key", "Content-Type", "Accept", "Authorization"],
        expose_headers=["X-Request-ID"],
    )

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
    # /api/doc-types/* — doc_type taxonomy (Migration 0011)
    app.include_router(doc_types.router)
    app.include_router(analytics.router)
    app.include_router(attachments.router)
    app.include_router(auth.router)
    # /api/auth/portal-callback — HWAX 포털 SSO 핸드오프 (PORTAL_JWKS_URL 없으면 404)
    app.include_router(portal_sso.router)
    app.include_router(bundle.router)
    app.include_router(convert.router)
    app.include_router(jobs.router)
    app.include_router(meta.router)
    # /api/org/teams, /api/org/groups — 조직 마스터 CRUD (Migration 0012)
    app.include_router(org.router)
    # /api/recommend/agents — 자연어 → 추천 agents (agent-discovery-console)
    app.include_router(recommend.router)
    app.include_router(system.router)
    # /api/taxonomy/* — 작은 모델용 어휘 발견 / 동의어 매핑
    app.include_router(taxonomy.router)
    # /api/groups/auto, /api/records/{id}/cluster, /api/records/bulk —
    # 의미 그룹 (Semantic Groups) 라우터 — 같은 의미의 record 군을 묶어
    # 작은 AI 가 한 번에 가져갈 수 있게 한다.
    app.include_router(groups.router)
    # /api/sync/* — 외부 데이터 소스 정기 pull 동기화 (alembic 0027)
    app.include_router(sync.router)
    # /api/discover, /api/schema, /api/hints, /api/docs/llm.txt, /api/ask
    app.include_router(discover.router)
    # /api/chat — 자체 챗 (vLLM + 로컬 도구 tool-calling) SSE. 메인 페이지 챗.
    app.include_router(chat.router)
    if settings.enable_metrics:
        app.include_router(metrics.router)
    # /api/metrics/mcp — JSONL tail 분석 (Prometheus 와 무관) — 항상 활성.
    app.include_router(metrics.mcp_router)
    # Wave-5 P1 — /api/mcp_tools/* (도구 업로드)
    if _mcp_tools is not None and hasattr(_mcp_tools, "router"):
        app.include_router(_mcp_tools.router)
    # Wave-6 P1 — /api/mcp/upstreams/* (federation 관리)
    if _mcp_upstreams is not None and hasattr(_mcp_upstreams, "router"):
        app.include_router(_mcp_upstreams.router)

    # 그림 바이너리 서빙: /figures/{doc_id}/F001.png
    settings.figures_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/figures",
        StaticFiles(directory=str(settings.figures_dir)),
        name="figures",
    )

    # 첨부 바이너리 서빙: /attachments/{doc_id}/A001.{ext}
    settings.attachments_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/attachments",
        StaticFiles(directory=str(settings.attachments_dir)),
        name="attachments",
    )


__all__ = [
    "agents",
    "analytics",
    "attachments",
    "auth",
    "bundle",
    "convert",
    "data",
    "discover",
    "doc_types",
    "groups",
    "jobs",
    "meta",
    "metrics",
    "portal_sso",
    "records",
    "register_routers",
    "search",
    "system",
    "taxonomy",
]
