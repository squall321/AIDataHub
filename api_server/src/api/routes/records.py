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

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import Principal, require_api_key
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
    team: str | None = Query(None),
    group: str | None = Query(None),
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
        "list_records: data_type=%s team=%s group=%s year=%s "
        "agents=%s tags=%s q=%s include_deleted=%s limit=%s offset=%s",
        data_type, team, group, year, agent, tag, q, include_deleted,
        limit, offset,
    )

    stmt = select(Record)
    if data_type:
        stmt = stmt.where(Record.data_type == data_type)
    if team:
        stmt = stmt.where(Record.team == team)
    if group:
        stmt = stmt.where(Record.group == group)
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
    # Strict team/group 검증 (Migration 0012 / org_svc)
    from api.services.org_svc import validate_team_group
    await validate_team_group(session, payload.team, payload.group)

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
    # 재부모화 시 depth 재계산 (Migration 0017). suggest-parent 확인 후
    # parent_record_id 를 PATCH 하는 표준 흐름을 지원한다.
    if "parent_record_id" in data:
        from api.ingest.db_writer import compute_depth

        rec.depth = await compute_depth(session, rec.parent_record_id)
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
# Suggest parent (포맷 유사 campaign 추천 — 사람 확인용)
# ---------------------------------------------------------------------------
@router.get("/{record_id}/suggest-parent")
async def suggest_parent(
    record_id: str,
    top_k: int = Query(5, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """이 record 와 포맷이 유사했던 부모(campaign) 후보를 점수순으로 제안.

    결정론적 휴리스틱 (doc_type/team-group/data_type/섹션구조/태그).
    사람이 확인 후 ``PATCH /api/records/{id}`` 의 ``parent_record_id`` 로 연결.
    """
    from api.services import parent_suggest_svc

    try:
        return await parent_suggest_svc.suggest_parents(
            session, record_id=record_id, top_k=top_k
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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


# ---------------------------------------------------------------------------
# POST /api/records/import — JSON 일괄 임포트 (auto_seq + UPSERT + dry_run)
# ---------------------------------------------------------------------------
async def _import_one(
    session: AsyncSession,
    *,
    raw: dict[str, Any],
    auto_seq: bool,
    dry_run: bool,
    actor: str,
    request_id: str | None,
    external_source: str | None = None,
) -> dict[str, Any]:
    """단일 record dict 를 import. INSERT 또는 UPSERT.

    ``external_source`` 가 지정되고 record dict 에 ``_external_id`` 키가 있으면
    ``external_id_map`` 을 통해 기존 record_id 로 UPSERT 한다.

    Returns:
        {"id", "action": "inserted|updated|skipped|dry_run", "warnings": [...]}
        또는 실패 시 {"error": str, "input_title": ...}
    """
    from api.db.models import ExternalIdMap
    from api.schemas.id_format import format_id, parse_id
    from api.services.seq import next_seq

    warnings: list[str] = []
    if not isinstance(raw, dict):
        return {"error": "record must be a JSON object", "input": str(raw)[:80]}

    rec = dict(raw)  # 변경 가능 copy

    # ----- 외부 ID 매핑 (push/pull 양방향에서 사용) -----
    external_id = rec.pop("_external_id", None)
    if external_source and external_id in (0, "", False):
        warnings.append(
            f"falsy _external_id {external_id!r} ignored — provide non-empty string"
        )
        external_id = None
    mapped_record_id: str | None = None
    user_supplied_id = rec.get("id")
    if external_source and external_id:
        try:
            # 매핑 + record.deleted_at 함께 확인 — soft-delete 된 record 는
            # 외부 sync 의 의도치 않은 부활을 막고 명시적 restore 를 요구한다.
            from sqlalchemy import and_

            row = (
                await session.execute(
                    select(ExternalIdMap.record_id, Record.deleted_at)
                    .join(Record, Record.id == ExternalIdMap.record_id, isouter=True)
                    .where(
                        and_(
                            ExternalIdMap.source == external_source,
                            ExternalIdMap.external_id == str(external_id),
                        )
                    )
                )
            ).first()
            if row is not None:
                existing_id, existing_deleted = row
                if existing_deleted is not None:
                    return {
                        "id": existing_id,
                        "error": (
                            f"refusing to UPSERT into soft-deleted record "
                            f"(external_id={external_id}, source={external_source}). "
                            "Call POST /api/records/{id}/restore first."
                        ),
                        "external_id": external_id,
                    }
                mapped_record_id = existing_id
                # 사용자가 명시한 id 와 매핑된 id 가 다르면 명시적으로 경고.
                if user_supplied_id and user_supplied_id != existing_id:
                    warnings.append(
                        f"explicit id {user_supplied_id!r} overridden by external_id_map → {existing_id!r}"
                    )
                rec["id"] = existing_id  # 기존 매핑 record 로 UPSERT
        except Exception as exc:  # pragma: no cover
            warnings.append(f"external_id lookup failed: {exc}")

    # 1) id 채번 (auto_seq)
    if not rec.get("id"):
        if not auto_seq:
            return {
                "error": "id missing (set auto_seq=true to auto-generate)",
                "input_title": rec.get("title"),
            }
        for k in ("data_type", "team", "group", "year"):
            if not rec.get(k):
                return {
                    "error": f"auto_seq needs '{k}' (along with data_type/team/group/year)",
                    "input_title": rec.get("title"),
                }
        seq = await next_seq(
            session,
            data_type=str(rec["data_type"]).upper(),
            team=str(rec["team"]).upper(),
            group=str(rec["group"]).upper(),
            year=int(rec["year"]),
        )
        rec["data_type"] = str(rec["data_type"]).upper()
        rec["team"] = str(rec["team"]).upper()
        rec["group"] = str(rec["group"]).upper()
        rec["year"] = int(rec["year"])
        rec["seq"] = seq
        rec["id"] = format_id(
            rec["data_type"], rec["team"], rec["group"], rec["year"], seq
        )
    else:
        # id 가 주어졌으면 거기서 파트를 파싱해 다른 필드와 동기화
        try:
            parts = parse_id(rec["id"])
        except ValueError as exc:
            return {"error": f"invalid id: {exc}", "input_title": rec.get("title")}
        for k, v in parts.items():
            existing = rec.get(k)
            if existing is None or existing == "":
                rec[k] = v
            elif (
                str(existing).upper() != str(v).upper()
                if isinstance(v, str)
                else int(existing) != int(v)
            ):
                warnings.append(
                    f"{k}={existing!r} from body overridden by id parts ({v!r})"
                )
                rec[k] = v

    # 2) 필수 검증
    if not rec.get("title"):
        return {"error": "title is required", "input_id": rec.get("id")}
    if rec.get("content") is None:
        rec["content"] = {}

    # 3) dry_run 이면 정규화 결과만 리포트
    if dry_run:
        existing = (
            await session.execute(select(Record.id).where(Record.id == rec["id"]))
        ).scalar_one_or_none()
        return {
            "id": rec["id"],
            "action": "dry_run",
            "would": "update" if existing else "create",
            "warnings": warnings,
        }

    # 4) team/group strict 검증
    from api.services.org_svc import validate_team_group

    try:
        await validate_team_group(session, rec["team"], rec["group"])
    except HTTPException as exc:
        return {
            "id": rec.get("id"),
            "error": exc.detail if hasattr(exc, "detail") else str(exc),
        }

    # 5) RecordIn(full schema, common.py) 으로 변환 → write_record (audit log 까지 처리)
    from api.ingest.db_writer import write_record
    from api.schemas import RecordIn as FullRecordIn

    try:
        record_in = FullRecordIn(
            **{k: v for k, v in rec.items() if k in FullRecordIn.model_fields}
        )
    except Exception as exc:
        return {"id": rec.get("id"), "error": f"validation failed: {exc}"}

    try:
        result = await write_record(
            session, record_in, actor=actor, request_id=request_id
        )

        # 외부 ID 매핑 등록 (신규 매핑 시 — 기존 매핑은 lookup 단계에서 해결).
        if external_source and external_id and mapped_record_id is None:
            session.add(
                ExternalIdMap(
                    source=external_source,
                    external_id=str(external_id),
                    record_id=result.record.id,
                )
            )

        await session.commit()
        # commit 성공 후에만 embed schedule (ghost 잡 방지)
        if getattr(result, "should_embed", False):
            try:
                from api.services.jobs import maybe_schedule_auto_embed

                maybe_schedule_auto_embed(result.record.id)
            except Exception as exc:  # noqa: BLE001
                log.debug("auto-embed schedule skipped post-commit: %s", exc)
        return {
            "id": result.record.id,
            "action": result.action,  # 'inserted' / 'updated' / 'skipped'
            "external_id": external_id if external_source else None,
            "warnings": warnings,
        }
    except IntegrityError as exc:
        await session.rollback()
        # Race: 다른 parallel import 가 같은 (source, external_id) 를 먼저 등록한 경우
        # — 재조회해서 기존 record 로 UPSERT 재시도.
        orig_msg = str(getattr(exc, "orig", exc))
        if external_source and external_id and "external_id_map" in orig_msg:
            try:
                existing_id = (
                    await session.execute(
                        select(ExternalIdMap.record_id).where(
                            (ExternalIdMap.source == external_source)
                            & (ExternalIdMap.external_id == str(external_id))
                        )
                    )
                ).scalar_one_or_none()
                if existing_id:
                    # 기존 record 로 UPSERT 재시도 (한 번만 — 무한루프 방지).
                    rec["id"] = existing_id
                    from api.schemas import RecordIn as FullRecordIn2

                    retry_in = FullRecordIn2(
                        **{k: v for k, v in rec.items() if k in FullRecordIn2.model_fields}
                    )
                    retry_result = await write_record(
                        session, retry_in, actor=actor, request_id=request_id
                    )
                    await session.commit()
                    # commit 성공 후 embed schedule
                    if getattr(retry_result, "should_embed", False):
                        try:
                            from api.services.jobs import maybe_schedule_auto_embed

                            maybe_schedule_auto_embed(retry_result.record.id)
                        except Exception as exc2_embed:  # noqa: BLE001
                            log.debug("auto-embed schedule skipped post-commit (race retry): %s", exc2_embed)
                    return {
                        "id": retry_result.record.id,
                        "action": retry_result.action,
                        "external_id": external_id,
                        "warnings": warnings + ["race_resolved: external_id was concurrently mapped"],
                    }
            except Exception as exc2:  # pragma: no cover
                await session.rollback()
                return {"id": rec.get("id"), "error": f"race retry failed: {exc2}"}
        return {"id": rec.get("id"), "error": f"integrity error: {orig_msg}"}
    except Exception as exc:
        await session.rollback()
        return {"id": rec.get("id"), "error": f"write failed: {exc}"}


@router.post("/import", summary="JSON 일괄 임포트 (auto_seq + UPSERT + dry_run + external_id 매핑)")
async def import_records(
    request: Request,
    payload: Any = Body(..., description="record dict / list / {records:[...]} wrapped"),
    auto_seq: bool = Query(
        False, description="True 면 id 없을 때 서버가 (data_type,team,group,year) 로 seq 자동 부여"
    ),
    dry_run: bool = Query(
        False, description="True 면 저장하지 않고 검증/정규화 결과만 반환"
    ),
    external_source: str | None = Query(
        None,
        description=(
            "외부 시스템 식별자 (e.g. 'signalforge', 'mxwp'). 지정 시 각 record 의 "
            "`_external_id` 키가 external_id_map 에 등록되어 후속 sync 시 동일 외부 ID "
            "는 같은 record 로 UPSERT 된다."
        ),
    ),
    session: AsyncSession = Depends(get_session),
    _principal: Principal = Depends(require_api_key),
) -> dict[str, Any]:
    """LLM 이 만든 규격 JSON 을 한 번에 일괄 등록한다.

    Body 형태 3가지 모두 허용:
        1. **단일 record**: ``{title: "...", content: {...}, ...}``
        2. **배열**: ``[{...}, {...}]``
        3. **wrapped**: ``{auto_seq: true, dry_run: false, records: [...]}``
            wrapped 형태의 ``auto_seq``/``dry_run`` 은 쿼리스트링보다 우선.

    동작:
        - ``id`` 가 있으면 UPSERT (기존 record 는 PATCH 처리, audit_log 기록).
        - ``id`` 가 없으면 ``auto_seq=true`` 일 때만 자동 채번 (안전).
        - 한 record 가 실패해도 다른 record 는 계속 처리 (best-effort).
        - 마지막에 ``ok / failed / warnings`` 카운트와 per-row 결과 반환.
    """
    # ----- body normalization -----
    if isinstance(payload, dict) and "records" in payload:
        records_in = payload.get("records") or []
        # body 의 옵션이 query param 보다 우선
        if "auto_seq" in payload:
            auto_seq = bool(payload["auto_seq"])
        if "dry_run" in payload:
            dry_run = bool(payload["dry_run"])
        if "external_source" in payload:
            external_source = str(payload["external_source"]) or None
    elif isinstance(payload, list):
        records_in = payload
    elif isinstance(payload, dict):
        # 단일 record dict 로 간주
        records_in = [payload]
    else:
        raise HTTPException(
            status_code=400,
            detail="body must be a record dict, a list of records, or {records:[...]}",
        )

    if not records_in:
        raise HTTPException(status_code=400, detail="records is empty")
    if len(records_in) > 1000:
        raise HTTPException(
            status_code=413,
            detail=f"too many records in one import: {len(records_in)} (max 1000)",
        )

    actor = _principal_name(request)
    rid = _request_id(request)

    results: list[dict[str, Any]] = []
    ok = 0
    failed = 0
    warn_total = 0
    for raw in records_in:
        outcome = await _import_one(
            session,
            raw=raw,
            auto_seq=auto_seq,
            dry_run=dry_run,
            actor=actor,
            request_id=rid,
            external_source=external_source,
        )
        results.append(outcome)
        if outcome.get("error"):
            failed += 1
        else:
            ok += 1
        warn_total += len(outcome.get("warnings", []) or [])

    log.info(
        "import_records: count=%s ok=%s failed=%s warnings=%s auto_seq=%s dry_run=%s source=%s",
        len(records_in), ok, failed, warn_total, auto_seq, dry_run, external_source,
    )
    return {
        "count": len(records_in),
        "ok": ok,
        "failed": failed,
        "warnings": warn_total,
        "auto_seq": auto_seq,
        "dry_run": dry_run,
        "external_source": external_source,
        "results": results,
    }


__all__ = ["router"]
