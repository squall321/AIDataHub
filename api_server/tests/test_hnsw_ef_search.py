# 전역 의미검색이 HNSW 탐색 폭(hnsw.ef_search)을 넓혀 거는가 — 기본값 40 으로는 정확 검색 상위 10 의 3분의 1 남짓을 놓쳤다
"""왜 — 전역 의미검색(record_ids 없음)은 HNSW 색인으로 정렬한다. PG 기본 탐색 폭 40 에서 질의 80개의 재현율@10 이 e5(v1) 0.641 ·
BGE-M3(m3) 0.665 였다 — 탐색이 그래프의 엉뚱한 무리에 갇혀 정답 카드 절(코사인 0.68)보다 먼 무관한 절(0.59)을 1위로 냈다(dev 2026-10-16,
HWAXPortal docs/embedder-bge-m3 EM-D16). 1000(pgvector 상한)이면 0.958 · 0.971 이고 데운 캐시에서 p95 30ms 안쪽이다.

  · 전역은 검색 문장 앞에 이 트랜잭션에만 SET LOCAL hnsw.ef_search 를 건다(두 슬롯 모두).
  · 좌석 범위는 정확 검색이라 걸지 않는다.
  · AIDH_HNSW_EF_SEARCH 로 바꾸고, 0 이면 걸지 않는다(되돌리기). 잘못된 값은 부팅에서 멈춘다.

시험 DB 는 SQLite 라 PostgreSQL 길은 실제로 돌지 않는다 — 그 길이 보내는 문장을 모아 본다(test_scoped_semantic_exact 와 같은 방식).
"""
from __future__ import annotations

import pytest

from api.services import embed_slots


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


def _sets(s: _Capture) -> list[str]:
    return [str(st) for st in s.stmts if str(st).startswith("SET LOCAL")]


@pytest.fixture
def _pg(monkeypatch):
    from api.services import embedding, search_svc
    from api.services.embedding import HashEmbedder

    class _Fake1024(HashEmbedder):
        dim = 1024
        name = "fake-bge-m3-d1024"
        model_id = "BAAI/bge-m3"

    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.delenv("AIDH_EMBED_ACTIVE", raising=False)
    monkeypatch.delenv("AIDH_EMBED_FILL", raising=False)
    monkeypatch.delenv("AIDH_HNSW_EF_SEARCH", raising=False)
    monkeypatch.delenv("AIDH_SEARCH_TOP_K_MAX", raising=False)
    monkeypatch.setitem(embedding._EMBEDDER_CACHE, "bge_m3", _Fake1024())
    monkeypatch.setattr(search_svc, "is_postgres", lambda session: True)
    return search_svc


def test_탐색_폭은_기본_1000_이고_요청_수보다_작지_않으며_상한을_넘지_않는다(monkeypatch):
    monkeypatch.delenv("AIDH_HNSW_EF_SEARCH", raising=False)
    assert embed_slots.hnsw_ef_search(30) == 1000
    monkeypatch.setenv("AIDH_HNSW_EF_SEARCH", "400")
    assert embed_slots.hnsw_ef_search(30) == 400
    assert embed_slots.hnsw_ef_search(600) == 600, "색인은 탐색 폭보다 많이 내지 못한다 — 요청 수 아래로 두지 않는다"
    assert embed_slots.hnsw_ef_search(5000) == 1000, "pgvector 상한"
    monkeypatch.setenv("AIDH_HNSW_EF_SEARCH", "0")
    assert embed_slots.hnsw_ef_search(30) is None, "0 이면 걸지 않는다(PG 기본값)"


@pytest.mark.parametrize("bad", ["abc", "-1", "1.5"])
def test_잘못된_탐색_폭은_부팅에서_멈춘다(monkeypatch, bad):
    monkeypatch.delenv("AIDH_EMBED_ACTIVE", raising=False)
    monkeypatch.delenv("AIDH_EMBED_FILL", raising=False)
    monkeypatch.setenv("AIDH_HNSW_EF_SEARCH", bad)
    with pytest.raises(embed_slots.SlotConfigError, match="AIDH_HNSW_EF_SEARCH"):
        embed_slots.validate_settings()


@pytest.mark.asyncio
@pytest.mark.parametrize("active", ["v1", "m3"])
async def test_전역은_검색_앞에_탐색_폭을_건다(_pg, monkeypatch, active):
    monkeypatch.setenv("AIDH_EMBED_ACTIVE", active)
    s = _Capture()
    await _pg.semantic_search(s, "낙하 충격 코너 응력", top_k=10)
    assert _sets(s) == ["SET LOCAL hnsw.ef_search = 1000"]
    assert str(s.stmts[0]).startswith("SET LOCAL"), "검색 문장보다 먼저 걸어야 그 문장에 먹는다"
    assert len(s.stmts) == 2


@pytest.mark.asyncio
async def test_좌석_범위에는_걸지_않는다(_pg, monkeypatch):
    monkeypatch.setenv("AIDH_EMBED_ACTIVE", "m3")
    s = _Capture()
    await _pg.semantic_search(s, "낙하 충격", top_k=10, record_ids=["DOC-A"])
    assert _sets(s) == [], "좌석 범위는 정확 검색이다 — 색인을 쓰지 않는다"


@pytest.mark.asyncio
async def test_0_이면_전역에도_걸지_않는다(_pg, monkeypatch):
    monkeypatch.setenv("AIDH_HNSW_EF_SEARCH", "0")
    s = _Capture()
    await _pg.semantic_search(s, "낙하 충격", top_k=10)
    assert _sets(s) == [] and len(s.stmts) == 1
