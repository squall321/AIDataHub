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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시: DB 연결 확인 (선택)
    yield
    # 종료 시: 엔진 dispose
    await engine.dispose()


app = FastAPI(
    title="사업부 문서 AI 데이터 API",
    description="Word → JSON → PostgreSQL 적재 데이터를 Cline SR 등 에이전트에 제공",
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


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {
        "service": "ai-data-api",
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
