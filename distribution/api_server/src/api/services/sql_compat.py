"""SQL 방언(dialect) 호환 헬퍼.

PostgreSQL 전용 ARRAY/JSONB 연산자(`@>`, `&&`, `unnest`, `to_tsvector` 등)는
운영(PG)에서는 효율적이지만 단위 테스트(SQLite)에서는 컴파일 오류를 낸다.
이 모듈은 라우트/서비스 코드가 단일 API 로 양쪽 백엔드를 모두 지원하도록
한다.

전략
----
- ``array_contains`` / ``array_overlap`` :
    * PG → ``column.op("@>")`` / ``column.op("&&")``  (TEXT[] 바인드 명시 캐스트)
    * SQLite → 후처리(post-filter) 모드. 호출 측이 결과를 ``filter_in_python``
      플래그로 후필터링할 수 있도록 ``ArrayPredicate`` 객체를 반환한다.
      이 객체는 SQL WHERE 절에서는 ``true`` (no-op) 로 컴파일되고,
      ``apply_python(rows)`` 으로 파이썬 측에서 행 필터링한다.

- ``array_unnest_count`` :
    * PG → ``func.unnest(column)`` 후 GROUP BY count
    * SQLite → 컬럼 값을 select 로 가져와 파이썬 ``Counter`` 로 집계.

- ``summary_ilike`` : 양쪽 모두 ``column.ilike("%q%")`` 로 충분.

- ``fts_match`` : PG 는 ``to_tsvector / plainto_tsquery``, SQLite 는 ILIKE.

테스트 데이터셋이 작다는 가정 하에 SQLite 폴백은 단순/안전 우선으로
구현한다. 운영에서는 PG 경로만 사용된다.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, literal, or_, select, true
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement


# ---------------------------------------------------------------------------
# Dialect detection
# ---------------------------------------------------------------------------
def dialect_name(session: AsyncSession) -> str:
    """Return ``session`` 의 dialect 이름 ("postgresql" / "sqlite" / ...)."""
    bind = session.get_bind()
    name = getattr(bind, "dialect", None)
    if name is None and isinstance(bind, Engine):
        name = bind.dialect
    try:
        return str(name.name)
    except AttributeError:
        return ""


def is_postgres(session: AsyncSession) -> bool:
    return dialect_name(session) == "postgresql"


# ---------------------------------------------------------------------------
# Array predicates
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ArrayPredicate:
    """배열 술어(predicate) 의 백엔드 추상화.

    PG 경로에서는 ``where_clause`` 가 실제 SQL where 표현식이고
    ``python_filter`` 는 ``None`` 이다.

    SQLite 경로에서는 ``where_clause`` 가 ``true`` (no-op) 이고
    ``python_filter`` 가 행 단위 술어 함수다. 호출자는
    ``apply_python(rows)`` 으로 후필터링한다.
    """

    where_clause: ColumnElement[bool]
    python_filter: Any = None  # Callable[[Any], bool] | None
    column_attr: str | None = None

    def apply_python(self, rows: Iterable[Any]) -> list[Any]:
        if self.python_filter is None:
            return list(rows)
        return [r for r in rows if self.python_filter(r)]


def _attr_name(column: Any) -> str | None:
    """ORM 컬럼에서 속성 이름 추출 (``Record.tags`` → ``"tags"``)."""
    key = getattr(column, "key", None)
    if isinstance(key, str):
        return key
    return None


def array_contains(
    column: Any, values: Sequence[str], session: AsyncSession
) -> ArrayPredicate:
    """``column @> values`` (모든 원소를 포함).

    - PG : ``column.op("@>")`` (배열 리터럴은 ``literal(...)`` 으로 명시 바인드).
    - 기타 : 파이썬 후필터.
    """
    values = list(values)
    if not values:
        return ArrayPredicate(where_clause=true())

    if is_postgres(session):
        return ArrayPredicate(where_clause=column.op("@>")(values))

    attr = _attr_name(column)
    needed = set(values)

    def _filt(row: Any) -> bool:
        attrname = attr or "tags"
        cur = getattr(row, attrname, None) or []
        return needed.issubset(set(cur))

    return ArrayPredicate(
        where_clause=true(),
        python_filter=_filt,
        column_attr=attr,
    )


def array_overlap(
    column: Any, values: Sequence[str], session: AsyncSession
) -> ArrayPredicate:
    """``column && values`` (교집합 존재).

    - PG : ``column.op("&&")`` 으로 ARRAY 교집합.
    - 기타 : 파이썬 후필터.
    """
    values = list(values)
    if not values:
        # Empty overlap → 항상 거짓 (어디서도 매치 못함)
        return ArrayPredicate(
            where_clause=literal(False),
            python_filter=lambda _r: False,
        )

    if is_postgres(session):
        return ArrayPredicate(where_clause=column.op("&&")(values))

    attr = _attr_name(column)
    needed = set(values)

    def _filt(row: Any) -> bool:
        attrname = attr or "agents"
        cur = getattr(row, attrname, None) or []
        return bool(needed.intersection(set(cur)))

    return ArrayPredicate(
        where_clause=true(),
        python_filter=_filt,
        column_attr=attr,
    )


# ---------------------------------------------------------------------------
# Convenience: combine predicate with a base statement
# ---------------------------------------------------------------------------
async def execute_with_array_filter(
    session: AsyncSession,
    stmt: Select,
    predicates: Sequence[ArrayPredicate],
) -> list[Any]:
    """``stmt`` 를 실행하고, SQLite 폴백이 필요한 ``predicates`` 를 파이썬에서 후필터.

    PG 에서는 모든 술어가 SQL 측에서 처리되었으므로 ``python_filter`` 가 모두
    ``None`` 이며 후필터 패스는 no-op 이다.
    """
    rows = (await session.execute(stmt)).scalars().unique().all()
    out = list(rows)
    for pred in predicates:
        out = pred.apply_python(out)
    return out


# ---------------------------------------------------------------------------
# Unnest count (analytics)
# ---------------------------------------------------------------------------
async def array_unnest_count(
    session: AsyncSession,
    column: Any,
    *,
    where_clauses: Sequence[ColumnElement[bool]] = (),
    python_predicates: Sequence[ArrayPredicate] = (),
    limit: int | None = None,
) -> list[tuple[str, int]]:
    """``column`` (text[]) 의 원소별 카운트를 (tag, count) 정렬 리스트로 반환.

    - PG → ``select(unnest(column), count()).group_by(unnest)`` 한 번의 쿼리.
    - SQLite → 컬럼 값을 모두 가져와 파이썬 ``Counter`` 로 집계.
    - ``python_predicates`` 가 있으면 SQLite 경로에서 행 단위 후필터 후 집계.
    """
    if is_postgres(session) and not python_predicates:
        unnested = func.unnest(column).label("element")
        stmt = select(unnested, func.count().label("count"))
        for w in where_clauses:
            stmt = stmt.where(w)
        stmt = stmt.group_by(unnested).order_by(func.count().desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await session.execute(stmt)).all()
        return [(str(t), int(c)) for t, c in rows if t is not None]

    # 폴백: row 페치 후 파이썬 집계
    # column 이 ORM-mapped 면 그 owner 클래스 자체를 select 해야 python_predicates
    # 의 row attribute lookup 이 동작한다.
    parent = getattr(column, "parent", None)
    parent_cls = getattr(parent, "class_", None)
    attr = _attr_name(column) or "tags"

    if parent_cls is not None:
        row_stmt = select(parent_cls)
        for w in where_clauses:
            row_stmt = row_stmt.where(w)
        rows = (await session.execute(row_stmt)).scalars().unique().all()
        rows_list = list(rows)
        for pred in python_predicates:
            rows_list = pred.apply_python(rows_list)
        counter: Counter[str] = Counter()
        for r in rows_list:
            vals = getattr(r, attr, None) or []
            counter.update(vals)
    else:
        # parent class 미상 → 컬럼만 select
        row_stmt = select(column)
        for w in where_clauses:
            row_stmt = row_stmt.where(w)
        vals_rows = (await session.execute(row_stmt)).scalars().all()
        counter = Counter()
        for vals in vals_rows:
            if vals:
                counter.update(vals)

    items = counter.most_common(limit) if limit is not None else counter.most_common()
    return [(str(t), int(c)) for t, c in items]


# ---------------------------------------------------------------------------
# Text predicates
# ---------------------------------------------------------------------------
def summary_ilike(column: Any, q: str) -> ColumnElement[bool]:
    """``column ILIKE %q%`` — PG/SQLite 모두 동일."""
    pattern = f"%{q}%"
    return column.ilike(pattern)


def fts_match(column: Any, q: str, session: AsyncSession) -> ColumnElement[bool]:
    """간이 FTS.

    - PG : ``to_tsvector('simple'::regconfig, column) @@ plainto_tsquery('simple'::regconfig, q)``
      ``literal('simple')`` 만 쓰면 첫 인자가 ``varchar`` 로 추론되어 PG 의
      ``to_tsvector(regconfig, text)`` 시그니처와 매칭되지 않는다
      (``UndefinedFunctionError``). ``literal_column`` 으로 ``::regconfig``
      캐스팅을 강제한다.
    - 기타 : ILIKE 폴백.
    """
    if not q:
        return literal(False)
    if is_postgres(session):
        from sqlalchemy import literal_column

        cfg = literal_column("'simple'::regconfig")
        tsvector = func.to_tsvector(cfg, column)
        tsquery = func.plainto_tsquery(cfg, literal(q))
        return tsvector.op("@@")(tsquery)
    return summary_ilike(column, q)


# ---------------------------------------------------------------------------
# Pagination helper (re-exported here for convenience; svc layer also uses)
# ---------------------------------------------------------------------------
async def paginate_rows(
    session: AsyncSession,
    stmt: Select,
    *,
    limit: int,
    offset: int,
    extra_python_predicates: Sequence[ArrayPredicate] = (),
) -> tuple[list[Any], int]:
    """공통 페이징.

    SQLite 폴백 술어가 있을 때는 SQL count()/limit/offset 가 부정확하므로
    전체 행을 가져와 파이썬에서 슬라이스한다 (테스트 규모에 한해 안전).
    """
    if extra_python_predicates:
        all_rows = (await session.execute(stmt)).scalars().unique().all()
        rows_list = list(all_rows)
        for pred in extra_python_predicates:
            rows_list = pred.apply_python(rows_list)
        total = len(rows_list)
        return rows_list[offset : offset + limit], total

    total_stmt = select(func.count()).select_from(stmt.subquery())
    total = int((await session.execute(total_stmt)).scalar_one())
    rows = (
        await session.execute(stmt.limit(limit).offset(offset))
    ).scalars().unique().all()
    return list(rows), total


__all__ = [
    "ArrayPredicate",
    "array_contains",
    "array_overlap",
    "array_unnest_count",
    "dialect_name",
    "execute_with_array_filter",
    "fts_match",
    "is_postgres",
    "paginate_rows",
    "summary_ilike",
]
