# 임베딩 슬롯 m3 의 실 PostgreSQL 길 — halfvec 바인딩 · advisory lock · 슬롯 열 검사 · m3 의미검색(정확 범위 포함)
"""SQLite 로는 못 보는 것만 실 PG 로 본다(AIDH_TEST_PG_URL 을 주면 돈다 — 폐기용 DB, 이행 0032 까지 올린 것).

- 백필이 halfvec(1024) 열을 실제로 쓰고 레코드의 updated_at 을 움직이지 않는다.
- 같은 (슬롯, 표)를 다른 연결이 쥐고 있으면 백필이 겹쳐 돌지 않는다(pg_try_advisory_lock).
- 활성 슬롯이 m3 면 의미검색이 halfvec 열을 CAST 한 질의로 재고 전역 · 좌석 범위 모두 맞는 절을 1위로 낸다.

이 파일의 백필은 청크마다 커밋한다(트랜잭션 롤백 픽스처를 쓰지 않는다) — 만든 행은 끝에 지운다.
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, select, text

from api.services.embedding import HashEmbedder

# pg_engine 은 세션 범위 픽스처라 그 연결은 세션 이벤트 루프에 묶인다 — 시험도 그 루프에서 돈다(함수 루프에서 돌면
# 'attached to a different loop' 로 죽는다).
pytestmark = pytest.mark.asyncio(loop_scope="session")

_PREFIX = "DOC-ZZ-M3PG-2026-"


class _Fake1024(HashEmbedder):
    dim = 1024
    name = "fake-bge-m3-d1024"
    model_id = "BAAI/bge-m3"

    def encode_many(self, texts, *, batch_size=None):
        return [self.encode(t) for t in texts]


@pytest.fixture
def m3pg(monkeypatch, pg_engine):
    from api.db import base as db_base
    from api.services import embedding

    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.delenv("AIDH_EMBED_FILL", raising=False)
    monkeypatch.delenv("AIDH_EMBED_ACTIVE", raising=False)
    monkeypatch.setitem(embedding._EMBEDDER_CACHE, "bge_m3", _Fake1024())
    monkeypatch.setattr(db_base, "engine", pg_engine)
    return pg_engine


async def _seed(engine, texts):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api.db.models import Record, RecordSection

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        await s.execute(delete(Record).where(Record.id.like(_PREFIX + "%")))
        for i, body in enumerate(texts):
            rid = f"{_PREFIX}{i + 1:010d}"
            s.add(Record(id=rid, data_type="DOC", team="ZZ", group="M3PG", year=2026, seq=i + 1,
                         title=f"m3 시험 {i}", summary="", tags=[], agents=["seat-m3pg"],
                         content={"caption": body}))
            await s.flush()
            s.add(RecordSection(record_id=rid, section_id="1", level=1, title="절", content_text=body))
        await s.commit()
    return sm


async def _cleanup(sm):
    from api.db.models import Record

    async with sm() as s:
        await s.execute(delete(Record).where(Record.id.like(_PREFIX + "%")))
        await s.commit()


async def test_백필은_halfvec_열을_쓰고_updated_at_은_그대로다(m3pg):
    from api.db.models import Record, RecordSection
    from api.services import embed_backfill, embed_slots

    sm = await _seed(m3pg, ["낙하 충격 코너 응력", "배터리 스웰링 가스", "카메라 OIS 떨림"])
    try:
        async with m3pg.connect() as c:
            assert await embed_slots.check_columns(c) == []
        async with sm() as s:
            before = dict((await s.execute(select(Record.id, Record.updated_at)
                                           .where(Record.id.like(_PREFIX + "%")))).all())
        m3 = embed_slots.slot("m3")
        rec = await embed_backfill.fill_table(m3, "records", record_ids=list(before))
        sec = await embed_backfill.fill_table(m3, "record_sections", record_ids=list(before))
        assert rec["processed"] == 3 and sec["processed"] == 3
        async with sm() as s:
            vecs = (await s.execute(select(RecordSection.embedding_m3).where(
                RecordSection.record_id.like(_PREFIX + "%")))).scalars().all()
            assert all(v is not None and v.dtype.name == "float32" and len(v) == 1024 for v in vecs)
            typ = (await s.execute(text(
                "SELECT pg_typeof(embedding_m3)::text FROM record_sections WHERE record_id LIKE :p LIMIT 1"),
                {"p": _PREFIX + "%"})).scalar()
            assert typ == "halfvec"
            after = dict((await s.execute(select(Record.id, Record.updated_at)
                                          .where(Record.id.like(_PREFIX + "%")))).all())
        assert after == before
    finally:
        await _cleanup(sm)


async def test_다른_연결이_같은_슬롯_표를_쥐면_건너뛴다(m3pg):
    from api.services import embed_backfill, embed_slots

    sm = await _seed(m3pg, ["하나", "둘"])
    key = embed_backfill.lock_key("m3", "record_sections")
    ids = [f"{_PREFIX}{i:010d}" for i in (1, 2)]
    try:
        async with m3pg.connect() as other:
            other = await other.execution_options(isolation_level="AUTOCOMMIT")
            assert (await other.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})).scalar()
            try:
                # 표 전체를 도는 러너는 겹치지 않는다
                res = await embed_backfill.fill_table(embed_slots.slot("m3"), "record_sections")
                assert res["locked"] is True and res["processed"] == 0
                # 레코드 범위(적재 직후 자동 임베딩)는 잠금과 상관없이 곧바로 채운다
                res = await embed_backfill.fill_table(embed_slots.slot("m3"), "record_sections", record_ids=ids)
                assert res["locked"] is False and res["processed"] == 2
            finally:
                await other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        res = await embed_backfill.fill_table(embed_slots.slot("m3"), "record_sections", record_ids=ids)
        assert res["processed"] == 0 and res["backlog_at_start"] == 0
    finally:
        await _cleanup(sm)


async def test_m3_의미검색은_전역과_좌석_범위_모두_맞는_절을_1위로(m3pg, monkeypatch):
    from api.services import embed_backfill, embed_slots, search_svc

    bodies = ["낙하 충격 코너 응력", "배터리 스웰링 가스", "카메라 OIS 떨림"]
    sm = await _seed(m3pg, bodies)
    ids = [f"{_PREFIX}{i:010d}" for i in (1, 2, 3)]
    try:
        await embed_backfill.fill_table(embed_slots.slot("m3"), "record_sections", record_ids=ids)
        monkeypatch.setenv("AIDH_EMBED_ACTIVE", "m3")
        async with sm() as s:
            seat = await search_svc.semantic_search(s, bodies[1], top_k=3, record_ids=ids)
            assert seat[0]["record_id"] == ids[1] and seat[0]["score"] >= 0.999
            # 전역은 HNSW 근사다 — 시험 행은 해시 벡터라 실제 벡터들 사이에서 외톨이다. 이 트랜잭션에만 후보를 넓힌다.
            await s.execute(text("SET LOCAL hnsw.ef_search = 400"))
            glob = await search_svc.semantic_search(s, bodies[1], top_k=10)
            assert glob and glob[0]["record_id"] == ids[1] and glob[0]["score"] >= 0.999
    finally:
        await _cleanup(sm)
