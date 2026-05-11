"""Locust 부하 시나리오 — Mobile eXperience AI Data Hub API.

총 4개 시나리오를 클래스 단위로 정의했고, ``--tags`` 또는 클래스 선택으로
하나만 실행하거나 가중치 비율로 섞어서 실행할 수 있다.

기본 실행 (read-heavy 단독):
    locust -f locustfile.py --host http://localhost:8000

헤드리스 (예: CI 30초 부하):
    locust -f locustfile.py --headless -u 100 -r 10 --run-time 60s \
           --host http://localhost:8000

특정 시나리오만:
    locust -f locustfile.py --headless --run-time 30s \
           --host http://localhost:8000 -u 50 -r 5 \
           --tags read

Tag 사용법:
    @tag('read')   — 80/20 read-heavy
    @tag('write')  — 80/20 write-heavy
    @tag('mcp')    — MCP-style discover/ask/data 패턴
    @tag('burst')  — 짧은 burst 트래픽

API key 가 필요한 환경:
    export API_KEY=...        # bash
    $env:API_KEY = "..."      # PowerShell
"""
from __future__ import annotations

import json
import os
import random
import string
import uuid
from typing import Any

from locust import HttpUser, between, constant_pacing, events, tag, task

# ---------------------------------------------------------------------------
# 공용 상수
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("API_KEY") or os.environ.get("X_API_KEY")
DEFAULT_AGENTS = [
    "iga-analyst",
    "cae-reporter",
    "battery-eng",
    "process-eng",
    "code-reviewer",
]
DEFAULT_TAGS = ["IGA", "battery", "thermal", "fillet", "split", "offset"]
DEFAULT_QUERIES = [
    "offset 처리",
    "열폭주 시뮬레이션",
    "fillet split",
    "battery cell",
    "code review",
    "사업부 IGA",
    "최근 1주일 보고서",
]


def _auth_headers() -> dict[str, str]:
    if API_KEY:
        return {"X-API-Key": API_KEY}
    return {}


def _rand_text(n: int = 40) -> str:
    return "".join(random.choices(string.ascii_lowercase + " ", k=n)).strip()


def _rand_record_payload() -> dict[str, Any]:
    """ingest 시뮬레이션용 가상 record (실제 스키마와 100% 호환은 아님 — 부하만 측정)."""
    rid = f"LOAD-{uuid.uuid4().hex[:10].upper()}"
    return {
        "id": rid,
        "title": f"load-test-{_rand_text(20)}",
        "summary": _rand_text(120),
        "data_type": random.choice(["DOCUMENT", "TABLE", "FIGURE"]),
        "tags": random.sample(DEFAULT_TAGS, k=2),
        "owner_dept": "loadtest",
    }


# ---------------------------------------------------------------------------
# 1) Read-heavy: 80% list/get/search, 20% ingest. 100 users, 1/s ramp.
# ---------------------------------------------------------------------------
class ReadHeavyUser(HttpUser):
    """80/20 read-heavy 워크로드.

    실행:
        locust -f locustfile.py --headless -u 100 -r 1 --run-time 60s \
               --host http://localhost:8000 --tags read
    """

    wait_time = between(0.5, 1.5)

    def on_start(self) -> None:
        self.client.headers.update(_auth_headers())

    @tag("read")
    @task(4)  # 40%
    def list_records(self) -> None:
        self.client.get("/api/records?limit=20", name="GET /api/records")

    @tag("read")
    @task(2)  # 20%
    def search_fts(self) -> None:
        q = random.choice(DEFAULT_QUERIES)
        self.client.get(
            f"/api/search?mode=fts&q={q}&limit=10",
            name="GET /api/search?mode=fts",
        )

    @tag("read")
    @task(2)  # 20%
    def query_data(self) -> None:
        agent = random.choice(DEFAULT_AGENTS)
        self.client.get(
            f"/api/data?agent={agent}&limit=5",
            name="GET /api/data",
        )

    @tag("read")
    @task(2)  # 20% (write)
    def ingest_record(self) -> None:
        payload = _rand_record_payload()
        with self.client.post(
            "/api/records",
            json=payload,
            name="POST /api/records",
            catch_response=True,
        ) as resp:
            # 422 도 허용 (스키마 mismatch — 부하 자체만 측정)
            if resp.status_code in (200, 201, 409, 422):
                resp.success()
            else:
                resp.failure(f"unexpected status {resp.status_code}")


# ---------------------------------------------------------------------------
# 2) Write-heavy: 80% ingest, 20% read. 50 users.
# ---------------------------------------------------------------------------
class WriteHeavyUser(HttpUser):
    """80/20 write-heavy 워크로드 — 인제스트 부하 한계 확인용.

    실행:
        locust -f locustfile.py --headless -u 50 -r 5 --run-time 60s \
               --host http://localhost:8000 --tags write
    """

    wait_time = between(0.2, 0.8)

    def on_start(self) -> None:
        self.client.headers.update(_auth_headers())

    @tag("write")
    @task(8)  # 80%
    def ingest_record(self) -> None:
        payload = _rand_record_payload()
        with self.client.post(
            "/api/records",
            json=payload,
            name="POST /api/records",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201, 409, 422):
                resp.success()
            else:
                resp.failure(f"unexpected status {resp.status_code}")

    @tag("write")
    @task(1)  # 10%
    def list_records(self) -> None:
        self.client.get("/api/records?limit=10", name="GET /api/records")

    @tag("write")
    @task(1)  # 10%
    def search(self) -> None:
        q = random.choice(DEFAULT_QUERIES)
        self.client.get(
            f"/api/search?mode=fts&q={q}&limit=5",
            name="GET /api/search?mode=fts",
        )


# ---------------------------------------------------------------------------
# 3) MCP query mix — discover / ask / data 패턴
# ---------------------------------------------------------------------------
class McpMixUser(HttpUser):
    """MCP 클라이언트가 흔히 보내는 호출 패턴.

    실행:
        locust -f locustfile.py --headless -u 30 -r 3 --run-time 60s \
               --host http://localhost:8000 --tags mcp
    """

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self.client.headers.update(_auth_headers())

    @tag("mcp")
    @task(2)
    def discover(self) -> None:
        self.client.get("/api/discover", name="GET /api/discover")

    @tag("mcp")
    @task(1)
    def schema(self) -> None:
        self.client.get("/api/schema", name="GET /api/schema")

    @tag("mcp")
    @task(3)
    def query_data(self) -> None:
        agent = random.choice(DEFAULT_AGENTS)
        q = random.choice(DEFAULT_QUERIES)
        self.client.get(
            f"/api/data?agent={agent}&query={q}&limit=5",
            name="GET /api/data",
        )

    @tag("mcp")
    @task(2)
    def ask(self) -> None:
        q = random.choice(DEFAULT_QUERIES)
        with self.client.post(
            "/api/ask",
            json={"query": q, "limit": 5},
            name="POST /api/ask",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 422):
                resp.success()
            else:
                resp.failure(f"unexpected status {resp.status_code}")

    @tag("mcp")
    @task(1)
    def list_agents(self) -> None:
        self.client.get("/api/agents", name="GET /api/agents")


# ---------------------------------------------------------------------------
# 4) Burst: 200 users, 30s burst — spike 내성 확인
# ---------------------------------------------------------------------------
class BurstUser(HttpUser):
    """짧은 spike 트래픽. constant pacing 으로 RPS 압박.

    실행:
        locust -f locustfile.py --headless -u 200 -r 50 --run-time 30s \
               --host http://localhost:8000 --tags burst
    """

    # 한 user 가 1 초마다 한 번 호출 → 200 users ≈ 200 RPS
    wait_time = constant_pacing(1.0)

    def on_start(self) -> None:
        self.client.headers.update(_auth_headers())

    @tag("burst")
    @task(5)
    def health(self) -> None:
        self.client.get("/health", name="GET /health")

    @tag("burst")
    @task(3)
    def list_records(self) -> None:
        self.client.get("/api/records?limit=5", name="GET /api/records")

    @tag("burst")
    @task(2)
    def search(self) -> None:
        q = random.choice(DEFAULT_QUERIES)
        self.client.get(
            f"/api/search?mode=fts&q={q}&limit=5",
            name="GET /api/search?mode=fts",
        )


# ---------------------------------------------------------------------------
# 종료 시 요약 (헤드리스 자동화 친화)
# ---------------------------------------------------------------------------
@events.quitting.add_listener
def _print_summary(environment: Any, **_: Any) -> None:  # pragma: no cover
    stats = environment.stats
    total = stats.total
    print("\n=== Load test summary ===")
    print(f"  total requests : {total.num_requests}")
    print(f"  failures       : {total.num_failures}")
    print(f"  median (p50)   : {total.median_response_time} ms")
    p95 = total.get_response_time_percentile(0.95)
    print(f"  p95            : {p95} ms")
    p99 = total.get_response_time_percentile(0.99)
    print(f"  p99            : {p99} ms")
    print(f"  RPS (avg)      : {total.total_rps:.1f}")
    if total.num_failures > 0:
        print("  WARNING: failures detected — check report for details.")
