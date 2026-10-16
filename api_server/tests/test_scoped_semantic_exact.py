# 좌석 범위 의미검색은 정확 검색으로 정렬하는가 — 큰 좌석에서 HNSW 가 전역 이웃만 내고 좌석 밖을 걸러 히트를 잃던 것(docs/ko-search-baseline.md)
"""왜 — `ORDER BY embedding <=> q` 를 그대로 두면 좌석이 커질 때(dev 실측 98~129장 사이) 플래너가 HNSW 색인 순서 훑기를 고른다.
색인은 전역 이웃을 ef_search(기본 40)개만 내고 좌석 밖 것은 그 뒤에 걸러져, 33건을 달라 해도 0~19건이 남았다(좌석 224개 · 카드 묶음
65.5%). 정렬식에 `+ 0` 을 붙이면 색인이 정렬을 대신할 수 없어 좌석 안 절을 다 잰다.

시험 DB 는 SQLite 라 PostgreSQL 길은 실제로 돌지 않는다 — 그 길이 만드는 문장을 PostgreSQL 방언으로 컴파일해 정렬식을 본다.
실제 DB 에서의 효과(히트 수 · 순위)는 scripts/ko_search_eval 의 실행기로 잰다.
"""
from __future__ import annotations

import re

import pytest
from sqlalchemy.dialects import postgresql


class _Rows:
    def all(self):
        return []


class _Capture:
    """execute 로 받은 문장을 모으는 세션 대역 — 결과는 빈 목록이다."""

    def __init__(self):
        self.stmts = []

    async def execute(self, stmt, *a, **kw):
        self.stmts.append(stmt)
        return _Rows()


def _order_by(stmt) -> str:
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    m = re.search(r"ORDER BY (.+?) LIMIT", sql, re.S)
    assert m, sql
    return " ".join(m.group(1).split())


@pytest.fixture
def _pg(monkeypatch):
    from api.services import search_svc

    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.delenv("AIDH_SEARCH_TOP_K_MAX", raising=False)
    monkeypatch.setattr(search_svc, "is_postgres", lambda session: True)
    return search_svc


@pytest.mark.asyncio
async def test_좌석_범위는_색인이_대신할_수_없는_정렬식이다(_pg):
    s = _Capture()
    await _pg.semantic_search(s, "낙하 충격 코너 응력", top_k=33, record_ids=["DOC-A", "DOC-B"])
    (stmt,) = s.stmts
    order = _order_by(stmt)
    assert "<=>" in order and "+" in order, order
    assert "record_sections.record_id IN" in str(stmt.compile(dialect=postgresql.dialect())), "좌석 범위 조건은 그대로다"


@pytest.mark.asyncio
async def test_빈_좌석도_범위다(_pg):
    """빈 목록은 '전역' 이 아니다(바인딩 0 인 좌석) — 정확 정렬도 범위 쪽이다."""
    s = _Capture()
    await _pg.semantic_search(s, "낙하 충격", top_k=5, record_ids=[])
    (stmt,) = s.stmts
    assert "+" in _order_by(stmt)


@pytest.mark.asyncio
async def test_전역은_그대로_색인을_쓰는_정렬식이다(_pg):
    s = _Capture()
    await _pg.semantic_search(s, "낙하 충격", top_k=10)
    await _pg.semantic_search(s, "낙하 충격", top_k=10, data_types=["DOC"])
    selects = [st for st in s.stmts if not str(st).startswith("SET LOCAL")]  # 전역은 탐색 폭을 먼저 건다(test_hnsw_ef_search)
    assert len(selects) == 2
    for stmt in selects:
        order = _order_by(stmt)
        assert "<=>" in order and "+" not in order, order
