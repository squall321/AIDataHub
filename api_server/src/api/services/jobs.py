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
    """임베딩 backfill 핸들러 — 본체는 :mod:`api.services.embed_backfill` 이다.

    동작:
        1. ``payload['record_id']`` (단일) 또는 ``payload['record_ids']`` (리스트) 가 있으면 그 레코드의
           절 · 시그니처만, 없으면 네 표(레코드 시그니처 · 에이전트 표본 · 도구 설명 · 절) 전부의 빈 행을 채운다.
        2. 채우는 슬롯은 ``AIDH_EMBED_FILL`` + 활성 슬롯(``embed_slots.fill``)이다 — 기본은 v1 하나.
        3. 키셋 페이지로 읽고 청크마다 커밋한다. (슬롯, 표)마다 advisory lock 하나 — 이미 누가 채우는 중이면
           그 표는 건너뛴다(``locked``). 빈 텍스트는 ``skipped-empty`` 로 표시하고, 인코딩 실패는 세고 넘어간다.

    Returns:
        예전 키(활성 슬롯의 절 기준) ``{"sections_processed", "skipped", "failed", "model",
        "over_model_limit", "model_max_tokens"}`` + 슬롯 · 표별 전부 ``by_slot``.
    """
    from . import embed_backfill, embed_slots

    target_ids = job.payload.get("record_ids")
    single_id = job.payload.get("record_id")
    if not target_ids and single_id:
        target_ids = [single_id]

    tables = ["records", "record_sections"] if target_ids else None
    slots = embed_slots.fill()
    plan = len(slots) * (len(tables) if tables else len(embed_slots.TABLES))
    done_parts = 0

    def _progress(done: int, total: int) -> None:
        # (슬롯, 표) 하나가 끝날 때마다 한 칸 — 그 안에서는 행 비율로 채운다.
        frac = (done / total) if total else 1.0
        job.progress = min((done_parts + min(frac, 1.0)) / max(plan, 1), 1.0)

    by_slot: dict[str, dict] = {}
    for s in slots:
        for table in (tables or list(embed_slots.TABLES)):
            res = await embed_backfill.run([s], [table], record_ids=target_ids, progress=_progress)
            by_slot.setdefault(s.name, {}).update(res.get(s.name, {}))
            done_parts += 1
            job.progress = min(done_parts / max(plan, 1), 1.0)

    active = embed_slots.active().name
    sec = (by_slot.get(active) or {}).get("record_sections") or {}
    errors = {f"{sn}/{tn}": st["error"] for sn, tabs in by_slot.items()
              for tn, st in tabs.items() if st.get("error")}
    for sn, tabs in by_slot.items():
        st = tabs.get("record_sections") or {}
        if st.get("over_model_limit"):
            logger.warning(
                "embed_handler: %d sections exceed the model input limit (%s tokens) — only the head "
                "of each is embedded (slot %s, model %s)", st["over_model_limit"], st.get("model_max_tokens"),
                sn, st.get("model"),
            )
    return {
        "sections_processed": int(sec.get("processed") or 0),
        "skipped": int(sec.get("skipped_empty") or 0),
        "failed": int(sec.get("failed") or 0),
        "model": sec.get("model"),
        "over_model_limit": int(sec.get("over_model_limit") or 0),
        "model_max_tokens": sec.get("model_max_tokens"),
        "slot": active,
        "by_slot": by_slot,
        "errors": errors,
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
