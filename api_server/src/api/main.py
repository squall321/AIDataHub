"""FastAPI 애플리케이션 엔트리포인트.

라우터 등록은 `api.routes.register_routers(app)` 가 담당한다.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import settings
from .database import engine
from .routes import register_routers


try:
    from .mcp_runtime import app as _mcp_app
    _MCP_AVAILABLE = True
except Exception:  # pragma: no cover
    _mcp_app = None
    _MCP_AVAILABLE = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    # v0.13.0 — EMBEDDING_DIM 환경변수와 실제 vector 컬럼 차원 일치 검증.
    # 불일치 시 sample_embedding sync 가 런타임에 실패하므로 부팅 단계에서 경고.
    await _check_embedding_dim_consistency()

    # MCP streamable_http_app 은 자체 lifespan(task group)이 있어야 동작한다.
    # FastAPI 의 mount() 는 sub-app 의 lifespan 을 자동 전파하지 않으므로,
    # 우리 lifespan 에서 명시적으로 enter / exit 한다.
    if _MCP_AVAILABLE and _mcp_app is not None:
        async with _mcp_app.router.lifespan_context(_mcp_app):
            yield
    else:
        yield
    await engine.dispose()


async def _check_embedding_dim_consistency() -> None:
    """``EMBEDDING_DIM`` 과 record_sections / agent_sample_embeddings 의 실제
    vector 컬럼 차원이 일치하는지 검증. 불일치 시 로그 경고 (부팅은 계속).

    PG 가 아닌 환경(SQLite test) 에서는 vector 가 JSON 으로 폴백되므로 skip.
    """
    import logging
    import os as _os

    log = logging.getLogger("api.main")
    expected = int(_os.environ.get("EMBEDDING_DIM", "384"))
    try:
        async with engine.connect() as conn:
            # PG only — SQLite 는 column type 이 JSON.
            dialect = conn.dialect.name if conn.dialect else ""
            if dialect != "postgresql":
                return
            from sqlalchemy import text as _text

            stmt = _text(
                "SELECT table_name, atttypmod "
                "FROM pg_attribute "
                "JOIN pg_class ON pg_class.oid = pg_attribute.attrelid "
                "JOIN information_schema.columns "
                "  ON columns.table_name = pg_class.relname "
                "  AND columns.column_name = pg_attribute.attname "
                "WHERE pg_class.relname IN ('record_sections','agent_sample_embeddings') "
                "  AND pg_attribute.attname = 'embedding'"
            )
            rows = (await conn.execute(stmt)).all()
            mismatches: list[str] = []
            for row in rows:
                # pgvector 의 atttypmod 는 (dim) 그대로 저장됨.
                actual = int(row.atttypmod) if row.atttypmod and row.atttypmod > 0 else None
                if actual is not None and actual != expected:
                    mismatches.append(
                        f"{row.table_name}.embedding=vector({actual}) but EMBEDDING_DIM={expected}"
                    )
            if mismatches:
                log.warning(
                    "EMBEDDING_DIM mismatch — embedding writes will fail at runtime: %s",
                    "; ".join(mismatches),
                )
            else:
                log.info("EMBEDDING_DIM consistency check OK (dim=%s)", expected)
    except Exception as exc:  # pragma: no cover — best-effort check
        log.info("EMBEDDING_DIM consistency check skipped (%s)", exc)


app = FastAPI(
    title="Mobile eXperience AI Data Hub",
    description="문서/시뮬레이션/데이터 → JSON → PostgreSQL+pgvector 적재 데이터를 AI 에이전트에 제공",
    version=__version__,
    lifespan=lifespan,
)

# 개발 단계에서는 CORS 전체 허용. 운영 배포 전에 origin 화이트리스트로 좁힌다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 도메인 라우터 등록
register_routers(app)

# ---------------------------------------------------------------------------
# /dashboard — 정적 SPA 마운트 (단일 페이지 vanilla JS 대시보드).
# 위치: ``api_server/static/dashboard/`` (api_server/src/api/main.py 기준
# parent.parent.parent). 디렉토리가 없으면 마운트를 건너뛰어 기존 동작 보전.
# ---------------------------------------------------------------------------
DASHBOARD_DIR = (
    Path(__file__).resolve().parent.parent.parent / "static" / "dashboard"
)
if DASHBOARD_DIR.exists():
    app.mount(
        "/dashboard",
        StaticFiles(directory=str(DASHBOARD_DIR), html=True),
        name="dashboard",
    )

# ---------------------------------------------------------------------------
# /downloads — VSCode extension .vsix 및 배포 파일 다운로드.
# setup.sh 가 빌드 후 api_server/static/downloads/ 에 복사한다.
# ---------------------------------------------------------------------------
DOWNLOADS_DIR = (
    Path(__file__).resolve().parent.parent.parent / "static" / "downloads"
)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/downloads",
    StaticFiles(directory=str(DOWNLOADS_DIR)),
    name="downloads",
)

# ---------------------------------------------------------------------------
# /mcp — MCP (Model Context Protocol) Streamable HTTP server (mcp-http-server).
# Cline / Claude Desktop / Claude Code 등 MCP 클라이언트가 우리 도구를 자동 발견.
# lifespan 은 위의 통합 lifespan() 에서 처리한다.
# ---------------------------------------------------------------------------
if _MCP_AVAILABLE and _mcp_app is not None:
    # MCP 호출(JSON-RPC over streamable HTTP) 의 tool name + latency 를 JSONL 로
    # 기록하는 ASGI 미들웨어. env AIDH_MCP_LOG=0 으로 비활성.
    from .middleware.mcp_logging import MCPLoggingASGI
    _mcp_logged = MCPLoggingASGI(_mcp_app)
    app.mount("/mcp", _mcp_logged, name="mcp")


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {
        "service": "mobile-experience-ai-data-hub",
        "version": __version__,
        "status": "running",
    }


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


def run() -> None:
    """`python -m api.main` 실행용."""
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
    )


if __name__ == "__main__":
    run()
