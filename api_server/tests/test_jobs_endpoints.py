"""S3. Async job queue endpoint tests."""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _clear_jobs():
    from api.services import jobs as job_svc

    job_svc.clear_all()
    yield
    job_svc.clear_all()


@pytest.mark.asyncio
async def test_list_jobs_empty(test_client) -> None:
    resp = await test_client.get("/api/jobs/")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"jobs": []}


@pytest.mark.asyncio
async def test_register_and_get(test_client) -> None:
    """register() 로 즉시 잡을 만들고 GET /api/jobs/{id} 로 조회."""
    from api.services import jobs as job_svc

    async def _h(job):
        await asyncio.sleep(0.01)
        return {"hello": "world"}

    job = job_svc.register("ocr", _h)
    assert job.kind == "ocr"
    assert job.status in ("pending", "running")

    resp = await test_client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == job.id

    # 잡이 끝날 때까지 기다린 뒤 다시 조회.
    for _ in range(50):
        await asyncio.sleep(0.02)
        if job.status in ("done", "failed"):
            break
    assert job.status == "done", f"status={job.status} error={job.error}"
    assert job.result == {"hello": "world"}


@pytest.mark.asyncio
async def test_post_embed_returns_202(db_client) -> None:
    """``POST /api/jobs/embed`` 가 202 + job_id 를 돌려준다."""
    resp = await db_client.post("/api/jobs/embed", json={})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["kind"] == "embed"
    assert body["status"] in ("pending", "running", "done", "failed")
    assert body["id"]


@pytest.mark.asyncio
async def test_get_job_404(test_client) -> None:
    resp = await test_client.get("/api/jobs/nonexistent-xxx")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_filters_by_kind(test_client) -> None:
    from api.services import jobs as job_svc

    async def _h(job):
        return {}

    job_svc.register("ocr", _h)
    job_svc.register("embed", _h)

    resp = await test_client.get("/api/jobs/?kind=ocr")
    assert resp.status_code == 200
    body = resp.json()
    assert all(j["kind"] == "ocr" for j in body["jobs"])
    assert len(body["jobs"]) == 1


@pytest.mark.asyncio
async def test_failed_job_records_error(test_client) -> None:
    from api.services import jobs as job_svc

    async def _bad(job):
        raise RuntimeError("boom")

    job = job_svc.register("ocr", _bad)
    for _ in range(50):
        await asyncio.sleep(0.02)
        if job.status in ("done", "failed"):
            break
    assert job.status == "failed"
    assert "boom" in (job.error or "")
