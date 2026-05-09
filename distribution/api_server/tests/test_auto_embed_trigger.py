"""S4. Auto-embedding trigger tests.

``AUTO_EMBED_ON_INSERT=true`` 일 때 :func:`api.ingest.db_writer.write_record`
가 ``api.services.jobs.maybe_schedule_auto_embed`` 를 호출해 임베딩 backfill
잡이 등록되는지 검증한다. 기본값 (False) 에서는 등록되지 않아야 한다.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_jobs_and_settings():
    """각 테스트가 격리된 in-memory 잡 큐와 설정을 사용하도록."""
    from api.config import settings
    from api.services import jobs as job_svc

    original = bool(getattr(settings, "auto_embed_on_insert", False))
    job_svc.clear_all()
    yield
    settings.auto_embed_on_insert = original
    job_svc.clear_all()


@pytest.mark.asyncio
async def test_auto_embed_schedules_when_flag_on(test_session, sample_doc_record_dict):
    """``AUTO_EMBED_ON_INSERT=True`` 면 INSERT 후 ``embed`` 잡이 1개 등록된다."""
    from api.config import settings
    from api.ingest.db_writer import write_record
    from api.ingest.normalizer import normalize
    from api.services import jobs as job_svc

    settings.auto_embed_on_insert = True

    record_in = normalize(sample_doc_record_dict)
    result = await write_record(test_session, record_in)
    await test_session.flush()

    assert result.action == "inserted"
    assert result.sections_written >= 1

    embed_jobs = job_svc.list_jobs(kind="embed")
    assert len(embed_jobs) >= 1, "expected at least one embed job to be scheduled"
    j = embed_jobs[0]
    assert j.kind == "embed"
    # payload 에 record_id 가 들어가 있어야 함.
    assert j.payload.get("record_id") == record_in.id


@pytest.mark.asyncio
async def test_auto_embed_skipped_when_flag_off(test_session, sample_doc_record_dict):
    """기본값 (False) 에서는 잡이 등록되지 않는다."""
    from api.config import settings
    from api.ingest.db_writer import write_record
    from api.ingest.normalizer import normalize
    from api.services import jobs as job_svc

    settings.auto_embed_on_insert = False

    record_in = normalize(sample_doc_record_dict)
    await write_record(test_session, record_in)
    await test_session.flush()

    assert job_svc.list_jobs(kind="embed") == []
