# 임베딩 슬롯(v1 · m3) 시험 — 설정 검증 · 이행 · 슬롯별 백필 · 읽기 열 · /api/embed · health
"""왜 — e5-base(v1) 를 BGE-M3(m3) 로 바꾸는 동안 의미검색이 비지 않게 새 열을 나란히 채우고 설정 한 줄로 읽기를 옮긴다
(HWAXPortal docs/embedder-bge-m3). 여기서는 실제 모델을 띄우지 않는다 — 1024차원 해시 임베더를 m3 공급자 자리에 꽂고,
PostgreSQL 길은 문장을 PG 방언으로 컴파일해 본다. 실 PG · 실 BGE-M3 는 docs/embedder-bge-m3.md 의 통합 실행으로 쟀다.
공급자 bge_m3 와 모델 잠금은 test_embedding_bge_m3.py 다.
"""
from __future__ import annotations

import asyncio
import re
import threading
import types
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from api.services.embedding import HashEmbedder


class _Fake1024(HashEmbedder):
    """m3 공급자 자리에 꽂는 1024차원 해시 임베더 — 인코딩한 글을 남긴다."""

    dim = 1024
    name = "fake-bge-m3-d1024"
    model_id = "BAAI/bge-m3"

    def __init__(self) -> None:
        self.encoded: list[str] = []
        self.fail_on: str | None = None   # 이 글이 든 묶음은 실패한다(청크 격리 시험)
        self.abort_after: int | None = None  # 이만큼 인코딩한 뒤 실행을 끊는다(이어하기 시험)

    def encode(self, text):
        self.encoded.append(text)
        return super().encode(text)

    def encode_many(self, texts, *, batch_size=None):
        if self.fail_on is not None and any(self.fail_on in t for t in texts):
            raise RuntimeError("CUDA error: 가짜 실패")
        if self.abort_after is not None and len(self.encoded) >= self.abort_after:
            raise KeyboardInterrupt  # 실행 전체가 끊긴다(Ctrl-C) — 청크 격리가 삼키지 않는다
        return [self.encode(t) for t in texts]


class _CountingHash(HashEmbedder):
    """v1 자리(EMBEDDING_PROVIDER=hash)에 꽂는 해시 임베더 — 인코딩한 글을 남긴다."""

    name = "counting-hash"

    def __init__(self) -> None:
        self.encoded: list[str] = []

    def encode(self, text):
        self.encoded.append(text)
        return super().encode(text)


@pytest.fixture
def slots(monkeypatch):
    """슬롯 설정을 깨끗이 하고, v1 은 해시(EMBEDDING_PROVIDER=hash), m3 는 1024차원 가짜를 꽂는다."""
    from api.services import embedding

    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.delenv("AIDH_EMBED_ACTIVE", raising=False)
    monkeypatch.delenv("AIDH_EMBED_FILL", raising=False)
    v1 = _CountingHash()
    m3 = _Fake1024()
    monkeypatch.setattr(embedding, "get_embedder", lambda: v1)
    monkeypatch.setitem(embedding._EMBEDDER_CACHE, "bge_m3", m3)
    return types.SimpleNamespace(v1=v1, m3=m3)


@pytest.fixture
def bf_engine(monkeypatch, test_engine, test_session_maker):
    """백필이 실 DB 엔진을 절대 쓰지 않게 — 시험 엔진(SQLite)으로 바꿔 끼운다."""
    from api.db import base as db_base

    monkeypatch.setattr(db_base, "engine", test_engine)
    return test_session_maker


_RID = "DOC-HE-CAE-2026-0000000{:03d}"


async def _seed(maker, n_records=1, sections=("절 본문",), **rec):
    """레코드 n 개와 그 절을 넣는다. 돌려주는 것은 레코드 id 목록."""
    from api.db.models import Record, RecordSection

    ids = []
    async with maker() as s:
        for i in range(n_records):
            rid = _RID.format(100 + i)
            base = dict(id=rid, data_type="DOC", team="HE", group="CAE", year=2026, seq=100 + i,
                        title=f"제목 {i}", summary="", tags=[], agents=["seat-a"],
                        content={"caption": f"캡션 {i}", "headers": ["strain", "stress"], "notes": "메모"})
            base.update(rec)
            s.add(Record(**base))
            await s.flush()
            for j, body in enumerate(sections):
                s.add(RecordSection(record_id=rid, section_id=str(j + 1), level=1, title=f"절 {j}",
                                    content_text=body))
            ids.append(rid)
        await s.commit()
    return ids


async def _col(maker, model, col, where=None):
    """한 열의 값을 기본키 순으로(deferred 인 m3 열도 직접 고른다)."""
    pk = list(model.__table__.primary_key.columns)[0]
    stmt = select(getattr(model, col)).order_by(pk)
    if where is not None:
        stmt = stmt.where(where)
    async with maker() as s:
        return list((await s.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# 1. 슬롯 설정
# ---------------------------------------------------------------------------
def test_기본은_v1_하나_지금과_같다(slots):
    from api.services import embed_slots

    assert embed_slots.active_name() == "v1" and embed_slots.fill_names() == ["v1"]
    v1 = embed_slots.slot("v1")
    assert v1.cols("record_sections").vec == "embedding" and not v1.halfvec


def test_활성_슬롯은_적지_않아도_채운다(slots, monkeypatch):
    from api.services import embed_slots

    monkeypatch.setenv("AIDH_EMBED_ACTIVE", "m3")
    assert embed_slots.fill_names() == ["v1", "m3"]
    monkeypatch.setenv("AIDH_EMBED_FILL", " M3 , v1 ")
    assert embed_slots.fill_names() == ["v1", "m3"]
    monkeypatch.setenv("AIDH_EMBED_FILL", "m3")
    assert embed_slots.fill_names() == ["m3"]
    m3 = embed_slots.active()
    assert m3.dim == 1024 and m3.halfvec and m3.provider == "bge_m3"
    assert m3.cols("records").vec == "signature_embedding_m3"


@pytest.mark.parametrize("knob,value", [("AIDH_EMBED_ACTIVE", "m4"), ("AIDH_EMBED_ACTIVE", "bge_m3"),
                                        ("AIDH_EMBED_FILL", "v1,m4"), ("AIDH_EMBED_FILL", ",")])
def test_모르는_슬롯은_조용히_v1_로_떨어지지_않는다(slots, monkeypatch, knob, value):
    from api.services import embed_slots

    monkeypatch.setenv(knob, value)
    with pytest.raises(embed_slots.SlotConfigError, match=knob):
        embed_slots.validate_settings()


@pytest.mark.asyncio
async def test_부팅_검증_모르는_값이면_멈춘다(slots, monkeypatch):
    from api import main

    monkeypatch.setenv("AIDH_EMBED_ACTIVE", "m4")
    with pytest.raises(Exception, match="AIDH_EMBED_ACTIVE"):
        await main._check_embed_slots()


@pytest.mark.asyncio
async def test_부팅_검증_m3_모델이_없으면_활성은_멈추고_채움은_경고(slots, monkeypatch, test_engine, caplog):
    from api import main
    from api.services import embed_slots

    monkeypatch.setattr(main, "engine", test_engine)  # SQLite — 열 검사는 PG 만 한다
    monkeypatch.setattr(embed_slots, "model_files_present", lambda repo: False)
    monkeypatch.setenv("AIDH_EMBED_ACTIVE", "m3")
    with pytest.raises(RuntimeError, match="BAAI/bge-m3"):
        await main._check_embed_slots()
    monkeypatch.setenv("AIDH_EMBED_ACTIVE", "v1")
    monkeypatch.setenv("AIDH_EMBED_FILL", "v1,m3")
    await main._check_embed_slots()
    assert "m3 백필이 실패한다" in caplog.text


# ---------------------------------------------------------------------------
# 2. 이행
# ---------------------------------------------------------------------------
def test_이행_0032_는_네_표에_halfvec_열만_더하고_색인은_짓지_않는다():
    src = (Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0032_embedding_m3_slot.py").read_text(
        encoding="utf-8")
    assert re.search(r'^revision: str = "0032"', src, re.M)
    assert re.search(r'^down_revision: .*= "0031"', src, re.M)
    for table, vec in (("record_sections", "embedding_m3"), ("records", "signature_embedding_m3"),
                       ("agent_sample_embeddings", "embedding_m3"), ("mcp_uploads", "description_embedding_m3")):
        assert f'("{table}", "{vec}")' in src
    assert "halfvec({_M3_DIM})" in src and "_M3_DIM = 1024" in src
    assert "{vec}_at timestamptz" in src and "{vec}_model varchar(100)" in src
    assert "DROP COLUMN IF EXISTS {vec}" in src
    assert "CREATE INDEX" not in src.split('"""', 2)[2]  # 독스트링 밖에 색인 DDL 이 없다
    # 데이터를 다시 쓰지 않는다(0013 처럼 비우지 않는다)
    assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", src, re.I)


def test_모델의_m3_열은_halfvec_1024_이고_벡터는_deferred_다():
    from api.db import models

    for model, vec in ((models.RecordSection, "embedding_m3"), (models.Record, "signature_embedding_m3"),
                       (models.AgentSampleEmbedding, "embedding_m3"), (models.MCPUpload, "description_embedding_m3")):
        col = model.__table__.c[vec]
        assert "HALFVEC(1024)" in str(col.type.compile(dialect=postgresql.dialect()))
        assert getattr(model, vec).property.deferred
        assert f"{vec}_at" in model.__table__.c and f"{vec}_model" in model.__table__.c


# ---------------------------------------------------------------------------
# 3. 백필
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_m3_백필은_m3_열만_채우고_v1_은_건드리지_않는다(slots, bf_engine):
    from api.db.models import RecordSection
    from api.services import embed_backfill, embed_slots

    await _seed(bf_engine, n_records=2, sections=("하나", "둘", "셋"))
    res = await embed_backfill.fill_table(embed_slots.slot("m3"), "record_sections", chunk=2)
    assert res["processed"] == 6 and res["chunks"] == 3 and res["failed"] == 0 and not res["locked"]
    assert all(v is not None and len(v) == 1024 for v in await _col(bf_engine, RecordSection, "embedding_m3"))
    assert set(await _col(bf_engine, RecordSection, "embedding_m3_model")) == {"fake-bge-m3-d1024"}
    assert await _col(bf_engine, RecordSection, "embedding") == [None] * 6       # v1 그대로
    assert await _col(bf_engine, RecordSection, "embedded_at") == [None] * 6
    assert slots.v1.encoded == []

    res = await embed_backfill.fill_table(embed_slots.slot("v1"), "record_sections")
    assert res["processed"] == 6
    assert all(v is not None for v in await _col(bf_engine, RecordSection, "embedding"))
    assert len(slots.m3.encoded) == 6  # v1 백필이 m3 를 다시 인코딩하지 않았다


@pytest.mark.asyncio
async def test_빈_본문은_표시만_하고_백로그에서_빠진다(slots, bf_engine):
    from api.db.models import RecordSection
    from api.services import embed_backfill, embed_slots

    await _seed(bf_engine, sections=("본문", "   ", ""))
    m3 = embed_slots.slot("m3")
    res = await embed_backfill.fill_table(m3, "record_sections")
    assert res["processed"] == 1 and res["skipped_empty"] == 2
    assert slots.m3.encoded == ["본문"]
    assert await _col(bf_engine, RecordSection, "embedding_m3_model") == [
        "fake-bge-m3-d1024", "skipped-empty", "skipped-empty"]
    async with bf_engine() as s:
        assert (await embed_backfill.backlog(s, [m3], ["record_sections"])) == {"m3": {"record_sections": 0}}
    again = await embed_backfill.fill_table(m3, "record_sections")
    assert again["processed"] == 0 and again["backlog_at_start"] == 0


@pytest.mark.asyncio
async def test_실패한_청크만_건너뛰고_다음_실행이_다시_집는다(slots, bf_engine):
    from api.db.models import RecordSection
    from api.services import embed_backfill, embed_slots

    await _seed(bf_engine, sections=("가", "나", "독", "라", "마", "바"))
    m3 = embed_slots.slot("m3")
    slots.m3.fail_on = "독"
    res = await embed_backfill.fill_table(m3, "record_sections", chunk=2)
    assert res["processed"] == 4 and res["failed"] == 2 and res["chunks"] == 3
    vecs = await _col(bf_engine, RecordSection, "embedding_m3")
    assert [v is not None for v in vecs] == [True, True, False, False, True, True]

    slots.m3.fail_on = None
    res = await embed_backfill.fill_table(m3, "record_sections", chunk=2)
    assert res["processed"] == 2 and res["backlog_at_start"] == 2


@pytest.mark.asyncio
async def test_끊겨도_커밋한_청크는_남고_다시_돌리면_이어_간다(slots, bf_engine):
    from api.db.models import RecordSection
    from api.services import embed_backfill, embed_slots

    await _seed(bf_engine, sections=tuple(f"절 {i}" for i in range(7)))
    m3 = embed_slots.slot("m3")
    slots.m3.abort_after = 4
    with pytest.raises(KeyboardInterrupt):
        await embed_backfill.fill_table(m3, "record_sections", chunk=2)
    vecs = await _col(bf_engine, RecordSection, "embedding_m3")
    assert sum(v is not None for v in vecs) == 4          # 청크 둘(4행)이 커밋됐다
    slots.m3.abort_after = None
    slots.m3.encoded.clear()
    res = await embed_backfill.fill_table(m3, "record_sections", chunk=2)
    assert res["processed"] == 3 and len(slots.m3.encoded) == 3  # 이미 채운 행은 다시 인코딩하지 않는다
    assert all(v is not None for v in await _col(bf_engine, RecordSection, "embedding_m3"))


@pytest.mark.asyncio
async def test_같은_슬롯_표를_쥔_러너가_있으면_겹쳐_돌지_않는다(slots, bf_engine, test_engine):
    from api.db.models import RecordSection
    from api.services import embed_backfill, embed_slots

    await _seed(bf_engine, sections=("가", "나"))
    async with embed_backfill._hold(test_engine, "m3", "record_sections") as got:
        assert got
        res = await embed_backfill.fill_table(embed_slots.slot("m3"), "record_sections")
        assert res["locked"] is True and res["processed"] == 0
        # 다른 슬롯 · 다른 표는 막지 않는다
        other = await embed_backfill.fill_table(embed_slots.slot("v1"), "record_sections")
        assert other["processed"] == 2
    assert await _col(bf_engine, RecordSection, "embedding_m3") == [None, None]
    res = await embed_backfill.fill_table(embed_slots.slot("m3"), "record_sections")
    assert res["processed"] == 2


def test_잠금_키는_슬롯과_표마다_다르고_박스가_달라도_같다():
    from api.services import embed_backfill

    keys = {embed_backfill.lock_key(s, t) for s in ("v1", "m3")
            for t in ("records", "record_sections", "agent_sample_embeddings", "mcp_uploads")}
    assert len(keys) == 8
    assert embed_backfill.lock_key("m3", "record_sections") == embed_backfill.lock_key("m3", "record_sections")
    assert all(-(2 ** 63) <= k < 2 ** 63 for k in keys)


@pytest.mark.asyncio
async def test_레코드_시그니처는_같은_규칙으로_채우고_updated_at_은_그대로다(slots, bf_engine):
    from api.db.models import Record
    from api.services import embed_backfill, embed_slots
    from api.services.similarity_svc import _record_signature

    ids = await _seed(bf_engine, n_records=3)
    async with bf_engine() as s:
        gone = await s.get(Record, ids[2])
        from datetime import UTC, datetime

        gone.deleted_at = datetime.now(UTC)
        await s.commit()
        before = {r.id: r.updated_at for r in (await s.execute(select(Record))).scalars()}
        want = [_record_signature(r) for r in (await s.execute(select(Record).order_by(Record.id))).scalars()][:2]
    res = await embed_backfill.fill_table(embed_slots.slot("m3"), "records")
    assert res["processed"] == 2                                    # 지운 레코드는 건너뛴다
    assert slots.m3.encoded == want                                  # 시그니처 규칙이 같다(제목 · 헤더 · 메모)
    async with bf_engine() as s:
        after = {r.id: r.updated_at for r in (await s.execute(select(Record))).scalars()}
    assert after == before                                           # merge 의 최신 판정을 움직이지 않는다


@pytest.mark.asyncio
async def test_에이전트_표본과_도구_설명도_채운다(slots, bf_engine):
    from api.db.models import Agent, AgentSampleEmbedding, MCPUpload
    from api.services import embed_backfill, embed_slots

    async with bf_engine() as s:
        s.add(Agent(agent_type="seat-a", name="a", description="d", common_tags=[], data_types=["DOC"]))
        await s.flush()
        s.add(AgentSampleEmbedding(agent_type="seat-a", sample_text="낙하 충격"))
        s.add(MCPUpload(name="tool-a", current_sha="x" * 64, description_text="CSV 통계"))
        s.add(MCPUpload(name="tool-b", current_sha="y" * 64, description_text=None))
        await s.commit()
    res = await embed_backfill.run([embed_slots.slot("m3")])
    assert res["m3"]["agent_sample_embeddings"]["processed"] == 1
    assert res["m3"]["mcp_uploads"]["processed"] == 1               # 설명 없는 도구는 백로그가 아니다
    assert (await _col(bf_engine, AgentSampleEmbedding, "embedding_m3"))[0] is not None
    assert [v is not None for v in await _col(bf_engine, MCPUpload, "description_embedding_m3")] == [True, False]


@pytest.mark.asyncio
async def test_embed_잡은_레코드_범위를_지키고_예전_결과_키를_낸다(slots, bf_engine, monkeypatch):
    from api.db.models import RecordSection
    from api.services import jobs

    monkeypatch.setenv("AIDH_EMBED_FILL", "v1,m3")
    ids = await _seed(bf_engine, n_records=2, sections=("하나", "둘"))
    job = jobs.Job(id="t-embed-m3", kind="embed", payload={"record_id": ids[0]})
    res = await jobs.embed_handler(job)
    assert res["sections_processed"] == 2 and res["slot"] == "v1" and res["errors"] == {}
    assert res["by_slot"]["m3"]["record_sections"]["processed"] == 2
    assert res["by_slot"]["m3"]["records"]["processed"] == 1
    assert "agent_sample_embeddings" not in res["by_slot"]["m3"]    # 레코드 범위면 표본 · 도구는 안 본다
    m3_vecs = await _col(bf_engine, RecordSection, "embedding_m3", RecordSection.record_id == ids[1])
    assert m3_vecs == [None, None]                                   # 범위 밖 레코드는 그대로
    assert job.progress == 1.0


@pytest.mark.asyncio
async def test_슬롯_하나가_실패해도_나머지는_돈다(slots, bf_engine, monkeypatch):
    from api.services import embed_backfill, embed_slots, embedding

    await _seed(bf_engine, sections=("하나",))

    def _boom(provider):
        raise RuntimeError("BAAI/bge-m3 모델 없음")

    monkeypatch.delitem(embedding._EMBEDDER_CACHE, "bge_m3")
    monkeypatch.setattr(embedding, "get_embedder_for", _boom)
    res = await embed_backfill.run([embed_slots.slot("m3"), embed_slots.slot("v1")], ["record_sections"])
    assert "모델 없음" in res["m3"]["record_sections"]["error"]
    assert res["v1"]["record_sections"]["processed"] == 1


def test_스윕은_앞_잡이_돌면_새로_띄우지_않는다(monkeypatch):
    import logging

    from api import main
    from api.services import jobs as job_svc

    made = []
    monkeypatch.setattr(job_svc, "register", lambda kind, handler, payload=None: made.append(kind) or
                        types.SimpleNamespace(id="embed-x", status="running"))
    log = logging.getLogger("t")
    first = main._schedule_embed_sweep(None, log)
    assert made == ["embed"]
    assert main._schedule_embed_sweep(first, log) is first and made == ["embed"]   # 아직 돈다 → 건너뛴다
    first.status = "done"
    main._schedule_embed_sweep(first, log)
    assert made == ["embed", "embed"]


@pytest.mark.asyncio
async def test_레코드를_고쳐_쓰면_두_슬롯의_시그니처가_비워진다(slots, bf_engine):
    from api.db.models import Record
    from api.services import embed_backfill, embed_slots

    ids = await _seed(bf_engine)
    await embed_backfill.fill_table(embed_slots.slot("m3"), "records")
    await embed_backfill.fill_table(embed_slots.slot("v1"), "records")
    async with bf_engine() as s:
        rec = await s.get(Record, ids[0])
        # deferred(raiseload) 인 m3 열에 쓰는 것은 읽기를 부르지 않는다
        for col in ("signature_embedding", "signature_embedding_m3", "signature_embedding_m3_at",
                    "signature_embedding_m3_model"):
            if hasattr(type(rec), col):
                setattr(rec, col, None)
        await s.commit()
    assert await _col(bf_engine, Record, "signature_embedding_m3") == [None]
    assert await _col(bf_engine, Record, "signature_embedding_m3_at") == [None]
    async with bf_engine() as s:
        assert (await embed_backfill.backlog(s, [embed_slots.slot("m3")], ["records"]))["m3"]["records"] == 1


# ---------------------------------------------------------------------------
# 4. 읽기 — 활성 슬롯의 열(PG 방언으로 컴파일)
# ---------------------------------------------------------------------------
class _Rows:
    def all(self):
        return []

    def scalars(self):
        return self


class _Capture:
    def __init__(self):
        self.stmts = []

    async def execute(self, stmt, *a, **kw):
        self.stmts.append(stmt)
        return _Rows()


def _sql(stmt) -> str:
    return " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())


@pytest.fixture
def pg(monkeypatch):
    from api.services import sample_embedding_svc, search_svc, tool_embedding_svc

    monkeypatch.delenv("AIDH_SEARCH_TOP_K_MAX", raising=False)
    for mod in (search_svc, sample_embedding_svc, tool_embedding_svc):
        monkeypatch.setattr(mod, "is_postgres", lambda session: True)
    return types.SimpleNamespace(search=search_svc, samples=sample_embedding_svc, tools=tool_embedding_svc)


@pytest.mark.asyncio
async def test_의미검색은_활성_슬롯의_열을_고르고_halfvec_은_캐스트한다(slots, pg, monkeypatch):
    s = _Capture()
    await pg.search.semantic_search(s, "낙하 충격", top_k=5)
    v1 = _sql(s.stmts[-1])
    assert "record_sections.embedding <=>" in v1 and "embedding_m3" not in v1 and "HALFVEC" not in v1
    assert slots.m3.encoded == []

    monkeypatch.setenv("AIDH_EMBED_ACTIVE", "m3")
    await pg.search.semantic_search(s, "낙하 충격", top_k=5)
    await pg.search.semantic_search(s, "낙하 충격", top_k=5, record_ids=["DOC-A"])
    glob, seat = _sql(s.stmts[-2]), _sql(s.stmts[-1])
    for sql in (glob, seat):
        assert "record_sections.embedding_m3 <=> CAST(" in sql and "AS HALFVEC(1024))" in sql
        assert "record_sections.embedding_m3 IS NOT NULL" in sql
        assert "record_sections.embedding <=>" not in sql
    order = lambda sql: re.search(r"ORDER BY (.+?) LIMIT", sql).group(1)  # noqa: E731
    assert "+" not in order(glob) and "+" in order(seat)   # 좌석 범위는 정확 검색(색인이 정렬을 대신 못 하게)
    assert slots.m3.encoded == ["낙하 충격"] * 2 and slots.v1.encoded == ["낙하 충격"]


@pytest.mark.asyncio
async def test_표본_도구_검색도_활성_슬롯의_열(slots, pg, monkeypatch):
    s = _Capture()
    monkeypatch.setenv("AIDH_EMBED_ACTIVE", "m3")
    await pg.samples.search_samples(s, "낙하", top_k=3)
    await pg.tools.search_tools(s, "통계", top_k=3)
    sample_sql, tool_sql = _sql(s.stmts[0]), _sql(s.stmts[1])
    assert "agent_sample_embeddings.embedding_m3 <=> CAST(" in sample_sql and "HALFVEC(1024)" in sample_sql
    assert "mcp_uploads.description_embedding_m3 <=> CAST(" in tool_sql
    monkeypatch.setenv("AIDH_EMBED_ACTIVE", "v1")
    await pg.samples.search_samples(s, "낙하", top_k=3)
    assert "agent_sample_embeddings.embedding <=>" in _sql(s.stmts[-1])


@pytest.mark.asyncio
async def test_유사도_ANN_은_활성_슬롯의_시그니처_열(slots, monkeypatch):
    from api.services import embed_slots, similarity_svc

    s = _Capture()
    monkeypatch.setenv("AIDH_EMBED_ACTIVE", "m3")
    await similarity_svc._ann_neighbors(s, embed_slots.active(), [0.0] * 1024, "DATA", 5)
    sql = _sql(s.stmts[0])
    assert "records.signature_embedding_m3 <=> CAST(" in sql and "records.signature_embedding_m3 IS NOT NULL" in sql


@pytest.mark.asyncio
async def test_SQLite_의미검색은_활성_슬롯의_벡터만_본다(slots, test_session_maker, monkeypatch):
    from api.db.models import RecordSection
    from api.services import embed_slots, search_svc

    ids = await _seed(test_session_maker, sections=("배터리 스웰링", "카메라 OIS"))
    async with test_session_maker() as s:
        rows = (await s.execute(select(RecordSection).order_by(RecordSection.id))).scalars().all()
        rows[0].embedding_m3 = slots.m3.encode("배터리 스웰링")   # m3 만 채운 절 하나
        await s.commit()
    async with test_session_maker() as s:
        assert await search_svc.semantic_search(s, "배터리 스웰링", top_k=5) == []   # v1 은 비어 있다
        monkeypatch.setenv("AIDH_EMBED_ACTIVE", "m3")
        hits = await search_svc.semantic_search(s, "배터리 스웰링", top_k=5, record_ids=ids)
        assert [h["section_id"] for h in hits] == ["1"] and hits[0]["score"] == 1.0
    assert embed_slots.active_name() == "m3"


@pytest.mark.asyncio
async def test_군집은_활성_슬롯의_열과_문턱을_쓴다(slots, test_session_maker, monkeypatch):
    from api.db.models import RecordSection
    from api.services import cluster_svc, embed_slots

    ids = await _seed(test_session_maker, n_records=2, sections=("같은 글",))
    async with test_session_maker() as s:
        for row in (await s.execute(select(RecordSection))).scalars().all():
            row.embedding_m3 = slots.m3.encode("같은 글")
        await s.commit()
    async with test_session_maker() as s:
        res = await cluster_svc.cluster_around_record(s, anchor_id=ids[0], mode="semantic")
        assert res["cluster_size"] == 0                                  # v1 활성 — v1 벡터가 없다
        monkeypatch.setenv("AIDH_EMBED_ACTIVE", "m3")
        monkeypatch.setitem(embed_slots.THRESHOLDS["m3"], "cluster_record_sim", 0.99)
        res = await cluster_svc.cluster_around_record(s, anchor_id=ids[0], mode="semantic")
        assert [i["id"] for i in res["items"]] == [ids[1]]


@pytest.mark.asyncio
async def test_유사도_확신_문턱은_활성_슬롯의_값(slots, test_session_maker, monkeypatch):
    from api.services import embed_slots, similarity_svc

    await _seed(test_session_maker, data_type="DATA", title="인장 시험")
    monkeypatch.setenv("AIDH_EMBED_ACTIVE", "m3")
    async with test_session_maker() as s:
        res = await similarity_svc.suggest_by_similarity(s, title="인장 시험", headers=["strain", "stress"],
                                                         notes="메모", data_type="DATA")
    top = res["neighbors"][0]["score"]
    assert top >= 0.99 and res["confidence"] == "high"
    monkeypatch.setitem(embed_slots.THRESHOLDS["m3"], "signature_high", 1.5)
    monkeypatch.setitem(embed_slots.THRESHOLDS["m3"], "signature_medium", 1.2)
    async with test_session_maker() as s:
        res = await similarity_svc.suggest_by_similarity(s, title="인장 시험", headers=["strain", "stress"],
                                                         notes="메모", data_type="DATA")
    assert res["confidence"] == "low"


# ---------------------------------------------------------------------------
# 5. /api/embed — 활성 슬롯의 모델 · 차원
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_embed_응답은_활성_슬롯의_model_dim_을_싣는다(slots, monkeypatch):
    from api.routes import embed as route

    out = await route.post_embed(route.EmbedRequest(texts=["배터리"], kind="query"))
    assert out["slot"] == "v1" and out["model"] == "hash-sha256-v1" and out["dim"] == len(out["vectors"][0])

    monkeypatch.setenv("AIDH_EMBED_ACTIVE", "m3")
    out = await route.post_embed(route.EmbedRequest(texts=["배터리", "battery"], kind="passage"))
    assert out["slot"] == "m3" and out["model"] == "BAAI/bge-m3" and out["dim"] == 1024
    assert len(out["vectors"]) == 2 and slots.m3.encoded == ["배터리", "battery"]

    empty = await route.post_embed(route.EmbedRequest(texts=[]))
    assert empty == {"vectors": [], "dim": 1024, "model": "BAAI/bge-m3", "slot": "m3"}


@pytest.mark.asyncio
async def test_health_는_슬롯마다_백로그를_낸다(slots, db_client, test_session_maker, monkeypatch):
    await _seed(test_session_maker, sections=("가", "나"))
    monkeypatch.setenv("AIDH_EMBED_FILL", "v1,m3")
    body = (await db_client.get("/api/system/health")).json()
    assert body["embed_backlog"] == 2 and body["embed_backlog_by_slot"] == {"v1": 2, "m3": 2}
    assert body["embed_slot"]["active"] == "v1" and body["embed_slot"]["fill"] == ["v1", "m3"]
    monkeypatch.setenv("AIDH_EMBED_ACTIVE", "m3")
    body = (await db_client.get("/api/system/health")).json()
    assert body["embedder"] == "bge_m3" and body["embed_slot"]["model"] == "BAAI/bge-m3"


@pytest.mark.asyncio
async def test_슬롯_간_질의는_스레드에서_인코딩한다(slots, pg, monkeypatch):
    """질의 인코딩이 이벤트 루프 스레드에서 돌면 백필이 쥔 모델 잠금을 기다리는 동안 프로세스가 선다."""
    seen = []
    orig = slots.v1.encode

    def spy(text):
        seen.append(threading.current_thread() is threading.main_thread())
        return orig(text)

    monkeypatch.setattr(slots.v1, "encode", spy)
    await pg.search.semantic_search(_Capture(), "낙하", top_k=3)
    assert seen == [False]
    await asyncio.sleep(0)
