"""``/api/records`` — 레코드 CRUD + 거버넌스 (audit/soft-delete/lineage/diff).

ARRAY 술어는 :mod:`api.services.sql_compat` 를 통해 방언 호환적으로 처리한다.
직접 ``op('@>')`` / ``op('&&')`` 를 호출하지 않는다.

Migration 0008 이후 추가된 거버넌스 엔드포인트:
    - ``GET    /api/records/{id}/lineage`` — 조상/자손 레코드 체인.
    - ``GET    /api/records/{id}/diff?from=...`` — 두 레코드 간 diff.
    - ``POST   /api/records/{id}/restore`` — soft-delete 복원.
    - ``DELETE /api/records/{id}?hard=true`` — 물리 삭제 (bootstrap 키 필요).

Soft delete 정책:
    - 기본 list/get 은 ``deleted_at IS NULL`` 만 반환.
    - ``?include_deleted=true`` 로 옵트인 가능.
    - 표준 ``DELETE`` 는 ``deleted_at = NOW()`` 만 세팅.
"""
from __future__ import annotations

import difflib
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import SessionLocal, get_session
from api.db.models import Record
from api.services.audit import compute_diff, log_action, record_snapshot
from api.services.sql_compat import (
    ArrayPredicate,
    array_contains,
    array_overlap,
    paginate_rows,
    summary_ilike,
)

from ._schemas import RecordIn, RecordListResponse, RecordOut, RecordPatch

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/records", tags=["records"])


def _principal_name(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        return "anonymous"
    return getattr(principal, "name", "anonymous") or "anonymous"


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


async def _bump_usage_in_session(
    session: AsyncSession, record_id: str
) -> None:
    """현재 세션에서 ``read_count`` 와 ``last_accessed_at`` 증가.

    트랜잭션 격리 안전한 짧은 UPDATE 를 수행한다. best-effort — 실패는
    로깅만 한다.
    """
    try:
        from sqlalchemy import update

        await session.execute(
            update(Record)
            .where(Record.id == record_id)
            .values(
                read_count=Record.read_count + 1,
                last_accessed_at=datetime.now(timezone.utc),
            )
        )
    except Exception as exc:  # pragma: no cover - best-effort
        log.debug("usage bump (in-session) failed for %s: %s", record_id, exc)


async def _bump_usage(record_id: str) -> None:
    """fire-and-forget: 별도 세션에서 ``read_count`` 증가.

    BackgroundTasks 에 등록되어 응답 후 수행된다. 운영(asyncpg) 환경에서는
    SessionLocal 가 메인 엔진과 동일하다.
    """
    try:
        async with SessionLocal() as session:
            await _bump_usage_in_session(session, record_id)
            await session.commit()
    except Exception as exc:  # pragma: no cover - best-effort
        log.debug("usage bump failed for %s: %s", record_id, exc)


@router.get(
    "",
    response_model=RecordListResponse,
    response_model_exclude_none=True,
)
@router.get(
    "/",
    response_model=RecordListResponse,
    response_model_exclude_none=True,
    include_in_schema=False,
)
async def list_records(
    data_type: str | None = Query(None),
    division: str | None = Query(None),
    team: str | None = Query(None),
    year: int | None = Query(None),
    agent: list[str] | None = Query(None),
    tag: list[str] | None = Query(None),
    q: str | None = Query(None, description="ILIKE on title + summary"),
    include_deleted: bool = Query(
        False, description="True 면 soft-deleted 레코드도 포함"
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> RecordListResponse:
    log.info(
        "list_records: data_type=%s division=%s team=%s year=%s "
        "agents=%s tags=%s q=%s include_deleted=%s limit=%s offset=%s",
        data_type, division, team, year, agent, tag, q, include_deleted,
        limit, offset,
    )

    stmt = select(Record)
    if data_type:
        stmt = stmt.where(Record.data_type == data_type)
    if division:
        stmt = stmt.where(Record.division == division)
    if team:
        stmt = stmt.where(Record.team == team)
    if year is not None:
        stmt = stmt.where(Record.year == year)
    if not include_deleted:
        stmt = stmt.where(Record.deleted_at.is_(None))

    py_predicates: list[ArrayPredicate] = []
    if agent:
        pred = array_overlap(Record.agents, agent, session)
        stmt = stmt.where(pred.where_clause)
        if pred.python_filter is not None:
            py_predicates.append(pred)
    if tag:
        pred = array_contains(Record.tags, tag, session)
        stmt = stmt.where(pred.where_clause)
        if pred.python_filter is not None:
            py_predicates.append(pred)
    if q:
        stmt = stmt.where(
            or_(summary_ilike(Record.title, q), summary_ilike(Record.summary, q))
        )

    stmt = stmt.order_by(Record.updated_at.desc(), Record.id.desc())

    rows, total = await paginate_rows(
        session,
        stmt,
        limit=limit,
        offset=offset,
        extra_python_predicates=py_predicates,
    )

    return RecordListResponse(
        items=[RecordOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{record_id}", response_model=RecordOut, response_model_exclude_none=True
)
async def get_record(
    record_id: str,
    request: Request,
    include_deleted: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> RecordOut:
    stmt = select(Record).where(Record.id == record_id)
    if not include_deleted:
        stmt = stmt.where(Record.deleted_at.is_(None))
    rec = (await session.execute(stmt)).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail=f"record not found: {record_id}")

    # ORM 객체 → dict 직렬화를 commit 전에 수행 (commit 후 expire 회피).
    out = RecordOut.model_validate(rec)

    # 사용량 통계: 현재 세션에서 in-place UPDATE 후 commit.
    # 트랜잭션 격리상 별도 세션은 SessionLocal 의존이라 테스트 환경에서
    # 다른 DB 를 가리킬 수 있어, 안전하게 같은 세션에서 처리한다.
    await _bump_usage_in_session(session, record_id)

    # 감사 로그: VIEW 이벤트 (best-effort).
    try:
        await log_action(
            session,
            action="VIEW",
            record_id=record_id,
            actor=_principal_name(request),
            request_id=_request_id(request),
        )
        await session.commit()
    except Exception:  # pragma: no cover
        await session.rollback()

    return out


@router.post(
    "",
    response_model=RecordOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/",
    response_model=RecordOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def create_record(
    payload: RecordIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RecordOut:
    data = payload.model_dump()
    rec = Record(**data)
    session.add(rec)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        log.info("create_record duplicate: %s", payload.id)
        raise HTTPException(
            status_code=409,
            detail=f"record already exists or violates constraint: {payload.id}",
        ) from exc
    await session.refresh(rec)

    # 감사 로그.
    try:
        await log_action(
            session,
            action="INSERT",
            record_id=rec.id,
            actor=_principal_name(request),
            request_id=_request_id(request),
            field_changes={"id": [None, rec.id]},
        )
        await session.commit()
    except Exception:  # pragma: no cover
        await session.rollback()

    return RecordOut.model_validate(rec)


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(
    record_id: str,
    request: Request,
    hard: bool = Query(False, description="True 시 물리 삭제 (bootstrap 키 필요)"),
    session: AsyncSession = Depends(get_session),
) -> None:
    rec = (
        await session.execute(select(Record).where(Record.id == record_id))
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail=f"record not found: {record_id}")

    actor = _principal_name(request)
    rid = _request_id(request)

    if hard:
        principal = getattr(request.state, "principal", None)
        if principal is None or not getattr(principal, "is_bootstrap", False):
            raise HTTPException(
                status_code=403,
                detail="hard delete requires bootstrap API key",
            )
        # 물리 삭제 + 감사 로그.
        await log_action(
            session,
            action="DELETE",
            record_id=record_id,
            actor=actor,
            request_id=rid,
            field_changes={"hard": [False, True]},
        )
        await session.delete(rec)
        await session.commit()
        return

    # soft delete (default).
    if rec.deleted_at is not None:
        # 이미 삭제됨 — 멱등 동작.
        return
    now = datetime.now(timezone.utc)
    pre = {"deleted_at": rec.deleted_at}
    rec.deleted_at = now
    post = {"deleted_at": rec.deleted_at}
    await log_action(
        session,
        action="DELETE",
        record_id=record_id,
        actor=actor,
        request_id=rid,
        field_changes=compute_diff(pre, post),
    )
    await session.commit()


@router.post(
    "/{record_id}/restore",
    response_model=RecordOut,
    response_model_exclude_none=True,
)
async def restore_record(
    record_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RecordOut:
    """soft-delete 된 레코드의 ``deleted_at`` 을 NULL 로 되돌린다."""
    rec = (
        await session.execute(select(Record).where(Record.id == record_id))
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail=f"record not found: {record_id}")
    if rec.deleted_at is None:
        # 이미 활성 — 멱등.
        return RecordOut.model_validate(rec)

    pre = {"deleted_at": rec.deleted_at}
    rec.deleted_at = None
    post = {"deleted_at": None}
    await log_action(
        session,
        action="RESTORE",
        record_id=record_id,
        actor=_principal_name(request),
        request_id=_request_id(request),
        field_changes=compute_diff(pre, post),
    )
    await session.commit()
    await session.refresh(rec)
    return RecordOut.model_validate(rec)


@router.patch(
    "/{record_id}", response_model=RecordOut, response_model_exclude_none=True
)
async def patch_record(
    record_id: str,
    patch: RecordPatch,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RecordOut:
    rec = (
        await session.execute(select(Record).where(Record.id == record_id))
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail=f"record not found: {record_id}")

    pre_snap = record_snapshot(rec)

    data = patch.model_dump(exclude_unset=True)
    for key, value in data.items():
        if value is None and key != "project":
            # ``project`` 만 명시적 None 허용 (nullable 컬럼)
            continue
        setattr(rec, key, value)
    post_snap = record_snapshot(rec)

    diff = compute_diff(pre_snap, post_snap)
    if diff:
        await log_action(
            session,
            action="UPDATE",
            record_id=record_id,
            actor=_principal_name(request),
            request_id=_request_id(request),
            field_changes=diff,
        )

    await session.commit()
    await session.refresh(rec)
    return RecordOut.model_validate(rec)


# ---------------------------------------------------------------------------
# Lineage (G3)
# ---------------------------------------------------------------------------
@router.get("/{record_id}/lineage")
async def lineage(
    record_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """레코드 계보 (조상 + 자손) 반환.

    ``parent_record_id`` 의 self-FK 를 따라 위/아래로 한 번씩 BFS 한다.
    순환 방지를 위해 방문 집합을 추적한다.
    """
    rec = (
        await session.execute(select(Record).where(Record.id == record_id))
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail=f"record not found: {record_id}")

    # ancestors: parent → grandparent → ...
    ancestors: list[dict[str, Any]] = []
    visited: set[str] = {rec.id}
    cursor: str | None = rec.parent_record_id
    while cursor and cursor not in visited:
        visited.add(cursor)
        parent = (
            await session.execute(select(Record).where(Record.id == cursor))
        ).scalar_one_or_none()
        if parent is None:
            break
        ancestors.append(_lineage_node(parent))
        cursor = parent.parent_record_id

    # descendants: BFS 자식들.
    descendants: list[dict[str, Any]] = []
    queue: list[str] = [rec.id]
    seen_desc: set[str] = {rec.id}
    while queue:
        current = queue.pop(0)
        children = (
            (
                await session.execute(
                    select(Record).where(Record.parent_record_id == current)
                )
            )
            .scalars()
            .all()
        )
        for child in children:
            if child.id in seen_desc:
                continue
            seen_desc.add(child.id)
            descendants.append(_lineage_node(child))
            queue.append(child.id)

    return {
        "record_id": rec.id,
        "self": _lineage_node(rec),
        "ancestors": ancestors,
        "descendants": descendants,
        "ancestor_count": len(ancestors),
        "descendant_count": len(descendants),
    }


def _lineage_node(rec: Record) -> dict[str, Any]:
    return {
        "id": rec.id,
        "data_type": rec.data_type,
        "title": rec.title,
        "version": rec.version,
        "status": rec.status,
        "derivation": rec.derivation,
        "parent_record_id": rec.parent_record_id,
        "content_hash": rec.content_hash,
        "deleted_at": rec.deleted_at.isoformat() if rec.deleted_at else None,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
    }


# ---------------------------------------------------------------------------
# Diff (G4)
# ---------------------------------------------------------------------------
_DIFF_META_FIELDS = (
    "title",
    "summary",
    "tags",
    "agents",
    "version",
    "status",
    "classification",
    "domain",
    "subject_keywords",
    "language",
    "derivation",
    "capabilities",
    "quality_score",
    "valid_from",
    "valid_until",
    "content_hash",
)


@router.get("/{record_id}/diff")
async def diff_records(
    record_id: str,
    from_: str = Query(..., alias="from", description="비교 대상 record id"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """두 레코드의 메타/섹션을 비교한다.

    응답 형태:
        {
          "from": "...",
          "to": "...",
          "meta_changes": {field: [old, new], ...},
          "section_changes": [
              {"section_id": "...", "kind": "added|removed|modified",
               "title_changes": [old, new] | null,
               "content_diff": "<unified diff text>"}
          ],
          "block_changes": "summary"
        }
    """
    if record_id == from_:
        raise HTTPException(status_code=400, detail="from and target record ids must differ")

    to_rec = (
        await session.execute(select(Record).where(Record.id == record_id))
    ).scalar_one_or_none()
    from_rec = (
        await session.execute(select(Record).where(Record.id == from_))
    ).scalar_one_or_none()
    if to_rec is None:
        raise HTTPException(status_code=404, detail=f"record not found: {record_id}")
    if from_rec is None:
        raise HTTPException(status_code=404, detail=f"record not found: {from_}")

    # ---- meta diff ----
    old_snap = {f: getattr(from_rec, f, None) for f in _DIFF_META_FIELDS}
    new_snap = {f: getattr(to_rec, f, None) for f in _DIFF_META_FIELDS}
    meta_changes = compute_diff(old_snap, new_snap)

    # ---- section diff (paired by section_id) ----
    from api.db.models import RecordSection

    from_sections = (
        (
            await session.execute(
                select(RecordSection).where(RecordSection.record_id == from_rec.id)
            )
        )
        .scalars()
        .all()
    )
    to_sections = (
        (
            await session.execute(
                select(RecordSection).where(RecordSection.record_id == to_rec.id)
            )
        )
        .scalars()
        .all()
    )
    from_map = {s.section_id: s for s in from_sections}
    to_map = {s.section_id: s for s in to_sections}

    section_changes: list[dict[str, Any]] = []
    all_keys = sorted(set(from_map) | set(to_map))
    for sid in all_keys:
        a = from_map.get(sid)
        b = to_map.get(sid)
        if a is None and b is not None:
            section_changes.append(
                {
                    "section_id": sid,
                    "kind": "added",
                    "title_changes": [None, b.title],
                    "content_diff": _unified_diff("", b.content_text or "", sid),
                }
            )
        elif b is None and a is not None:
            section_changes.append(
                {
                    "section_id": sid,
                    "kind": "removed",
                    "title_changes": [a.title, None],
                    "content_diff": _unified_diff(a.content_text or "", "", sid),
                }
            )
        else:
            assert a is not None and b is not None
            title_changed = (a.title or "") != (b.title or "")
            content_changed = (a.content_text or "") != (b.content_text or "")
            if title_changed or content_changed:
                section_changes.append(
                    {
                        "section_id": sid,
                        "kind": "modified",
                        "title_changes": [a.title, b.title] if title_changed else None,
                        "content_diff": (
                            _unified_diff(
                                a.content_text or "", b.content_text or "", sid
                            )
                            if content_changed
                            else ""
                        ),
                    }
                )

    # block diff: 단순 summary (deep block diff 는 섹션 텍스트 diff 로 흡수).
    block_changes: str
    a_blocks = (from_rec.content or {}).get("sections")
    b_blocks = (to_rec.content or {}).get("sections")
    if a_blocks == b_blocks:
        block_changes = "identical"
    else:
        block_changes = "summary"

    return {
        "from": from_rec.id,
        "to": to_rec.id,
        "meta_changes": meta_changes,
        "section_changes": section_changes,
        "block_changes": block_changes,
    }


def _unified_diff(a: str, b: str, label: str) -> str:
    """줄 단위 unified diff 텍스트."""
    a_lines = (a or "").splitlines(keepends=True) or [""]
    b_lines = (b or "").splitlines(keepends=True) or [""]
    diff_lines = difflib.unified_diff(
        a_lines,
        b_lines,
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
        n=2,
    )
    return "".join(diff_lines)


__all__ = ["router"]
