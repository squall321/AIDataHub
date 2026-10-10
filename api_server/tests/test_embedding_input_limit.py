# 임베딩 모델 입력 한도(e5 512 토큰)를 넘은 사실이 조용하지 않은지 — 적재 잡·/api/embed·검색 응답의 회귀 시험
"""모델 한도라 늘릴 수 없다. 대신 예전처럼 말없이 두지 않는다.

- 적재(passage) — 긴 절은 앞부분만 벡터에 들어가 뒤쪽 내용으로는 영영 안 찾힌다 → 잡 결과·경고.
- 질의(query) — 긴 질문·브리프는 앞부분만 검색에 쓰인다 → agent_search·recommend_agents 응답.
- /api/embed — 예전 2,000자 선절단(말없음)을 걷고, 넘친 텍스트의 순번을 돌려준다.
"""
from __future__ import annotations

import logging
import types

import pytest

from api.services.embedding import HashEmbedder


class _LimitedHash(HashEmbedder):
    """HashEmbedder 벡터 + 단어 수를 토큰으로 세는 작은 한도(6). 실제 e5 를 띄우지 않는다."""

    name = "limited-hash"

    def __init__(self) -> None:
        self.seen: list[str] = []

    def encode(self, text):
        self.seen.append(text)
        return super().encode(text)

    def input_limit(self):
        return 6

    def input_tokens(self, text, *, is_query=False):
        return len((text or "").split()) + 2


def test_sentence_transformer_counts_tokens_with_its_tokenizer():
    """e5 어댑터 — 한도는 모델의 max_seq_length, 토큰은 모델 토크나이저(접두 포함)로 센다."""
    from api.services.embedding import SentenceTransformerEmbedder, input_overflow

    e = SentenceTransformerEmbedder.__new__(SentenceTransformerEmbedder)
    e._uses_e5_prefix = True
    e.name = "st-fake"
    e._model = types.SimpleNamespace(
        max_seq_length=8,
        tokenizer=lambda text, **kw: {"input_ids": ["<s>", *text.split(), "</s>"]},
    )
    assert input_overflow(e, "짧은 질의", is_query=True) is None
    long = "긴 " * 20
    ov = input_overflow(e, long, is_query=True)
    assert ov == {"tokens": 23, "max_tokens": 8, "chars": len(long), "model": "st-fake"}


def test_hash_embedder_has_no_limit():
    from api.services.embedding import input_overflow

    assert input_overflow(HashEmbedder(), "가 " * 5000) is None


@pytest.mark.asyncio
async def test_embed_route_no_char_cut_and_reports_overflow(monkeypatch):
    from api.routes import embed as route
    from api.services import embedding

    fake = _LimitedHash()
    monkeypatch.setattr(embedding, "get_embedder", lambda: fake)
    long = "토큰 " * 1500                      # 4,500자 — 예전엔 2,000자에서 말없이 잘렸다
    out = await route.post_embed(route.EmbedRequest(texts=["짧다", long], kind="passage"))
    assert len(out["vectors"]) == 2
    assert max(len(t) for t in fake.seen) == len(long), "임베더에 닿기 전에 글자로 잘렸다"
    assert out["over_limit"] == [1] and out["max_tokens"] == 6


@pytest.mark.asyncio
async def test_embed_job_reports_sections_over_the_model_limit(
    monkeypatch, test_engine, test_session_maker, caplog
):
    from api.db import base as db_base
    from api.db.models import Record, RecordSection
    from api.services import embedding, jobs

    fake = _LimitedHash()
    monkeypatch.setattr(embedding, "get_embedder", lambda: fake)
    monkeypatch.setattr(db_base, "engine", test_engine)       # 실 DB 엔진을 절대 쓰지 않는다
    async with test_session_maker() as s:
        s.add(Record(id="DOC-HE-CAE-2026-0000000444", data_type="DOC", team="HE", group="CAE",
                     year=2026, seq=444, title="t", summary="", tags=[], agents=[],
                     content={"raw": "x"}))
        await s.flush()
        s.add(RecordSection(record_id="DOC-HE-CAE-2026-0000000444", section_id="1", level=1,
                            title="짧은 절", content_text="짧은 절 본문"))
        s.add(RecordSection(record_id="DOC-HE-CAE-2026-0000000444", section_id="2", level=1,
                            title="긴 절", content_text="긴 절 본문 " * 50))
        await s.commit()

    caplog.set_level(logging.WARNING, logger="api.services.jobs")
    job = jobs.Job(id="t-embed", kind="embed", payload={"record_id": "DOC-HE-CAE-2026-0000000444"})
    res = await jobs.embed_handler(job)
    assert res["sections_processed"] == 2
    assert res["over_model_limit"] == 1 and res["model_max_tokens"] == 6
    assert any("input limit" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_agent_search_says_when_the_query_overflows(monkeypatch, test_session_maker):
    from api import mcp_runtime
    from api.db.models import Agent, Record, RecordSection
    from api.services import embedding

    fake = _LimitedHash()
    monkeypatch.setattr(embedding, "get_embedder", lambda: fake)
    monkeypatch.setattr(mcp_runtime, "SessionLocal", test_session_maker)
    async with test_session_maker() as s:
        s.add(Agent(agent_type="seat-q", name="q", description="d", common_tags=[], data_types=["DOC"]))
        s.add(Record(id="DOC-HE-CAE-2026-0000000445", data_type="DOC", team="HE", group="CAE",
                     year=2026, seq=445, title="t", summary="", tags=[], agents=["seat-q"],
                     content={"raw": "x"}))
        await s.flush()
        s.add(RecordSection(record_id="DOC-HE-CAE-2026-0000000445", section_id="1", level=1,
                            title="절", content_text="본문", embedding=fake.encode("본문"),
                            embedding_model=fake.name))
        await s.commit()

    short = await mcp_runtime.agent_search(agent_type="seat-q", q="본문", mode="semantic")
    assert short["query_over_model_limit"] is None
    long_q = "낙하 충격 코너 응력 PSA 박리 침수 경로 분석"
    out = await mcp_runtime.agent_search(agent_type="seat-q", q=long_q, mode="hybrid")
    ov = out["query_over_model_limit"]
    assert ov and ov["max_tokens"] == 6 and ov["tokens"] > 6

    rec = await mcp_runtime.recommend_agents(q=long_q, top_k_tools=0)
    assert rec["query_over_model_limit"] and rec["query_over_model_limit"]["max_tokens"] == 6
