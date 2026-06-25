# DATA 표 집계 계산 — REST 라우트와 MCP 도구가 공유하는 순수 집계 수학.
"""DATA 레코드(headers/rows)의 통계 집계 (avg/max/min/sum/count + group_by).

버그나기 쉬운 집계 수학을 한 곳에 둔다. 헤더/행 추출은 호출자가 하고
(route 는 _extract_* 헬퍼, MCP 는 content 직접 접근), 이 순수 함수가 계산만
담당한다 — 입력 오류는 ValueError 로 던져 호출자가 HTTP 422 또는 에러봉투로 변환.
"""
from __future__ import annotations

from typing import Any

AGGREGATE_OPS = ("avg", "max", "min", "sum", "count")


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


def _agg(op: str, values: list[Any]) -> Any:
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
    return None  # unreachable (op 검증은 호출 전)


def aggregate(
    *,
    headers: list[str],
    units: list[Any] | None,
    rows: list[list[Any]],
    op: str,
    column: str | None = None,
    group_by: str | None = None,
    where: str | None = None,
) -> dict[str, Any]:
    """집계 결과 dict. 입력 오류는 ValueError.

    op != count 면 column 필수. where 는 'col:val' 사전필터.
    """
    if op not in AGGREGATE_OPS:
        raise ValueError(f"op must be one of {AGGREGATE_OPS}, got {op!r}")
    units = units or []
    if op != "count" and not column:
        raise ValueError(f"op={op} requires 'column'")
    if column and column not in headers:
        raise ValueError(f"unknown column {column!r} (available: {headers})")
    if group_by and group_by not in headers:
        raise ValueError(f"unknown group_by {group_by!r} (available: {headers})")

    # where 사전필터
    if where:
        if ":" not in where:
            raise ValueError("where must be 'column:value'")
        wcol, wval = (s.strip() for s in where.split(":", 1))
        if wcol not in headers:
            raise ValueError(f"unknown where column {wcol!r}")
        widx = headers.index(wcol)
        rows = [r for r in rows if widx < len(r) and str(r[widx]) == wval]

    col_idx = headers.index(column) if column else None
    grp_idx = headers.index(group_by) if group_by else None
    unit = units[col_idx] if (col_idx is not None and col_idx < len(units)) else None

    # 비그룹
    if group_by is None:
        if col_idx is None:  # count 전체
            result = len(rows)
        else:
            values = [r[col_idx] for r in rows if col_idx < len(r)]
            result = _agg(op, values)
        return {
            "op": op, "column": column, "result": result,
            "unit": unit, "rows_considered": len(rows),
        }

    # 그룹별
    groups: dict[Any, list[Any]] = {}
    for r in rows:
        key = r[grp_idx] if (grp_idx is not None and grp_idx < len(r)) else None
        val = r[col_idx] if (col_idx is not None and col_idx < len(r)) else None
        groups.setdefault(key, []).append(val)

    metric_key = f"{op}_{column}" if column else op
    out_rows = [{group_by: k, metric_key: _agg(op, vals)} for k, vals in groups.items()]
    try:
        out_rows.sort(key=lambda r: (r[metric_key] is None, -(r[metric_key] or 0)))
    except TypeError:
        out_rows.sort(key=lambda r: str(r.get(group_by)))

    return {
        "op": op, "column": column, "group_by": group_by, "unit": unit,
        "result": out_rows, "groups": len(out_rows), "rows_considered": len(rows),
    }


__all__ = ["aggregate", "AGGREGATE_OPS"]
