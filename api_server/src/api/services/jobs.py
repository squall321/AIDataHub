"""In-memory async job queue (light version).

For long-running tasks (embedding backfill, OCR, batch ingest from API)
we register a ``Job`` and run it in an ``asyncio.create_task`` background
pool — no external broker (Celery/RQ/Arq) for now.

Design:
    - Job records are kept in a process-local dict keyed by ``job_id``.
    - TTL-based pruning: expired jobs are deleted on the next read.
    - One background ``asyncio.Task`` per job — concurrency is bounded by
      the per-kind ``Semaphore`` registered at module load.
    - ``progress`` is a 0..1 float; handlers update it via ``Job.update``.
    - ``result`` and ``error`` are dict / str respectively.

Limitations (deferred):
    - Process-local. A multi-process deployment must move to Redis/Arq
      or a DB-backed queue.
    - No retries / dead-letter; failures simply set ``status='failed'``.
    - No persistence across restarts.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job dataclass
# ---------------------------------------------------------------------------
@dataclass
class Job:
    """In-memory job record."""

    id: str
    kind: str  # 'embed' | 'ocr' | 'batch_ingest'
    status: str = "pending"  # 'pending' | 'running' | 'done' | 'failed'
    progress: float = 0.0
    result: dict | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": round(float(self.progress), 4),
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "payload": dict(self.payload),
        }

    def update(self, **changes: Any) -> None:
        for k, v in changes.items():
            if hasattr(self, k):
                setattr(self, k, v)


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------
_JOBS: dict[str, Job] = {}
_TASKS: dict[str, asyncio.Task] = {}
# kind 별 semaphore — 동시 실행 상한 (default 4).
_SEMAPHORES: dict[str, asyncio.Semaphore] = {}


def _semaphore_for(kind: str, default: int = 4) -> asyncio.Semaphore:
    sem = _SEMAPHORES.get(kind)
    if sem is None:
        sem = asyncio.Semaphore(default)
        _SEMAPHORES[kind] = sem
    return sem


def _prune_expired() -> None:
    """TTL 초과 잡 삭제 — 호출 시점에 lazy 수행."""
    cutoff = time.time() - int(getattr(settings, "jobs_ttl_seconds", 3600) or 3600)
    expired = [jid for jid, j in _JOBS.items() if (j.finished_at or j.created_at) < cutoff]
    for jid in expired:
        _JOBS.pop(jid, None)
        # 완료된 잡의 task 참조 정리.
        t = _TASKS.pop(jid, None)
        if t is not None and not t.done():
            t.cancel()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
JobHandler = Callable[[Job], Awaitable[dict]]


def register(
    kind: str,
    handler: JobHandler,
    *,
    payload: dict | None = None,
    job_id: str | None = None,
    concurrency: int = 4,
) -> Job:
    """잡을 등록하고 background task 로 실행한다.

    Returns the freshly created ``Job`` (status ``pending``). The returned
    record may already be ``running`` by the time the caller inspects it.
    """
    _prune_expired()

    jid = job_id or f"{kind}-{uuid.uuid4().hex[:12]}"
    job = Job(id=jid, kind=kind, payload=dict(payload or {}))
    _JOBS[jid] = job

    sem = _semaphore_for(kind, default=concurrency)

    async def _runner() -> None:
        async with sem:
            job.status = "running"
            job.started_at = time.time()
            try:
                res = await handler(job)
                job.result = dict(res) if isinstance(res, dict) else {"value": res}
                job.status = "done"
                job.progress = 1.0
            except asyncio.CancelledError:
                job.status = "failed"
                job.error = "cancelled"
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("job %s failed", jid)
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
            finally:
                job.finished_at = time.time()
                _TASKS.pop(jid, None)

    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_runner())
        _TASKS[jid] = task
    except RuntimeError:
        # 이벤트 루프가 없는 동기 컨텍스트 (best-effort 폴백): 잡을 즉시
        # ``failed`` 로 표시하지 말고 ``pending`` 상태를 유지한다.
        # 호출 측에서 await 기반 컨텍스트로 옮겨야 함.
        logger.warning(
            "no running event loop — job %s remains pending until scheduled",
            jid,
        )

    return job


def get(job_id: str) -> Job | None:
    _prune_expired()
    return _JOBS.get(job_id)


def list_jobs(kind: str | None = None, *, limit: int | None = None) -> list[Job]:
    _prune_expired()
    items = list(_JOBS.values())
    if kind:
        items = [j for j in items if j.kind == kind]
    items.sort(key=lambda j: j.created_at, reverse=True)
    cap = int(limit or getattr(settings, "jobs_list_limit", 100) or 100)
    return items[:cap]


def clear_all() -> None:
    """주로 테스트 격리를 위한 헬퍼."""
    for t in list(_TASKS.values()):
        if not t.done():
            t.cancel()
    _TASKS.clear()
    _JOBS.clear()


# ---------------------------------------------------------------------------
# Embedding job handler
# ---------------------------------------------------------------------------
async def embed_handler(job: Job) -> dict:
    """임베딩 backfill 핸들러.

    동작:
        1. ``payload['record_id']`` (단일) 또는 ``payload['record_ids']``
           (리스트) 로 대상 레코드를 정한다. 둘 다 없으면 미임베딩
           (``embedding IS NULL``) 섹션 전체를 처리한다.
        2. :func:`api.services.embedding.get_embedder` 로 embedder 를 얻어
           각 섹션의 ``content_text`` 에 대해 :meth:`Embedder.encode` 를
           호출, ``record_sections.embedding`` / ``embedded_at`` /
           ``embedding_model`` 컬럼을 갱신한다.
        3. 빈 텍스트는 skip, 인코딩 실패는 warning 후 skip.

    Returns:
        ``{"sections_processed": N, "skipped": M, "model": "..."}``
    """
    from datetime import datetime, timezone

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ..db.base import engine
    from ..db.models import RecordSection
    from .embedding import get_embedder

    target_ids = job.payload.get("record_ids")
    single_id = job.payload.get("record_id")
    if not target_ids and single_id:
        target_ids = [single_id]

    # embedder 인스턴스화 (provider 미설정 시 HashEmbedder default).
    try:
        embedder = get_embedder()
    except Exception as exc:  # noqa: BLE001
        logger.error("embed_handler: get_embedder failed: %s", exc)
        raise

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    processed = 0
    skipped = 0
    failed = 0

    async with sessionmaker() as session:
        stmt = select(RecordSection)
        if target_ids:
            stmt = stmt.where(RecordSection.record_id.in_(list(target_ids)))
        else:
            # backfill 모드: 미임베딩 섹션만 대상 (재실행 안전).
            stmt = stmt.where(RecordSection.embedding.is_(None))
        result = await session.execute(stmt)
        sections = list(result.scalars().all())

        total = max(len(sections), 1)
        now = datetime.now(timezone.utc)

        for i, sec in enumerate(sections, start=1):
            text = (sec.content_text or "").strip()
            if not text:
                skipped += 1
            else:
                try:
                    vec = embedder.encode(text[:8000])
                    sec.embedding = vec
                    sec.embedded_at = now
                    sec.embedding_model = embedder.name
                    processed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "embed_handler: encode failed for section %s: %s",
                        sec.id, exc,
                    )
                    failed += 1
            job.progress = i / total
        await session.commit()

    return {
        "sections_processed": processed,
        "skipped": skipped,
        "failed": failed,
        "model": embedder.name,
    }


# ---------------------------------------------------------------------------
# Auto-trigger helpers
# ---------------------------------------------------------------------------
def maybe_schedule_auto_embed(record_id: str) -> Job | None:
    """``AUTO_EMBED_ON_INSERT=true`` 일 때 임베딩 잡을 등록한다."""
    if not getattr(settings, "auto_embed_on_insert", False):
        return None
    try:
        return register(
            "embed",
            embed_handler,
            payload={"record_id": record_id},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("maybe_schedule_auto_embed failed: %s", exc)
        return None


__all__ = [
    "Job",
    "JobHandler",
    "clear_all",
    "embed_handler",
    "get",
    "list_jobs",
    "maybe_schedule_auto_embed",
    "register",
]
