"""FastAPI 애플리케이션 엔트리포인트.

라우터 등록은 `api.routes.register_routers(app)` 가 담당한다.
"""
import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
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
    # 임베딩 슬롯(AIDH_EMBED_ACTIVE · AIDH_EMBED_FILL) — 모르는 값 · 없는 열이면 여기서 멈춘다(조용히 v1 로
    # 떨어지지 않는다). v1 의 차원 어긋남은 아래 검사가 예전처럼 경고만 한다.
    await _check_embed_slots()

    # v0.13.0 — EMBEDDING_DIM 환경변수와 실제 vector 컬럼 차원 일치 검증.
    # 불일치 시 sample_embedding sync 가 런타임에 실패하므로 부팅 단계에서 경고.
    await _check_embedding_dim_consistency()

    # 견주기용 두 번째 인스턴스(AIDH_EVAL_INSTANCE=1) — 같은 DB 를 쓰는 본 인스턴스 곁에 다른 포트로 띄워
    # 활성 슬롯만 바꿔 잰다. 부팅 때 DB 에 쓰는 일(동기화 소스 등록 · 멈춘 동기화 정리 — 본 인스턴스가 돌리는
    # 동기화를 'stale' 로 지운다)과 스케줄러(동기화 · 임베딩 스윕)를 하지 않는다. 검색만 받는다.
    eval_instance = (os.environ.get("AIDH_EVAL_INSTANCE") or "").strip().lower() in ("1", "true", "on", "yes")

    # v0.8 — config/sync_sources.yml 부팅 일괄 등록 (옵션 A).
    # AIDH_SYNC_BOOTSTRAP=false 로 비활성. 파일 없으면 silent skip.
    if not eval_instance:
        await _bootstrap_sync_sources()

    # v0.14 — 무인 운영 (2026-06-10 진단 후속):
    #   1. 재시작 자기치유 — 직전 크래시로 남은 sync_runs.status='running' 정리.
    #   2. embedder 워밍업 — 첫 semantic 질의가 모델 cold-load 1.6s+ 를 맞지 않게.
    #   3. 인앱 스케줄러 — sync_sources.schedule_cron 자동 실행 + embed sweep.
    #      외부 cron 등록에 의존하지 않으므로 '등록 누락 → 데이터 정체' 자체가
    #      불가능해진다 (5/30~6/10 11일 정체의 근본 원인 제거).
    if not eval_instance:
        await _cleanup_stale_sync_runs()
    _schedule_embedder_warmup()
    if eval_instance:
        import logging

        logging.getLogger("api.main").warning(
            "AIDH_EVAL_INSTANCE=1 — 견주기용 인스턴스: 동기화 소스 등록 · 멈춘 동기화 정리 · 스케줄러를 건너뛴다")
        scheduler_task = asyncio.create_task(asyncio.sleep(0))
    else:
        scheduler_task = asyncio.create_task(_scheduler_loop())

    # MCP streamable_http_app 은 자체 lifespan(task group)이 있어야 동작한다.
    # FastAPI 의 mount() 는 sub-app 의 lifespan 을 자동 전파하지 않으므로,
    # 우리 lifespan 에서 명시적으로 enter / exit 한다.
    try:
        if _MCP_AVAILABLE and _mcp_app is not None:
            async with _mcp_app.router.lifespan_context(_mcp_app):
                yield
        else:
            yield
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    await engine.dispose()


# ---------------------------------------------------------------------------
# 무인 운영 — 인앱 스케줄러 (sync + embed sweep)
# ---------------------------------------------------------------------------
async def _cleanup_stale_sync_runs() -> None:
    """프로세스 재시작 시 잔존 'running' run 을 error 처리 — 단일 프로세스
    구조에서 재시작 직후 running 일 수 있는 run 은 존재하지 않는다."""
    import logging

    log = logging.getLogger("api.scheduler")
    try:
        from sqlalchemy import update

        from .db.models import SyncRun

        async with engine.begin() as conn:
            result = await conn.execute(
                update(SyncRun)
                .where(SyncRun.status == "running")
                .values(status="error", error="stale — cleared at startup")
            )
            if result.rowcount:
                log.warning("startup: cleared %d stale running sync_runs", result.rowcount)
    except Exception as exc:  # pragma: no cover — best-effort
        log.warning("stale sync_run cleanup failed: %s", exc)


def _schedule_embedder_warmup() -> None:
    """모델 로드 (~수 초) 를 부팅 블로킹 없이 백그라운드 스레드에서 선실행 — 활성 슬롯의 임베더만.
    채우기만 하는 슬롯의 모델은 백필이 처음 쓸 때 올린다(전환 동안 두 모델이 한 프로세스에 오를 수 있다)."""
    import logging

    log = logging.getLogger("api.scheduler")

    async def _warm() -> None:
        try:
            from .services import embed_slots

            emb = await asyncio.to_thread(embed_slots.embedder, embed_slots.active())
            log.info("embedder warmed up: %s", emb.name)
        except Exception as exc:  # noqa: BLE001
            log.warning("embedder warmup failed: %s", exc)

    asyncio.create_task(_warm())


async def _scheduler_loop() -> None:
    """1분 tick — due 인 sync_sources 실행 + 주기적 embed sweep.

    - ``AIDH_SCHEDULER=off`` 로 전체 비활성 (외부 cron 운영으로 회귀 가능).
    - run_sync 자체에 동시 실행 가드 (sync_runs.status='running') 가 있어
      외부 cron 과 공존해도 안전.
    - partial run 은 last_sync_at 을 전진시키지 않으므로 (R8), 재시도 폭주를
      막기 위해 시도 시각은 in-process 로 따로 기억한다.
    """
    import logging

    log = logging.getLogger("api.scheduler")
    if (os.environ.get("AIDH_SCHEDULER") or "on").strip().lower() in ("off", "0", "false"):
        log.info("in-app scheduler disabled (AIDH_SCHEDULER=off)")
        return

    embed_sweep_min = int(os.environ.get("AIDH_EMBED_SWEEP_MIN", "30") or 30)
    last_attempt: dict[int, float] = {}  # source_id → monotonic seconds
    last_embed_sweep = 0.0  # 0 → 첫 tick 에 즉시 1회 (startup backfill)
    sweep_job = None  # 직전에 띄운 스윕 잡 — 아직 돌면 새로 띄우지 않는다

    log.info(
        "in-app scheduler started (tick=60s, embed_sweep=%dmin)", embed_sweep_min
    )
    while True:
        await asyncio.sleep(60)
        # --- sync sources ---
        try:
            await _run_due_syncs(last_attempt, log)
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduler sync pass failed: %s", exc)
        # --- embed sweep (미임베딩 잔여분 백필 — 실패 자동 복구 안전망) ---
        # 예전엔 앞 스윕이 몇 시간째 돌아도 30분마다 새 잡을 겹쳐 띄웠다. 프로세스 안에서는 직전 잡이 끝났는지 보고,
        # 프로세스 밖(CLI 백필)과는 (슬롯, 표)마다 advisory lock 이 막는다(embed_backfill).
        try:
            if time.monotonic() - last_embed_sweep >= embed_sweep_min * 60 - 1:
                sweep_job = _schedule_embed_sweep(sweep_job, log)
                last_embed_sweep = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            log.warning("embed sweep scheduling failed: %s", exc)

        # --- signature 백필 (매 tick — REST 배치 import 가 ~1분 내 검색되게).
        # 빈 행 0건이면 곧바로 끝나 저렴. find_similar_data ANN 용.
        try:
            await _signature_backfill_sweep(log)
        except Exception as exc:  # noqa: BLE001
            log.warning("signature backfill failed: %s", exc)


def _schedule_embed_sweep(prev, log):
    """임베딩 스윕 잡을 띄운다 — 직전 잡이 아직 돌면 띄우지 않고 그 잡을 그대로 돌려준다."""
    from .services import jobs as job_svc

    if prev is not None and prev.status in ("pending", "running"):
        log.info("embed sweep still running (%s) — 이번 차례는 건너뛴다", prev.id)
        return prev
    job = job_svc.register("embed", job_svc.embed_handler, payload={})
    log.info("embed sweep scheduled")
    return job


async def _signature_backfill_sweep(log) -> None:
    """레코드 시그니처가 빈 행을 채우는 슬롯마다 백필 (sweep 당 슬롯마다 최대 500건)."""
    from .services import embed_backfill

    res = await embed_backfill.run(tables=["records"], limit=500, log_every_s=600)
    for slot_name, tabs in res.items():
        st = tabs.get("records") or {}
        if st.get("processed"):
            log.info("signature backfill %s: filled=%s backlog_at_start=%s",
                     slot_name, st["processed"], st.get("backlog_at_start"))


async def _check_embed_slots() -> None:
    """임베딩 슬롯 설정 검증 — 틀리면 부팅을 멈춘다(SlotConfigError · RuntimeError).

    1. AIDH_EMBED_ACTIVE · AIDH_EMBED_FILL 의 값(모르는 슬롯 이름).
    2. 활성 · 채움 슬롯 중 m3 가 있으면 그 열(halfvec(1024))이 DB 에 있는가(PG 만 — 이행 안 됨이면 멈춘다).
    3. m3 가 활성이면 BGE-M3 모델 파일이 이 박스에 있는가(없으면 멈춘다 — 모든 의미검색이 실패하기 때문).
       채우기만 하면 경고만 한다(백필만 실패하고 검색은 v1 로 돈다).
    """
    import logging

    from .services import embed_slots

    log = logging.getLogger("api.main")
    cfg = embed_slots.validate_settings()
    if "m3" in cfg["fill"]:
        problems: list[str] = []
        # 두 번까지 — 예전엔 풀의 첫 연결이 import 때(mcp_uploads 적재의 asyncio.run) 다른 루프에서 맺은 것이라
        # 'attached to a different loop' 로 버려졌다(실측 2026-10-16). 그 적재는 이제 풀 없는 엔진을 쓴다(H3) — 그래도 DB 가 잠깐
        # 안 닿는 것을 한 번 더 버틴다.
        for attempt in (1, 2):
            try:
                async with engine.connect() as conn:
                    problems = await embed_slots.check_columns(conn)
                break
            except Exception as exc:  # noqa: BLE001 — DB 가 잠깐 안 닿는 것은 열이 틀린 것과 다르다
                if attempt == 2:
                    log.warning("embed slots: DB 에 닿지 못해 m3 열을 확인하지 못했다(%s) — 부팅은 계속한다", exc)
        if problems:
            raise RuntimeError("임베딩 슬롯 열이 맞지 않는다 — " + "; ".join(problems))
        repo = embed_slots.model_id(embed_slots.slot("m3"))
        if not embed_slots.model_files_present(repo):
            msg = (f"{repo} 모델 파일이 없다(AIDH_MODELS_DIR · HF 캐시) — 폐쇄망이면 "
                   "deploy/apptainer/model-from-drive.sh 로 받는다")
            if cfg["active"] == "m3":
                raise RuntimeError(f"AIDH_EMBED_ACTIVE=m3 인데 {msg}")
            log.warning("AIDH_EMBED_FILL 에 m3 가 있는데 %s — m3 백필이 실패한다", msg)
    log.info("embed slots — active=%s fill=%s", cfg["active"], ",".join(cfg["fill"]))


async def _run_due_syncs(last_attempt: dict[int, float], log) -> None:
    from datetime import datetime, timezone

    from sqlalchemy import select

    from .db.base import SessionLocal
    from .db.models import SyncSource
    from .services import sync_svc

    # SessionLocal (expire_on_commit=False) 필수 — raw AsyncSession(engine) 은
    # 기본 expire 라 run_sync 내부 commit 후 src.* 접근이 MissingGreenlet 으로 죽는다.
    async with SessionLocal() as session:
        rows = (
            (await session.execute(select(SyncSource).where(SyncSource.enabled.is_(True))))
            .scalars()
            .all()
        )
        now = datetime.now(timezone.utc)
        # run_sync 의 commit 이후에도 안전하게 쓰도록 필요한 속성만 먼저 복사.
        sources = [
            (src.id, src.name, src.schedule_cron, src.last_sync_at) for src in rows
        ]
        for src_id, src_name, schedule_cron, last_sync_at in sources:
            interval_min = sync_svc.interval_minutes_from_cron(schedule_cron)
            if interval_min is None:
                continue  # schedule_cron 비어있음 = 수동 전용
            # 시도 간격 가드 (partial 시 last_sync_at 미전진 → 폭주 방지)
            mono = time.monotonic()
            prev = last_attempt.get(src_id)
            if prev is not None and (mono - prev) < interval_min * 60:
                continue
            # last_sync_at 기준 due 판정 (재시작 직후 불필요한 즉시 재실행 방지)
            if last_sync_at is not None:
                last_dt = last_sync_at
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if (now - last_dt).total_seconds() < interval_min * 60:
                    continue
            last_attempt[src_id] = mono
            try:
                result = await sync_svc.run_sync(session, src_id, trigger="cron")
                log.info(
                    "scheduled sync %s: status=%s fetched=%s imported=%s skipped=%s failed=%s",
                    src_name,
                    result.get("status"),
                    result.get("fetched"),
                    result.get("imported"),
                    result.get("skipped"),
                    result.get("failed"),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("scheduled sync %s failed: %s", src_name, exc)


async def _bootstrap_sync_sources() -> None:
    """``config/sync_sources.yml`` 을 읽어 sync_sources 자동 등록·갱신.

    옵션 A 핵심:
        - yaml 1개에 모든 외부 연결 선언 → 재현 가능한 배포.
        - api_key 는 ``api_key_env`` 로 환경변수에서 읽음 (yaml 평문 X).
        - DB 에만 있는 source 는 그대로 유지 (수동 등록 보존).
    """
    import logging
    log = logging.getLogger("api.main")
    try:
        from sqlalchemy.ext.asyncio import AsyncSession

        from .services import sync_bootstrap

        async with AsyncSession(engine) as session:
            result = await sync_bootstrap.bootstrap_sync_sources(session)
        if result.get("skipped"):
            return
        log.info(
            "sync_sources bootstrap — created=%s updated=%s unchanged=%s errors=%s file=%s",
            len(result.get("created") or []),
            len(result.get("updated") or []),
            len(result.get("unchanged") or []),
            len(result.get("errors") or []),
            result.get("config_file"),
        )
    except Exception as exc:  # pragma: no cover — best-effort
        log.warning("sync_sources bootstrap failed: %s", exc)


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
    # Behind the HWAX portal this app is reverse-proxied under /ai-data-hub/ (the proxy strips the
    # prefix, so routing is unchanged). Set AIDH_ROOT_PATH=/ai-data-hub so generated URLs
    # (docs / openapi server) carry the public prefix. Empty = standalone.
    root_path=os.environ.get("AIDH_ROOT_PATH", ""),
)

# CORS — env 로 allow_origins 좁히기 가능.
#   AIDH_CORS_ALLOW_ORIGINS="https://aidatahub.internal,https://web.example.com"
#   (생략 시 "*" — 개발 모드. 운영 배포 시 반드시 좁혀라.)
import os as _os_cors

_cors_env = _os_cors.environ.get("AIDH_CORS_ALLOW_ORIGINS", "").strip()
_cors_origins = (
    [o.strip() for o in _cors_env.split(",") if o.strip()]
    if _cors_env else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
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
# /static/examples — wave-5 매니페스트/스크립트 예제 (Dashboard "MCP 도구" 탭
# 의 안내 링크용). repo 의 examples/ 디렉토리를 그대로 노출.
# ---------------------------------------------------------------------------
EXAMPLES_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "examples"
)
if EXAMPLES_DIR.exists():
    app.mount(
        "/static/examples",
        StaticFiles(directory=str(EXAMPLES_DIR), html=False),
        name="examples",
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
async def root(request: Request):
    # Browsers landing on the root (e.g. the HWAX portal tile → /ai-data-hub/) get sent to the
    # dashboard SPA; API clients still receive the JSON status. The relative target resolves under
    # whatever prefix the app is served at.
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(url="dashboard/")
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
