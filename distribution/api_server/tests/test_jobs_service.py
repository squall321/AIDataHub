"""Additional unit tests for ``api.services.jobs``."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_jobs():
    from api.services import jobs as job_svc

    job_svc.clear_all()
    yield
    job_svc.clear_all()


@pytest.mark.asyncio
async def test_job_to_dict_round_trips_payload():
    from api.services import jobs as job_svc

    async def _h(job):
        return {"ok": True}

    j = job_svc.register("ocr", _h, payload={"x": 1})
    d = j.to_dict()
    assert d["kind"] == "ocr"
    assert d["payload"] == {"x": 1}
    assert d["progress"] == 0.0 or 0.0 <= d["progress"] <= 1.0


@pytest.mark.asyncio
async def test_list_jobs_filter_and_limit():
    """``list_jobs`` 의 kind 필터 + limit 동작."""
    from api.services import jobs as job_svc

    async def _h(job):
        return {}

    job_svc.register("ocr", _h)
    job_svc.register("ocr", _h)
    job_svc.register("embed", _h)

    ocr_only = job_svc.list_jobs(kind="ocr")
    assert len(ocr_only) == 2
    assert all(j.kind == "ocr" for j in ocr_only)

    limited = job_svc.list_jobs(limit=1)
    assert len(limited) == 1


@pytest.mark.asyncio
async def test_maybe_schedule_auto_embed_off_returns_none():
    from api.config import settings
    from api.services import jobs as job_svc

    settings.auto_embed_on_insert = False
    result = job_svc.maybe_schedule_auto_embed("DOC-HE-CAE-2026-000099")
    assert result is None
    assert job_svc.list_jobs(kind="embed") == []


@pytest.mark.asyncio
async def test_maybe_schedule_auto_embed_on_returns_job():
    from api.config import settings
    from api.services import jobs as job_svc

    original = settings.auto_embed_on_insert
    settings.auto_embed_on_insert = True
    try:
        job = job_svc.maybe_schedule_auto_embed("DOC-HE-CAE-2026-000099")
        assert job is not None
        assert job.kind == "embed"
        assert job.payload.get("record_id") == "DOC-HE-CAE-2026-000099"
    finally:
        settings.auto_embed_on_insert = original
