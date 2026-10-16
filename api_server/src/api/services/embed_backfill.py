# 슬롯별 임베딩 백필 — 키셋 페이지 · 청크마다 커밋 · (슬롯, 표)마다 advisory lock 하나 · 이어하기
"""임베딩 백필.

예전 ``jobs.embed_handler`` 는 빈 절 전부를 ORM 객체로 올리고 끝에 한 번 커밋했다 — dev 에서 절 21만 개에
RSS 16GB · 몇 시간 열린 트랜잭션이었고, 스케줄러는 30분마다 새 잡을 겹쳐 띄웠다(HWAXPortal
``docs/embedder-bge-m3`` EM-D4). 절 109만 개를 새 모델로 다시 임베딩하려면 이렇게는 안 된다. 그래서

- **키셋 페이지** — 기본키 순으로 ``chunk`` 행씩, 필요한 열(키 · 글)만 읽는다. 메모리는 청크 크기로 묶인다.
- **청크마다 커밋** — 멈춰도 그때까지 채운 것은 남고, 다시 돌리면 빈 행부터 이어 간다(이어하기는 "비어 있는
  행" 조건 그 자체다 — 따로 적어 두는 진행 표지가 없다).
- **(슬롯, 표)마다 하나** — PostgreSQL advisory lock 으로 같은 슬롯 · 같은 표를 두 러너가 함께 채우지 않는다
  (인앱 스윕끼리, 스윕과 CLI). 잡지 못하면 기다리지 않고 '잠김' 으로 돌아간다. 레코드 범위 실행(적재 직후 자동 임베딩)은
  몇 행뿐이라 잠금 없이 곧바로 돈다.
- **청크 단위 실패 격리** — 인코딩 · 쓰기가 실패한 청크는 세고 건너뛴다(다음 실행이 다시 집는다).
- **빈 글** — 기록 열이 있는 표(절, 그리고 m3 의 모든 표)는 시각을 채우고 모델을 ``skipped-empty`` 로 적어
  백로그에서 뺀다. 기록 열이 없는 v1 의 세 표는 빈 글을 처음부터 고르지 않는다.

쓰는 열은 슬롯이 정한다(``embed_slots``). 글은 표마다 — 절 본문, 레코드 시그니처(``similarity_svc`` 의 규칙),
에이전트 표본, 도구 설명. 레코드를 갱신해도 ``updated_at`` 을 움직이지 않는다 — 임베딩은 내용의 파생값이고,
cae00 merge 가 ``updated_at`` 으로 어느 쪽이 최신인지 가른다(ORM 의 onupdate 는 자기 값으로 막고, PG 트리거는
``SET LOCAL aidh.keep_updated_at = 'on'`` 으로 건너뛴다 — 이행 0032).
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, bindparam, func, select, text, update

from . import embed_slots
from .embed_slots import Slot

log = logging.getLogger(__name__)

SKIPPED_EMPTY = "skipped-empty"
# 한 번에 읽고 커밋하는 행 수. 크면 그 안에서 토큰 수로 줄 세운 묶음이 고르게 돼 빠르다 — BGE-M3 절 표본에서
# 256 행 82.9 → 1,024 행 94.6 절/초(dev RTX 5070 Ti · 공유 GPU, 2026-10-16). 메모리는 청크당 수십 MB 다.
DEFAULT_CHUNK = 1024
# 글 앞 8,000자만 모델에 준다 — 토크나이저 비용 보호(embedding._wrap 과 같은 값). 실효 한도는 모델 토큰이다.
_TEXT_CAP = 8000


# ---------------------------------------------------------------------------
# 표마다 — 키 · 글 · 범위
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Spec:
    table: str
    pk: str
    # 키셋으로 읽을 열(키 다음) — 글을 만드는 데 필요한 것만.
    columns: Callable[[Any], list]
    text: Callable[[Any], str]
    # 이 표의 행이 레코드 범위(record_ids)에 드는 조건. None 이면 범위를 준 실행에서는 이 표를 건너뛴다.
    record_filter: Callable[[Any, list[str]], Any] | None
    # 늘 거는 조건(지운 레코드 · 글 없는 행 등)
    always: Callable[[Any], list]
    # 기록 열이 없는 슬롯에서 빈 글을 처음부터 거르는 조건
    nonempty: Callable[[Any], Any]


def _models():
    from ..db import models

    return models


def _sections_spec() -> _Spec:
    return _Spec(
        table="record_sections",
        pk="id",
        columns=lambda mdl: [mdl.content_text],
        text=lambda row: row.content_text or "",
        record_filter=lambda mdl, ids: mdl.record_id.in_(ids),
        always=lambda mdl: [],
        nonempty=lambda mdl: func.length(func.trim(mdl.content_text)) > 0,
    )


def _records_spec() -> _Spec:
    from .similarity_svc import signature_text

    return _Spec(
        table="records",
        pk="id",
        # 시그니처에 쓰는 것만 꺼낸다 — content 전체(평균 12KB · 최대 수백 KB)를 끌어오지 않는다.
        columns=lambda mdl: [
            mdl.title,
            mdl.content["caption"].as_string().label("caption"),
            mdl.content["headers"].label("headers"),
            mdl.content["notes"].as_string().label("notes"),
        ],
        text=lambda row: signature_text(
            title=row.title or "", caption=row.caption, headers=row.headers, notes=row.notes
        ),
        record_filter=lambda mdl, ids: mdl.id.in_(ids),
        always=lambda mdl: [mdl.deleted_at.is_(None)],
        nonempty=lambda mdl: mdl.id.is_not(None),  # 시그니처는 빈 글이 없다('(empty)' 로 채운다)
    )


def _samples_spec() -> _Spec:
    return _Spec(
        table="agent_sample_embeddings",
        pk="id",
        columns=lambda mdl: [mdl.sample_text],
        text=lambda row: row.sample_text or "",
        record_filter=None,
        always=lambda mdl: [],
        nonempty=lambda mdl: func.length(func.trim(mdl.sample_text)) > 0,
    )


def _uploads_spec() -> _Spec:
    return _Spec(
        table="mcp_uploads",
        pk="name",
        columns=lambda mdl: [mdl.description_text],
        text=lambda row: row.description_text or "",
        record_filter=None,
        # 설명이 없는 도구는 어느 슬롯에서도 백로그가 아니다 — 설명을 쓸 때(sync_tool_embedding) 슬롯 열을 비운다.
        always=lambda mdl: [mdl.description_text.is_not(None)],
        nonempty=lambda mdl: func.length(func.trim(mdl.description_text)) > 0,
    )


_SPEC_BUILDERS: dict[str, Callable[[], _Spec]] = {
    "record_sections": _sections_spec,
    "records": _records_spec,
    "agent_sample_embeddings": _samples_spec,
    "mcp_uploads": _uploads_spec,
}


def _model_for(table: str):
    m = _models()
    return {
        "record_sections": m.RecordSection,
        "records": m.Record,
        "agent_sample_embeddings": m.AgentSampleEmbedding,
        "mcp_uploads": m.MCPUpload,
    }[table]


def backlog_condition(s: Slot, table: str):
    """그 슬롯 · 표에서 '아직 채울 행' 조건 — 백필과 상태 보고(health · CLI)가 같은 식을 쓴다."""
    mdl = _model_for(table)
    spec = _SPEC_BUILDERS[table]()
    cols = s.cols(table)
    conds = [getattr(mdl, cols.vec).is_(None), *spec.always(mdl)]
    if cols.at:
        conds.append(getattr(mdl, cols.at).is_(None))
    else:
        conds.append(spec.nonempty(mdl))
    return and_(*conds)


# ---------------------------------------------------------------------------
# 잠금 — (슬롯, 표)마다 하나
# ---------------------------------------------------------------------------
_LOCAL_LOCKS: set[str] = set()  # SQLite(시험) 대역 — 프로세스 안에서만 막는다


def lock_key(slot_name: str, table: str) -> int:
    """advisory lock 키(bigint) — 이름에서 결정론으로 만든다(박스 · 프로세스가 달라도 같다)."""
    digest = hashlib.sha256(f"aidh-embed-backfill:{slot_name}:{table}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@contextlib.asynccontextmanager
async def _no_lock() -> AsyncIterator[bool]:
    yield True


@contextlib.asynccontextmanager
async def _hold(engine, slot_name: str, table: str) -> AsyncIterator[bool]:
    """잡으면 True, 이미 누가 쥐고 있으면 False. PG 는 세션 advisory lock 을 연결 하나에 쥐고 끝에 놓는다
    (프로세스가 죽으면 연결이 끊기며 PG 가 놓는다). 연결은 AUTOCOMMIT — 몇 시간 열린 트랜잭션을 만들지 않는다."""
    name = f"{slot_name}:{table}"
    if engine.dialect.name != "postgresql":
        if name in _LOCAL_LOCKS:
            yield False
            return
        _LOCAL_LOCKS.add(name)
        try:
            yield True
        finally:
            _LOCAL_LOCKS.discard(name)
        return
    key = lock_key(slot_name, table)
    conn = await engine.connect()
    try:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        got = bool((await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})).scalar())
        try:
            yield got
        finally:
            if got:
                await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 한 슬롯 · 한 표
# ---------------------------------------------------------------------------
def _encode_chunk(embedder, texts: list[str], batch: int | None) -> tuple[list[list[float]], int]:
    """스레드에서 돈다 — 인코딩하고 모델 입력 한도를 넘은 글 수를 센다(뒤쪽은 벡터에 들지 않는다)."""
    vecs = embedder.encode_many(texts, batch_size=batch)
    over = 0
    limit = embedder.input_limit()
    if limit:
        try:
            counts = embedder.input_tokens_many(texts)
        except Exception:  # noqa: BLE001 — 세기 실패가 적재를 막아선 안 된다
            counts = []
        over = sum(1 for n in counts if n is not None and n > limit)
    return vecs, over


def _fmt_eta(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


async def fill_table(
    s: Slot,
    table: str,
    *,
    record_ids: Sequence[str] | None = None,
    limit: int | None = None,
    chunk: int = DEFAULT_CHUNK,
    batch: int | None = None,
    engine=None,
    progress: Callable[[int, int], None] | None = None,
    log_every_s: float = 30.0,
) -> dict[str, Any]:
    """그 슬롯의 그 표에서 빈 행을 채운다. 결과는 세어 돌려준다(잠겨 있으면 ``locked: True``).

    ``record_ids`` 를 주면 그 레코드의 절 · 시그니처만(표본 · 도구 표는 건너뛴다). ``limit`` 은 이번 실행에서 볼
    행 수 상한(빈 글 표시 포함)이다. ``progress(done, total)`` 은 청크마다 부른다.
    """
    from ..db import base as db_base

    engine = engine if engine is not None else db_base.engine
    spec = _SPEC_BUILDERS[table]()
    mdl = _model_for(table)
    cols = s.cols(table)
    out: dict[str, Any] = {
        "slot": s.name, "table": table, "processed": 0, "skipped_empty": 0, "failed": 0,
        "over_model_limit": 0, "chunks": 0, "locked": False, "model": None, "model_max_tokens": None,
        "backlog_at_start": None, "seconds": 0.0,
    }
    if record_ids is not None and spec.record_filter is None:
        return out
    ids = list(record_ids) if record_ids is not None else None
    if ids is not None and not ids:
        return out

    t0 = time.monotonic()
    # 레코드 범위(적재 직후 자동 임베딩 · POST /api/jobs/embed record_ids)는 잠금을 잡지 않는다 — 몇 행뿐이고, 표 전체를
    # 도는 러너(몇 시간짜리 CLI 백필 포함)가 쥐고 있어도 새 레코드는 곧바로 찾혀야 한다. 겹쳐도 쓰기는 '아직 빈 행'
    # 조건이라 한 번만 들어간다.
    lock = _hold(engine, s.name, table) if ids is None else _no_lock()
    async with lock as got:
        if not got:
            out["locked"] = True
            log.info("embed backfill %s/%s: 다른 러너가 채우는 중이라 건너뛴다(advisory lock)", s.name, table)
            return out

        from sqlalchemy.ext.asyncio import async_sessionmaker

        sm = async_sessionmaker(engine, expire_on_commit=False)
        pk_attr = getattr(mdl, spec.pk)
        cond = backlog_condition(s, table)
        if ids is not None:
            cond = and_(cond, spec.record_filter(mdl, ids))
        async with sm() as session:
            total = int((await session.execute(select(func.count()).select_from(mdl).where(cond))).scalar() or 0)
        out["backlog_at_start"] = total
        if limit:
            total = min(total, int(limit))
        if total == 0:
            return out

        tbl = mdl.__table__
        # 레코드를 고쳐 써도 updated_at 은 그대로 둔다 — 열의 onupdate(now())가 끼어들지 않게 자기 값으로 둔다.
        keep = {c.name: c for c in tbl.columns if c.onupdate is not None}
        vec_col = tbl.c[cols.vec]
        guard = vec_col.is_(None)
        fill_values: dict[str, Any] = {cols.vec: bindparam("b_vec", type_=vec_col.type), **keep}
        empty_values: dict[str, Any] = dict(keep)
        if cols.at:
            fill_values[cols.at] = bindparam("b_at")
            empty_values[cols.at] = bindparam("b_at")
            guard = and_(guard, tbl.c[cols.at].is_(None))
        if cols.model:
            fill_values[cols.model] = bindparam("b_model")
            empty_values[cols.model] = bindparam("b_model")
        pk_col = tbl.c[spec.pk]
        fill_stmt = update(tbl).where(pk_col == bindparam("b_pk")).where(guard).values(fill_values)
        empty_stmt = update(tbl).where(pk_col == bindparam("b_pk")).where(guard).values(empty_values)

        embedder = None
        last = None
        seen = 0
        last_log = t0
        while True:
            want = chunk if not limit else min(chunk, int(limit) - seen)
            if want <= 0:
                break
            stmt = select(pk_attr, *spec.columns(mdl)).where(cond)
            if last is not None:
                stmt = stmt.where(pk_attr > last)
            stmt = stmt.order_by(pk_attr).limit(want)
            async with sm() as session:
                rows = (await session.execute(stmt)).all()
            if not rows:
                break
            last = rows[-1][0]
            seen += len(rows)
            out["chunks"] += 1

            empties: list[Any] = []
            todo: list[tuple[Any, str]] = []
            for row in rows:
                body = spec.text(row) or ""
                if body.strip():
                    todo.append((row[0], body[:_TEXT_CAP]))
                else:
                    empties.append(row[0])

            now = datetime.now(UTC)
            vecs: list[list[float]] | None = None
            if todo:
                try:
                    if embedder is None:
                        # 첫 로드는 수 초다 — 이벤트 루프를 막지 않게 스레드로.
                        embedder = await asyncio.to_thread(embed_slots.embedder, s)
                        out["model"] = embedder.name
                        out["model_max_tokens"] = embedder.input_limit()
                    vecs, over = await asyncio.to_thread(_encode_chunk, embedder, [t for _, t in todo], batch)
                    out["over_model_limit"] += over
                except Exception as exc:  # noqa: BLE001 — 청크 하나의 실패가 실행 전체를 멈추지 않게
                    out["failed"] += len(todo)
                    log.warning("embed backfill %s/%s: 청크 인코딩 실패(%d행, 키 %s..%s) — 건너뛴다: %s",
                                s.name, table, len(todo), todo[0][0], todo[-1][0], exc)
                    if embedder is None:
                        raise  # 모델을 못 올렸다 — 이 실행은 더 할 수 있는 것이 없다
            try:
                async with sm() as session:
                    if engine.dialect.name == "postgresql":
                        # records 의 BEFORE UPDATE 트리거가 updated_at 을 덮지 않게(이행 0032) — 이 트랜잭션에만.
                        await session.execute(text("SET LOCAL aidh.keep_updated_at = 'on'"))
                    if empties and cols.at:
                        await session.execute(
                            empty_stmt,
                            [{"b_pk": pk, "b_at": now, "b_model": SKIPPED_EMPTY} for pk in empties],
                        )
                    if vecs is not None:
                        await session.execute(
                            fill_stmt,
                            [{"b_pk": pk, "b_vec": v, "b_at": now, "b_model": out["model"]}
                             for (pk, _), v in zip(todo, vecs, strict=True)],
                        )
                    await session.commit()
                if empties and cols.at:
                    out["skipped_empty"] += len(empties)
                if vecs is not None:
                    out["processed"] += len(vecs)
            except Exception as exc:  # noqa: BLE001
                if vecs is not None:
                    out["failed"] += len(vecs)
                log.warning("embed backfill %s/%s: 청크 쓰기 실패(키 %s..%s) — 건너뛴다: %s",
                            s.name, table, rows[0][0], last, exc)

            done = out["processed"] + out["skipped_empty"] + out["failed"]
            if progress is not None:
                progress(done, total)
            tick = time.monotonic()
            if tick - last_log >= log_every_s:
                last_log = tick
                rate = out["processed"] / max(tick - t0, 1e-6)
                left = max(total - done, 0)
                log.info("embed backfill %s/%s: %d/%d (%.1f%%) · %.1f행/초 · 남은 %s · 실패 %d",
                         s.name, table, done, total, 100.0 * done / max(total, 1), rate,
                         _fmt_eta(left / rate) if rate > 0 else "?", out["failed"])

    out["seconds"] = round(time.monotonic() - t0, 2)
    if out["processed"] or out["skipped_empty"] or out["failed"]:
        log.info("embed backfill %s/%s 끝: 채움 %d · 빈 글 %d · 실패 %d · 한도 넘침 %d · %.1f초(%.1f행/초)",
                 s.name, table, out["processed"], out["skipped_empty"], out["failed"],
                 out["over_model_limit"], out["seconds"], out["processed"] / max(out["seconds"], 1e-6))
    return out


async def run(
    slots: Sequence[Slot] | None = None,
    tables: Sequence[str] | None = None,
    **kw: Any,
) -> dict[str, dict[str, dict[str, Any]]]:
    """슬롯 × 표를 차례로 채운다(작은 표 먼저, 절은 마지막). 결과는 ``{slot: {table: stats}}``."""
    slots = list(slots) if slots is not None else embed_slots.fill()
    tables = list(tables) if tables is not None else list(embed_slots.TABLES)
    unknown = [t for t in tables if t not in _SPEC_BUILDERS]
    if unknown:
        raise ValueError(f"모르는 표 {unknown} — {list(embed_slots.TABLES)} 중에서")
    order = [t for t in embed_slots.TABLES if t in tables]
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for s in slots:
        for table in order:
            try:
                out.setdefault(s.name, {})[table] = await fill_table(s, table, **kw)
            except Exception as exc:  # noqa: BLE001 — 한 슬롯 · 표의 실패(모델 없음 등)가 나머지를 막지 않게
                log.exception("embed backfill %s/%s 실패", s.name, table)
                out.setdefault(s.name, {})[table] = {"slot": s.name, "table": table,
                                                     "error": f"{type(exc).__name__}: {exc}"}
    return out


async def backlog(session, slots: Sequence[Slot] | None = None,
                  tables: Sequence[str] | None = None) -> dict[str, dict[str, int]]:
    """슬롯 · 표마다 남은 행 수(읽기만)."""
    slots = list(slots) if slots is not None else embed_slots.fill()
    tables = list(tables) if tables is not None else list(embed_slots.TABLES)
    out: dict[str, dict[str, int]] = {}
    for s in slots:
        for table in tables:
            mdl = _model_for(table)
            n = (await session.execute(
                select(func.count()).select_from(mdl).where(backlog_condition(s, table)))).scalar()
            out.setdefault(s.name, {})[table] = int(n or 0)
    return out


__all__ = ["DEFAULT_CHUNK", "SKIPPED_EMPTY", "backlog", "backlog_condition", "fill_table", "lock_key", "run"]
