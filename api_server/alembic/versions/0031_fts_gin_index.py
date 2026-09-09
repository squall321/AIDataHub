"""FTS GIN 표현식 인덱스 — to_tsvector 전수 스캔 제거

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-09

배경:
    ``fts_search`` 는 ``to_tsvector('simple', col) @@ websearch_to_tsquery(...)`` 를 쓰는데
    그 **식에 인덱스가 없었다**(마이그레이션 전수 grep 0건). 표현식 인덱스가 없으면
    PostgreSQL 은 행마다 to_tsvector 를 계산하며 전수 스캔한다.

    실측(2026-09-09, record_sections 862,910 행):
        Parallel Seq Scan, Rows Removed by Filter 287,637 × 3 worker,
        Execution Time 25,633 ms → 0건.
    AND 매칭이 0건이면 코드가 OR 로 한 번 더 돌고(search_svc.py), 섹션·레코드 질의가
    각각 그러므로 한 호출에 4회 스캔이 된다. agent_search(mode="fts") 가 221초였다.

인덱스 대상 3종:
    - record_sections.content_text  (본문 — 스캔 비용의 대부분)
    - records.title / records.summary (레코드 질의)

    ``fts_match`` 가 만드는 식과 **글자 그대로 같아야** 플래너가 인덱스를 쓴다.
    'simple'::regconfig 캐스팅까지 동일하게 적는다.

CONCURRENTLY 인 이유:
    일반 CREATE INDEX 는 SHARE 락을 잡아 그 표의 **쓰기를 막는다**. 이 DB 는 논문
    인제스트가 상시 쓰고 있어 빌드 동안(수 분) 적재가 멈춘다. CONCURRENTLY 는 트랜잭션
    안에서 못 돌므로 ``autocommit_block`` 으로 감싼다.

    ⚠ CONCURRENTLY 는 실패 시 INVALID 인덱스를 남긴다. 재실행 전에
    ``\\di+ idx_sections_fts_gin`` 으로 확인하고 필요하면 DROP 후 재시도한다.

안전 확인(2026-09-09 실측):
    content_text 최대 60,038자 / 평균 1,401자 — tsvector 1MB 한계 초과 행 0건.
    'word is too long to be indexed'(2047자 초과 단어 무시)는 NOTICE 이지 실패가 아니다.
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    ("idx_sections_fts_gin", "record_sections", "content_text"),
    ("idx_records_title_fts_gin", "records", "title"),
    ("idx_records_summary_fts_gin", "records", "summary"),
)


def upgrade() -> None:
    from alembic import op

    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite 테스트는 ILIKE 폴백이라 인덱스 대상이 아니다.
    with op.get_context().autocommit_block():
        for name, table, col in _INDEXES:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                f"ON {table} USING gin (to_tsvector('simple'::regconfig, {col}))"
            )


def downgrade() -> None:
    from alembic import op

    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        for name, _table, _col in _INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
