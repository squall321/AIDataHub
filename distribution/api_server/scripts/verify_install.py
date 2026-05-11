"""``setup.bat`` / ``setup.sh`` 후 설치 검증 스크립트.

다음을 차례로 검사하고 색깔 있는 요약을 출력한다:

    1. DB 스키마 — alembic_version 의 head 가 ``"0009"`` 인지.
    2. 표준 에이전트 시드 — ``agents`` 테이블에 5 종(``iga-analyst`` 등) 존재.
    3. 핵심 엔드포인트 — ``/`` ``/health`` ``/api/system/health`` ``/api/discover``
       ``/api/schema`` ``/api/hints`` ``/api/docs/llm.txt`` 가 200 응답.
    4. 문서 — ``docs/AGENT_ONBOARDING.md`` 가 존재하고 ``/api/docs/llm.txt`` 는
       200 + non-empty.

각 항목은 ``green`` / ``yellow`` / ``red`` 상태로 분류한다. 모두 green 이면
exit 0, 하나라도 red 이면 exit 1, 그 외(yellow 만 있음)는 exit 0.

운영 환경(라이브 PostgreSQL) 에서 ``alembic upgrade head`` 후 실행하는 것이
표준 사용 케이스이지만, DB 가 비어있어도 (``records`` 테이블이 비면 시드 검사
yellow) 스크립트 자체는 안전하게 종료한다.

실행:
    & .venv/Scripts/python.exe scripts/verify_install.py

옵션 환경변수:
    DATABASE_URL — 검사할 DB. 미지정 시 ``api.config.settings`` 기본값 사용.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path 보정
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ANSI color (Windows 10+ 콘솔에서 자동 활성화).
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
BOLD = "\x1b[1m"


# ---------------------------------------------------------------------------
# 결과 컨테이너
# ---------------------------------------------------------------------------
class Check:
    __slots__ = ("name", "status", "detail")

    def __init__(self, name: str, status: str, detail: str = "") -> None:
        # status ∈ {"green", "yellow", "red"}
        self.name = name
        self.status = status
        self.detail = detail

    def render(self) -> str:
        color = {"green": GREEN, "yellow": YELLOW, "red": RED}[self.status]
        tag = {"green": "  OK ", "yellow": "WARN ", "red": "FAIL "}[self.status]
        line = f"  {color}{tag}{RESET} {self.name}"
        if self.detail:
            line += f"  — {self.detail}"
        return line


# ---------------------------------------------------------------------------
# 1) DB 스키마 (alembic_version)
# ---------------------------------------------------------------------------
async def check_schema() -> Check:
    try:
        from sqlalchemy import text

        from api.db.base import engine
    except Exception as exc:  # pragma: no cover
        return Check("DB schema (alembic head)", "red", f"import failed: {exc}")

    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT version_num FROM alembic_version")
            )
            row = result.fetchone()
    except Exception as exc:
        return Check(
            "DB schema (alembic head)",
            "red",
            f"DB connect/query failed: {type(exc).__name__}",
        )

    if not row:
        return Check(
            "DB schema (alembic head)",
            "yellow",
            "alembic_version 비어있음 — `alembic upgrade head` 가 필요?",
        )
    head = str(row[0])
    expected = "0009"
    if head == expected:
        return Check("DB schema (alembic head)", "green", f"= {head}")
    return Check(
        "DB schema (alembic head)",
        "red",
        f"got {head!r}, expected {expected!r}",
    )


# ---------------------------------------------------------------------------
# 2) 표준 에이전트 시드 (5 종)
# ---------------------------------------------------------------------------
EXPECTED_AGENTS = (
    "iga-analyst",
    "cae-reporter",
    "material-reviewer",
    "process-checker",
    "code-assistant",
)


async def check_seed() -> Check:
    try:
        from sqlalchemy import select

        from api.db.base import SessionLocal
        from api.db.models import Agent
    except Exception as exc:  # pragma: no cover
        return Check("Standard agents seeded", "red", f"import failed: {exc}")

    try:
        async with SessionLocal() as s:
            rows = (await s.execute(select(Agent.agent_type))).scalars().all()
    except Exception as exc:
        return Check(
            "Standard agents seeded",
            "red",
            f"agents 조회 실패: {type(exc).__name__}",
        )
    present = set(rows or [])
    missing = [a for a in EXPECTED_AGENTS if a not in present]
    if not missing:
        return Check(
            "Standard agents seeded",
            "green",
            f"{len(EXPECTED_AGENTS)}/{len(EXPECTED_AGENTS)} present",
        )
    if len(missing) == len(EXPECTED_AGENTS):
        return Check(
            "Standard agents seeded",
            "yellow",
            "0 found — `python -m api.seed` 미실행?",
        )
    return Check(
        "Standard agents seeded",
        "red",
        f"missing: {', '.join(missing)}",
    )


# ---------------------------------------------------------------------------
# 3) 엔드포인트 (in-process ASGI)
# ---------------------------------------------------------------------------
KEY_ENDPOINTS = (
    ("/", "root"),
    ("/health", "health"),
    ("/api/system/health", "system_health"),
    ("/api/discover", "discover"),
    ("/api/schema", "schema"),
    ("/api/hints", "hints"),
    ("/api/docs/llm.txt", "llm_doc"),
)


async def check_endpoints() -> list[Check]:
    out: list[Check] = []
    try:
        from httpx import ASGITransport, AsyncClient

        from api.main import app
    except Exception as exc:  # pragma: no cover
        return [Check("API endpoints", "red", f"app import failed: {exc}")]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://verify") as c:
        for path, name in KEY_ENDPOINTS:
            try:
                r = await c.get(path)
            except Exception as exc:
                out.append(
                    Check(f"GET {path}", "red", f"{type(exc).__name__}: {exc}")
                )
                continue
            if r.status_code != 200:
                out.append(
                    Check(
                        f"GET {path}",
                        "red",
                        f"{r.status_code} {r.text[:60]}",
                    )
                )
                continue
            # llm.txt 는 본문이 비어있으면 yellow.
            if path == "/api/docs/llm.txt" and not (r.text or "").strip():
                out.append(Check(f"GET {path}", "yellow", "200 but empty body"))
                continue
            out.append(Check(f"GET {path} ({name})", "green", "200"))
    return out


# ---------------------------------------------------------------------------
# 4) 문서 파일
# ---------------------------------------------------------------------------
def check_docs() -> list[Check]:
    out: list[Check] = []
    onboarding = ROOT / "docs" / "AGENT_ONBOARDING.md"
    if onboarding.is_file() and onboarding.stat().st_size > 0:
        out.append(
            Check(
                "docs/AGENT_ONBOARDING.md",
                "green",
                f"{onboarding.stat().st_size} bytes",
            )
        )
    else:
        out.append(
            Check(
                "docs/AGENT_ONBOARDING.md",
                "red",
                f"missing: {onboarding}",
            )
        )
    return out


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
async def run_async() -> int:
    print(f"{BOLD}Mobile eXperience AI Data Hub install verification{RESET}")
    print(f"  src = {SRC}")
    print()

    checks: list[Check] = []
    checks.append(await check_schema())
    checks.append(await check_seed())
    checks.extend(await check_endpoints())
    checks.extend(check_docs())

    for c in checks:
        print(c.render())

    n_green = sum(1 for c in checks if c.status == "green")
    n_yellow = sum(1 for c in checks if c.status == "yellow")
    n_red = sum(1 for c in checks if c.status == "red")

    print()
    print(
        f"  Summary: {GREEN}{n_green} green{RESET}, "
        f"{YELLOW}{n_yellow} yellow{RESET}, "
        f"{RED}{n_red} red{RESET}"
    )

    if n_red > 0:
        print(f"{RED}Install verification FAILED.{RESET}")
        return 1
    if n_yellow > 0:
        print(
            f"{YELLOW}Install verification PASSED with warnings.{RESET}"
        )
        return 0
    print(f"{GREEN}Install verification PASSED.{RESET}")
    return 0


def main() -> int:
    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:  # pragma: no cover
            pass
    try:
        return asyncio.run(run_async())
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
