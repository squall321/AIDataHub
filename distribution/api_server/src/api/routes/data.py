"""``/api/data`` — DATA 타입 전용 라우터 (+ Cline SR 코어 호환).

본 라우터는 두 가지 모드를 지원한다:

1. **DATA 카탈로그 모드** (Phase Faceted-DATA)
   - ``GET /api/data``                 — DATA 타입 record 카탈로그 (작은 AI 가
     어떤 데이터가 있는지 한눈에 본다). 다축 필터 (tags/domain/agent/
     min_rows) + limit/offset.
   - ``GET /api/data/{id}/rows``       — 한 DATA record 의 모든 행 (페이징 +
     간단한 컬럼=값 필터 ``where=Region:Yield``).
   - ``GET /api/data/{id}/columns``    — 컬럼 정의 (description/unit/dtype),
     ``column_descriptions`` / ``units`` / ``units_map`` / ``_GLOSSARY`` 시트
     중 사용 가능한 정보를 합성.
   - ``GET /api/data/{id}/aggregate``  — 간단한 통계 집계 (``avg|max|min|sum|
     count``) + optional ``group_by``.

2. **에이전트 모드** (Cline SR 호환, 기존 동작)
   - ``GET /api/data?agent=<x>&query=...&data_types=...&limit=...`` —
     ``agent`` 쿼리 파라미터가 *주어진 경우* 로 한정해 기존
     :func:`api.services.search_svc.data_for_agent` 로 위임한다.

설계 노트
---------
- ``agent`` 파라미터의 유무로 두 모드를 구분 (라우트 충돌 방지).
- ``data_type`` 컬럼이 ``"DATA"`` 인 record 만 카탈로그/rows/columns/aggregate
  대상이 된다. 다른 타입은 404.
- 행 단위 통계는 파이썬에서 계산 — 보통 한 record 당 수십~수천 행 규모이므로
  PG 측 집계가 필요 없다. (운영에서 큰 데이터셋이 등장하면 SQL 집계로 교체 가능.)
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import get_session
from api.db.models import Record
from api.services.search_svc import data_for_agent
from api.services.sql_compat import array_contains, paginate_rows

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data"])


# ---------------------------------------------------------------------------
# Helpers — DATA content payload 추출
# ---------------------------------------------------------------------------
def _content_dict(rec: Record) -> dict[str, Any]:
    c = rec.content or {}
    if not isinstance(c, dict):
        return {}
    return c


def _extract_headers(rec: Record) -> list[str]:
    c = _content_dict(rec)
    h = c.get("headers")
    if isinstance(h, list):
        return [str(x) for x in h]
    return []


def _extract_units(rec: Record) -> list[Any]:
    """``units`` 는 dict (``{col: unit}``) 또는 list 두 형태 모두 지원."""
    c = _content_dict(rec)
    u = c.get("units")
    headers = _extract_headers(rec)
    if isinstance(u, list):
        # 길이 맞춰 None 패딩.
        out = list(u) + [None] * max(0, len(headers) - len(u))
        return out[: len(headers)] if headers else list(u)
    if isinstance(u, dict):
        # units_map (column-name keyed) → header 순으로 정렬.
        units_map = u
    else:
        units_map = c.get("units_map") or {}
    if not headers:
        return []
    return [units_map.get(h) for h in headers]


def _extract_rows(rec: Record) -> list[list[Any]]:
    c = _content_dict(rec)
    r = c.get("rows")
    if isinstance(r, list):
        return [row if isinstance(row, list) else [row] for row in r]
    return []


def _extract_context(rec: Record) -> dict[str, Any]:
    c = _content_dict(rec)
    ctx = c.get("context")
    return ctx if isinstance(ctx, dict) else {}


def _extract_column_descriptions(rec: Record) -> dict[str, str]:
    """``column_descriptions`` 또는 ``_GLOSSARY`` 시트 형태에서 추출."""
    c = _content_dict(rec)
    cd = c.get("column_descriptions")
    if isinstance(cd, dict):
        return {str(k): str(v) for k, v in cd.items()}
    # _GLOSSARY 시트 형태 (rows: [[term, description], ...])
    gl = c.get("_GLOSSARY") or c.get("glossary")
    if isinstance(gl, dict):
        rows = gl.get("rows")
        if isinstance(rows, list):
            out: dict[str, str] = {}
            for row in rows:
                if isinstance(row, list) and len(row) >= 2 and row[0]:
                    out[str(row[0])] = str(row[1] if row[1] is not None else "")
            return out
    return {}


def _infer_dtype(values: list[Any]) -> str:
    """행에서 컬럼별 dtype 을 추정. enum / int / float / str / mixed."""
    seen = {type(v).__name__ for v in values if v is not None}
    if not seen:
        return "null"
    if seen == {"int"}:
        return "int"
    if seen <= {"int", "float"}:
        return "float"
    if seen == {"bool"}:
        return "bool"
    if seen == {"str"}:
        # enum 추정: 고유 값 <= 8 이면 enum
        uniq = {str(v) for v in values if v is not None}
        return "enum" if len(uniq) <= 8 else "str"
    return "mixed"


def _ensure_data_record(rec: Record | None) -> Record:
    if rec is None:
        raise HTTPException(status_code=404, detail="record not found")
    if rec.data_type != "DATA":
        raise HTTPException(
            status_code=404,
            detail=f"record {rec.id} is not DATA type (got {rec.data_type})",
        )
    return rec


async def _load_record(session: AsyncSession, record_id: str) -> Record | None:
    res = await session.execute(select(Record).where(Record.id == record_id))
    return res.scalars().unique().one_or_none()


# ---------------------------------------------------------------------------
# GET /api/data — agent 모드 (기존) OR DATA 카탈로그 모드 (신규)
# ---------------------------------------------------------------------------
@router.get("")
@router.get("/", include_in_schema=False)
async def get_data(
    background: BackgroundTasks,
    agent: str | None = Query(
        None,
        description=(
            "에이전트 식별자 (예: iga-analyst). 주어지면 Cline SR 호환 "
            "agent-scoped 응답 (results+relevance) 을 반환한다. "
            "없으면 DATA 타입 카탈로그 모드."
        ),
    ),
    query: str | None = Query(
        None, description="자연어 검색어 (agent 모드에서만 사용)"
    ),
    data_types: list[str] | None = Query(
        None,
        description="(agent 모드) data_type 화이트리스트",
    ),
    tags: str | None = Query(
        None, description="(카탈로그 모드) 콤마 구분 태그 필터 — AND"
    ),
    domain: str | None = Query(
        None, description="(카탈로그 모드) 도메인 필터 (예: material-test)"
    ),
    min_rows: int | None = Query(
        None, ge=0, description="(카탈로그 모드) 최소 행 수"
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """``agent`` 가 있으면 Cline SR 호환, 없으면 DATA 카탈로그."""
    log.info(
        "get_data: agent=%s tags=%s domain=%s min_rows=%s limit=%s offset=%s",
        agent, tags, domain, min_rows, limit, offset,
    )

    # ----- agent 모드 (기존) ---------------------------------------------
    if agent is not None:
        # legacy guardrails: limit 1..20.
        legacy_limit = max(1, min(int(limit), 20))
        payload = await data_for_agent(
            session,
            agent=agent,
            query=query,
            data_types=data_types,
            limit=legacy_limit,
        )
        try:
            from .records import _bump_usage  # local import to avoid cycle

            record_ids: list[str] = []
            for r in payload.get("results") or []:
                rid = r.get("record_id")
                if rid and rid not in record_ids:
                    record_ids.append(rid)
            for rid in record_ids:
                background.add_task(_bump_usage, rid)
        except Exception as exc:  # pragma: no cover
            log.debug("usage bump scheduling failed: %s", exc)
        return payload

    # ----- 카탈로그 모드 (신규) -------------------------------------------
    stmt = select(Record).where(Record.data_type == "DATA").where(
        Record.deleted_at.is_(None)
    )
    if domain:
        stmt = stmt.where(Record.domain == domain)
    pyfilters = []
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if tag_list:
            pred = array_contains(Record.tags, tag_list, session)
            stmt = stmt.where(pred.where_clause)
            if pred.python_filter is not None:
                pyfilters.append(pred)
    # agent 필터는 카탈로그 모드에서는 query param 그대로 받지 않으므로
    # tags 와 domain 만 단순 처리. (agent 는 모드 전환 트리거였음)

    stmt = stmt.order_by(Record.updated_at.desc(), Record.id.desc())

    # 페이징은 SQL 측 + min_rows 후필터.
    # min_rows 가 있으면 모두 가져와 파이썬에서 행 수 필터.
    rows: list[Record]
    if min_rows is not None or pyfilters:
        all_rows = (await session.execute(stmt)).scalars().unique().all()
        all_list = list(all_rows)
        for pred in pyfilters:
            all_list = pred.apply_python(all_list)
        if min_rows is not None:
            all_list = [r for r in all_list if len(_extract_rows(r)) >= min_rows]
        total = len(all_list)
        rows = all_list[offset : offset + limit]
    else:
        rows, total = await paginate_rows(
            session, stmt, limit=limit, offset=offset
        )

    items = []
    for rec in rows:
        c = _content_dict(rec)
        headers = _extract_headers(rec)
        items.append(
            {
                "id": rec.id,
                "title": rec.title,
                "domain": rec.domain,
                "tags": list(rec.tags or []),
                "rows": len(_extract_rows(rec)),
                "columns": headers,
                "units": _extract_units(rec),
                "context": _extract_context(rec),
                "summary": rec.summary or "",
                "classification": rec.classification,
                "status": rec.status,
            }
        )

    return {
        "total": total,
        "items": items,
        "limit": limit,
        "offset": offset,
        "filters": {
            "tags": tags,
            "domain": domain,
            "min_rows": min_rows,
        },
    }


# ---------------------------------------------------------------------------
# GET /api/data/{record_id}/rows
# ---------------------------------------------------------------------------
@router.get("/{record_id}/rows")
async def get_data_rows(
    record_id: str,
    limit: int = Query(100, ge=1, le=10_000),
    offset: int = Query(0, ge=0),
    where: str | None = Query(
        None,
        description=(
            "간단한 컬럼=값 필터 (단일 조건). 형식: ``column:value``. "
            "예: ``where=Region:Yield``. 값은 문자열 동등 비교."
        ),
    ),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """DATA record 의 행을 페이징해서 반환."""
    rec = _ensure_data_record(await _load_record(session, record_id))

    headers = _extract_headers(rec)
    units = _extract_units(rec)
    all_rows = _extract_rows(rec)

    # where 필터 적용
    where_col: str | None = None
    where_val: str | None = None
    if where:
        if ":" not in where:
            raise HTTPException(
                status_code=422,
                detail="where must be in 'column:value' format",
            )
        where_col, where_val = where.split(":", 1)
        where_col = where_col.strip()
        where_val = where_val.strip()
        if where_col not in headers:
            raise HTTPException(
                status_code=422,
                detail=f"unknown column {where_col!r} (available: {headers})",
            )
        col_idx = headers.index(where_col)
        all_rows = [
            r for r in all_rows
            if col_idx < len(r) and str(r[col_idx]) == where_val
        ]

    total = len(all_rows)
    page = all_rows[offset : offset + limit]

    return {
        "record_id": rec.id,
        "headers": headers,
        "units": units,
        "total_rows": total,
        "rows": page,
        "limit": limit,
        "offset": offset,
        "where": where,
    }


# ---------------------------------------------------------------------------
# GET /api/data/{record_id}/columns
# ---------------------------------------------------------------------------
@router.get("/{record_id}/columns")
async def get_data_columns(
    record_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """DATA record 의 컬럼 정의 (description/unit/dtype)."""
    rec = _ensure_data_record(await _load_record(session, record_id))

    headers = _extract_headers(rec)
    units = _extract_units(rec)
    descs = _extract_column_descriptions(rec)
    rows = _extract_rows(rec)

    items = []
    for i, name in enumerate(headers):
        col_values = [r[i] for r in rows if i < len(r)]
        unit = units[i] if i < len(units) else None
        items.append(
            {
                "column": name,
                "description": descs.get(name, ""),
                "unit": unit,
                "dtype": _infer_dtype(col_values),
            }
        )

    return {
        "record_id": rec.id,
        "items": items,
    }


# ---------------------------------------------------------------------------
# GET /api/data/{record_id}/aggregate
# ---------------------------------------------------------------------------
AggregateOp = Literal["avg", "max", "min", "sum", "count"]


@router.get("/{record_id}/aggregate")
async def get_data_aggregate(
    record_id: str,
    op: AggregateOp = Query(
        ..., description="집계 연산: avg|max|min|sum|count"
    ),
    column: str | None = Query(
        None,
        description=(
            "대상 컬럼 — op != count 일 때 필수. count 일 때는 무시되거나 "
            "그 컬럼의 non-null 카운트로 사용된다."
        ),
    ),
    group_by: str | None = Query(
        None, description="그룹화 컬럼 (옵션)"
    ),
    where: str | None = Query(
        None,
        description="간단한 컬럼=값 사전필터 (rows 와 동일한 형식).",
    ),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """간단한 통계 집계 — 작은 AI 가 직접 계산하지 않아도 되게."""
    rec = _ensure_data_record(await _load_record(session, record_id))

    headers = _extract_headers(rec)
    units = _extract_units(rec)
    rows = _extract_rows(rec)

    # column 검증
    if op != "count" and not column:
        raise HTTPException(
            status_code=422,
            detail=f"op={op} requires 'column' parameter",
        )
    if column and column not in headers:
        raise HTTPException(
            status_code=422,
            detail=f"unknown column {column!r} (available: {headers})",
        )
    if group_by and group_by not in headers:
        raise HTTPException(
            status_code=422,
            detail=f"unknown group_by column {group_by!r} (available: {headers})",
        )

    # where 사전필터
    if where:
        if ":" not in where:
            raise HTTPException(
                status_code=422,
                detail="where must be in 'column:value' format",
            )
        wcol, wval = where.split(":", 1)
        wcol, wval = wcol.strip(), wval.strip()
        if wcol not in headers:
            raise HTTPException(
                status_code=422,
                detail=f"unknown where column {wcol!r}",
            )
        widx = headers.index(wcol)
        rows = [r for r in rows if widx < len(r) and str(r[widx]) == wval]

    col_idx = headers.index(column) if column else None
    grp_idx = headers.index(group_by) if group_by else None

    def _to_number(v: Any) -> float | None:
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                return None
        return None

    def _agg(values: list[Any]) -> Any:
        if op == "count":
            return sum(1 for v in values if v is not None)
        nums = [n for v in values if (n := _to_number(v)) is not None]
        if not nums:
            return None
        if op == "avg":
            return round(sum(nums) / len(nums), 6)
        if op == "max":
            return max(nums)
        if op == "min":
            return min(nums)
        if op == "sum":
            return round(sum(nums), 6)
        return None  # unreachable

    unit = None
    if column and col_idx is not None and col_idx < len(units):
        unit = units[col_idx]

    if group_by is None:
        if op == "count":
            values = [
                (r[col_idx] if col_idx is not None and col_idx < len(r) else None)
                for r in rows
            ] if col_idx is not None else list(rows)
            result = sum(1 for r in rows if r is not None) if col_idx is None else _agg(values)
        else:
            values = [r[col_idx] for r in rows if col_idx is not None and col_idx < len(r)]
            result = _agg(values)
        return {
            "record_id": rec.id,
            "op": op,
            "column": column,
            "result": result,
            "unit": unit,
            "rows_considered": len(rows),
        }

    # group_by 모드
    groups: dict[Any, list[Any]] = {}
    for r in rows:
        if grp_idx is not None and grp_idx < len(r):
            key = r[grp_idx]
        else:
            key = None
        if col_idx is not None and col_idx < len(r):
            groups.setdefault(key, []).append(r[col_idx])
        else:
            groups.setdefault(key, []).append(None)

    metric_key = f"{op}_{column}" if column else op
    out_rows = []
    for k, vals in groups.items():
        out_rows.append({group_by: k, metric_key: _agg(vals)})

    # 결과 정렬: numeric metric 이면 desc, 아니면 group key 정렬.
    try:
        out_rows.sort(
            key=lambda r: (r[metric_key] is None, -(r[metric_key] or 0)),
        )
    except TypeError:
        out_rows.sort(key=lambda r: str(r.get(group_by)))

    return {
        "record_id": rec.id,
        "op": op,
        "column": column,
        "group_by": group_by,
        "unit": unit,
        "result": out_rows,
        "groups": len(out_rows),
        "rows_considered": len(rows),
    }


__all__ = ["router"]
