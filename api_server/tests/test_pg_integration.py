# 실 PostgreSQL 전용 경로(pgvector ANN · @> · SQL 페이징) 통합 테스트.
"""2계층 테스트의 상단 — SQLite 폴백이 못 타는 프로덕션 경로를 실 PG 로 검증.

pg_session fixture 는 AIDH_TEST_PG_URL opt-in (미설정이면 skip). per-test
트랜잭션 롤백이라 데이터 오염이 없다. hash 임베더(결정론적)로 동일 시그니처=
동일 벡터 → <=> 거리 0 → 유사도 1.0 을 보장한다.

검증 대상 (SQLite 가 못 타던 실제 코드 경로):
  - suggest_by_similarity → is_postgres=True → _ann_neighbors (vector <=>)
  - query_records tags    → array_contains PG 분기 (@> 배열 포함)
  - paginate_rows         → SQL count()/limit/offset (python 후필터 아님)
"""
from __future__ import annotations

import pytest

from api.services.sql_compat import is_postgres


@pytest.fixture(autouse=True)
def _hash_embedder(monkeypatch):
    """모든 PG 테스트를 hash 임베더로 — 모델 다운로드/네트워크 없이 결정론적."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    from api.services import embedding

    embedding._EMBEDDER_CACHE.clear()
    yield
    embedding._EMBEDDER_CACHE.clear()


async def _seed(session, rid, **over):
    from api.db.models import Record

    base = dict(
        id=rid, data_type="DATA", team="HE", group="CAE", year=2026,
        seq=int(rid.rsplit("-", 1)[-1]),  # id 끝번호 = 고유 seq (자연키 uq 충돌 방지)
        title="인장", summary="", tags=[], agents=[],
        content={"caption": "인장", "headers": ["strain", "stress"]},
    )
    base.update(over)
    session.add(Record(**base))
    await session.flush()


# ── 1. pgvector ANN 실제 경로 (vector <=>) ───────────────────────────
@pytest.mark.asyncio
async def test_ann_similarity_uses_pgvector(pg_session):
    from api.services import similarity_svc as sim

    # 이 세션이 실제로 PG 경로를 타는지 강제 확인 — 아니면 테스트 무의미.
    assert is_postgres(pg_session) is True

    # 동일 시그니처 2건 + signature_embedding 채움(ANN 대상은 NOT NULL 만).
    for i, rid in enumerate(("DATA-HE-CAE-2026-0000000001", "DATA-HE-CAE-2026-0000000002"), 1):
        await _seed(
            pg_session, rid, seq=i, doc_type="material_test_data",
            tags=["stress-strain", "material"],
            content={"caption": "인장", "headers": ["strain", "stress"],
                     "graph_type": "stress_strain"},
        )
        ok = await sim.set_signature_embedding(pg_session, rid)
        assert ok is True

    # 같은 시그니처로 질의 → _ann_neighbors(<=>) 가 두 건을 찾아야 한다.
    res = await sim.suggest_by_similarity(
        pg_session, caption="인장", headers=["strain", "stress"], data_type="DATA",
    )
    assert res["neighbors"], "ANN 이 이웃을 찾아야 함"
    # 동일 벡터 → cosine 유사도 ≈ 1.0 → confidence high.
    assert res["neighbors"][0]["score"] >= 0.99
    assert res["confidence"] == "high"
    # 집계 제안 (SQLite 테스트와 동일 계약, 이번엔 실 PG 경로로).
    assert res["suggested"]["doc_type"]["value"] == "material_test_data"
    assert res["suggested"]["graph_type"]["value"] == "stress_strain"
    # team/group 은 자동 확정 금지 — needs_human 후보로만.
    assert res["needs_human"]["team"]["candidates"] == ["HE"]
    assert "team" not in res["suggested"]


@pytest.mark.asyncio
async def test_ann_skips_null_signature(pg_session):
    from api.services import similarity_svc as sim

    # signature_embedding 미백필(NULL) 이면 ANN 대상에서 제외 → 이웃 없음.
    await _seed(pg_session, "DATA-HE-CAE-2026-0000000009")  # set_signature_embedding 안 함
    res = await sim.suggest_by_similarity(
        pg_session, caption="인장", headers=["strain", "stress"], data_type="DATA",
    )
    assert res["neighbors"] == []
    assert res["confidence"] == "none"


# ── 2. @> 배열 포함 연산자 실제 경로 ─────────────────────────────────
@pytest.mark.asyncio
async def test_tags_filter_uses_at_operator(pg_session):
    from api.services.record_query_svc import query_records

    await _seed(pg_session, "DATA-HE-CAE-2026-0000000001", tags=["x", "y"])
    await _seed(pg_session, "DATA-HE-CAE-2026-0000000002", tags=["x"])

    # PG 분기: @> 는 '모든 원소 포함'(AND). {x,y} 는 1건만.
    rows, total = await query_records(pg_session, tags=["x", "y"])
    assert total == 1 and rows[0].id == "DATA-HE-CAE-2026-0000000001"
    # {x} 는 두 건 모두 포함.
    rows, total = await query_records(pg_session, tags=["x"])
    assert total == 2


# ── 3. SQL count()/limit/offset 실제 경로 ────────────────────────────
@pytest.mark.asyncio
async def test_pagination_uses_sql_limit_offset(pg_session):
    from api.services.record_query_svc import query_records

    for i in range(1, 6):
        await _seed(pg_session, f"DATA-HE-CAE-2026-000000000{i}", seq=i)

    # SQL count() 는 전체 5, limit 은 페이지 크기.
    rows, total = await query_records(pg_session, team="HE", limit=2, offset=0)
    assert total == 5 and len(rows) == 2
    rows, total = await query_records(pg_session, team="HE", limit=2, offset=4)
    assert total == 5 and len(rows) == 1  # 마지막 페이지 1건


@pytest.mark.asyncio
async def test_soft_delete_excluded_pg(pg_session):
    from datetime import datetime, timezone

    from api.services.record_query_svc import query_records

    await _seed(pg_session, "DATA-HE-CAE-2026-0000000001")
    await _seed(pg_session, "DATA-HE-CAE-2026-0000000002",
                deleted_at=datetime.now(timezone.utc))
    _, total = await query_records(pg_session, team="HE")
    assert total == 1
    _, total = await query_records(pg_session, team="HE", include_deleted=True)
    assert total == 2
