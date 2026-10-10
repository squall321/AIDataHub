# 크로스인코더 재정렬이 모델 입력 상한에서 읽고 이벤트 루프를 붙들지 않는지 보는 회귀 시험(GA-4)
"""크로스인코더(AIDH_RERANK_PROVIDER=bge_m3) 재정렬의 입력 길이와 실행 자리.

히트 본문이 절 전문이 된 뒤(bb075bf) 재정렬 입력도 200자에서 절 전문이 됐다. 그런데 CrossEncoder 를
max_length 없이 만들어 bge-reranker-v2-m3 의 한도 8192토큰까지 읽었고(같은 쌍 수의 연산이 200자 시절의
8~16배), predict 는 async 함수 안의 동기 호출이라 그동안 AIDataHub 의 다른 요청이 전부 멈췄다
(uvicorn 은 워커 하나다). 고친 뒤의 계약:

- 모델 입력 상한은 AIDH_RERANK_MAX_LENGTH(기본 512)다. 히트 본문은 그대로 전문이다 — 상한은 순위를
  매기는 모델이 쌍의 앞 몇 토큰을 읽느냐일 뿐이고, 넘친 쌍의 수는 WARNING 으로 남긴다.
- predict 는 이벤트 루프 밖(스레드)에서 돈다 — semantic_search 의 두 반환 경로와 hybrid_search 모두.
- 스레드로 옮겨 생기는 경합(첫 동시 호출의 이중 적재·적재 중 호출의 무재정렬 반환)이 없다.
- hybrid 는 semantic 단계가 같은 질의로 이미 매긴 히트를 다시 매기지 않는다(결과 순서는 같다).

입력은 생산자 실제 함수(semantic_search·hybrid_search)로 만든다. DB 는 가짜 세션으로 대신한다 —
이 박스는 aiosqlite 가 없어 SQLite 픽스처가 건너뛰어지므로, 가짜 세션이 PG·SQLite 두 경로의 행을 낸다.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import sys
import threading
import time
import types
from types import SimpleNamespace

import pytest

# 카드 절 본문 — '점수=N' 표식이 가짜 모델의 로짓이다. 길이는 512 를 넘긴다(1자=1토큰 가짜 토크나이저).
_PAD = "공칭 압축률은 하한이 아니라 하한 + 공차 여유로 설정한다. " * 30


def _body(tag: str, logit: float) -> str:
    return f"[{tag}] 점수={logit} " + _PAD + f"끝표식-{tag}"


def _logit_of(passage: str) -> float:
    m = re.search(r"점수=(-?[0-9.]+)", passage)
    return float(m.group(1)) if m else 0.0


def _sig(x: float) -> float:
    return round(1.0 / (1.0 + math.exp(-x)), 6)


class _CharTokenizer:
    """1자 = 1토큰 가짜 토크나이저 — 쌍(질의, 본문)의 길이를 len(q)+len(p)+4 로 센다."""

    def __call__(self, text, text_pair=None, **kw):
        assert kw.get("truncation") is False, "셈은 자르지 않은 길이여야 한다"
        return {"input_ids": [[0] * (len(a) + len(b) + 4) for a, b in zip(text, text_pair)]}


def _install_fake_ce(monkeypatch, *, load_delay: float = 0.0, on_predict=None):
    """sentence_transformers.CrossEncoder 를 가짜로 바꾼다. 만든 인자·predict 호출을 rec 에 남긴다."""
    rec = SimpleNamespace(inits=[], calls=[], active=0, max_active=0)
    guard = threading.Lock()

    class FakeCrossEncoder:
        def __init__(self, name, **kw):
            if load_delay:
                time.sleep(load_delay)
            rec.inits.append(kw)
            self.max_seq_length = kw.get("max_length") or 8192
            self.tokenizer = _CharTokenizer()

        def predict(self, pairs, batch_size=16, show_progress_bar=False):
            try:
                asyncio.get_running_loop()
                on_loop = True
            except RuntimeError:
                on_loop = False
            with guard:
                rec.active += 1
                rec.max_active = max(rec.max_active, rec.active)
            try:
                rec.calls.append({"passages": [p for _, p in pairs], "on_loop": on_loop})
                if on_predict is not None:
                    on_predict()
                return [_logit_of(p) for _, p in pairs]
            finally:
                with guard:
                    rec.active -= 1

    mod = types.ModuleType("sentence_transformers")
    mod.CrossEncoder = FakeCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)
    return rec


@pytest.fixture
def ce_on(monkeypatch):
    """재정렬을 켜고 모듈의 적재 상태를 비운다(시험마다 새로 적재)."""
    from api.services import rerank

    monkeypatch.setenv("AIDH_RERANK_PROVIDER", "bge_m3")
    monkeypatch.delenv("AIDH_RERANK_MAX_LENGTH", raising=False)
    monkeypatch.delenv("AIDH_RERANK_OVERFETCH", raising=False)
    monkeypatch.delenv("AIDH_SEARCH_TOP_K_MAX", raising=False)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.setattr(rerank, "_model", None)
    monkeypatch.setattr(rerank, "_load_attempted", False)
    monkeypatch.setattr(rerank, "_load_failed", False)
    return rerank


# ---------------------------------------------------------------------------
# 가짜 세션 — semantic_search 의 PG·SQLite 두 경로에 행을 낸다(SQL 은 실행하지 않는다)
# ---------------------------------------------------------------------------
class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, dialect: str, rows):
        self._bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect))
        self._rows = rows

    def get_bind(self):
        return self._bind

    async def execute(self, _stmt):
        return _Result(self._rows)


def _pg_session(bodies: list[tuple[str, str]]) -> _FakeSession:
    """[(section_id, body)] — 거리 오름차순(앞이 코사인 상위)으로 낸다."""
    rows = [
        SimpleNamespace(
            section_pk=i, section_id=sid, record_id=f"R-{sid}", section_title=f"절 {sid}",
            content_text=body, section_path=None, figure_refs=None, table_refs=None,
            title=f"문서 {sid}", data_type="DOC", tags=[], distance=0.10 + 0.01 * i,
        )
        for i, (sid, body) in enumerate(bodies)
    ]
    return _FakeSession("postgresql", rows)


def _sqlite_session(bodies: list[tuple[str, str]]) -> _FakeSession:
    from api.services.embedding import get_embedder

    emb = get_embedder()
    rows = []
    for sid, body in bodies:
        sec = SimpleNamespace(embedding=emb.encode(body), section_id=sid, title=f"절 {sid}",
                              content_text=body, section_path=None, figure_refs=None, table_refs=None)
        rec = SimpleNamespace(id=f"R-{sid}", title=f"문서 {sid}", data_type="DOC", tags=[])
        rows.append((sec, rec))
    return _FakeSession("sqlite", rows)


_SEM = [("S1", _body("S1", 5.0)), ("S2", _body("S2", 4.0)), ("S3", _body("S3", 3.0)),
        ("S4", _body("S4", 2.0)), ("S5", _body("S5", 1.0))]


# ---------------------------------------------------------------------------
# 1) 모델 입력 상한
# ---------------------------------------------------------------------------
def test_cross_encoder_is_built_with_input_limit_512(ce_on, monkeypatch):
    rec = _install_fake_ce(monkeypatch)
    hits = [{"record_id": "r", "section_id": "1", "score": 0.9, "snippet": _body("A", 1.0)}]
    ce_on.maybe_rerank("공차", hits)
    assert rec.inits, "모델이 적재되지 않았다"
    assert rec.inits[0].get("max_length") == 512, (
        f"CrossEncoder 를 max_length 없이 만들었다({rec.inits[0]}) — bge-reranker-v2-m3 는 8192토큰까지 읽는다")


def test_input_limit_follows_env(ce_on, monkeypatch):
    monkeypatch.setenv("AIDH_RERANK_MAX_LENGTH", "1024")
    rec = _install_fake_ce(monkeypatch)
    ce_on.maybe_rerank("공차", [{"record_id": "r", "section_id": "1", "snippet": "x"}])
    assert rec.inits[0].get("max_length") == 1024


def test_over_limit_pairs_are_warned_and_bodies_stay_whole(ce_on, monkeypatch, caplog):
    rec = _install_fake_ce(monkeypatch)
    long_a, long_b, short = _body("A", 2.0), _body("B", 3.0), "[C] 점수=1.0 짧은 절"
    hits = [{"record_id": "r", "section_id": s, "score": 0.9, "snippet": b}
            for s, b in (("a", long_a), ("b", long_b), ("c", short))]
    with caplog.at_level(logging.WARNING, logger="api.services.rerank"):
        out = ce_on.maybe_rerank("공차", hits)
    # 모델에는 자르지 않은 본문을 준다(자르는 것은 모델의 max_length) — 셈이 원문 길이를 봐야 해서다.
    assert rec.calls[0]["passages"] == [long_a, long_b, short]
    # 응답 본문은 전문 그대로다.
    assert {h["section_id"]: h["snippet"] for h in out} == {"a": long_a, "b": long_b, "c": short}
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    hit = [m for m in msgs if "2/3" in m and "512" in m and "AIDH_RERANK_MAX_LENGTH" in m]
    assert hit, f"넘친 쌍 수를 말하지 않았다: {msgs}"


def test_no_warning_when_all_pairs_fit(ce_on, monkeypatch, caplog):
    _install_fake_ce(monkeypatch)
    hits = [{"record_id": "r", "section_id": "c", "snippet": "[C] 점수=1.0 짧은 절"}]
    with caplog.at_level(logging.WARNING, logger="api.services.rerank"):
        ce_on.maybe_rerank("공차", hits)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ---------------------------------------------------------------------------
# 2) predict 는 이벤트 루프 밖에서 — semantic_search 두 경로와 hybrid_search
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("make_session", [_pg_session, _sqlite_session], ids=["pg", "sqlite"])
async def test_semantic_search_reranks_off_event_loop(ce_on, monkeypatch, make_session):
    from api.services import search_svc

    rec = _install_fake_ce(monkeypatch)
    hits = await search_svc.semantic_search(make_session(_SEM), "공차", top_k=3)
    assert rec.calls, "재정렬이 걸리지 않았다"
    assert not any(c["on_loop"] for c in rec.calls), "predict 가 이벤트 루프 스레드에서 돌았다"
    # 재정렬 결과는 그대로다 — 로짓 순(S1>S2>S3), 본문 전문.
    assert [h["section_id"] for h in hits] == ["S1", "S2", "S3"]
    assert all(h["snippet"] == dict(_SEM)[h["section_id"]] for h in hits)
    assert [h["rerank_score"] for h in hits] == [_sig(5.0), _sig(4.0), _sig(3.0)]


async def test_semantic_search_rerank_lets_other_requests_run(ce_on, monkeypatch):
    """증상 그대로 — predict 가 도는 동안 같은 루프의 다른 코루틴이 돌 수 있어야 한다."""
    from api.services import search_svc

    started, released = threading.Event(), threading.Event()
    seen: list[bool] = []

    def on_predict():
        started.set()
        seen.append(released.wait(timeout=2.0))  # 루프가 막혀 있으면 아무도 풀어 주지 못한다

    _install_fake_ce(monkeypatch, on_predict=on_predict)

    async def other_request():
        while not started.is_set():
            await asyncio.sleep(0.005)
        released.set()

    other = asyncio.create_task(other_request())
    await search_svc.semantic_search(_pg_session(_SEM), "공차", top_k=3)
    released.set()
    await other
    assert seen == [True], "predict 가 도는 동안 다른 요청이 멈췄다(이벤트 루프를 붙들었다)"


def _fts_items():
    """fts_search 와 같은 모양 — S3 는 시맨틱과 겹치고 F2·F3 는 FTS 에서만 온다."""
    def item(sid, body):
        return {"record_id": f"R-{sid}", "title": f"문서 {sid}", "data_type": "DOC", "section_id": sid,
                "section_title": f"절 {sid}", "snippet": body, "tags": []}
    return [item("S3", dict(_SEM)["S3"]), item("F2", _body("F2", 4.5)), item("F3", _body("F3", 0.5))]


async def test_hybrid_reranks_off_event_loop_and_does_not_rescore_semantic_hits(ce_on, monkeypatch):
    from api.services import search_svc

    rec = _install_fake_ce(monkeypatch)

    async def fake_fts(_s, q, *, limit, record_ids=None, data_types=None):
        return (_fts_items(), 3)

    monkeypatch.setattr(search_svc, "fts_search", fake_fts)
    out = await search_svc.hybrid_search(_pg_session(_SEM), "공차", top_k=3)

    assert rec.calls and not any(c["on_loop"] for c in rec.calls), "predict 가 이벤트 루프 스레드에서 돌았다"
    # semantic 단계 1번(시맨틱 후보 5) + hybrid 단계 1번 — hybrid 단계는 FTS 에서만 온 F2·F3 만 매긴다.
    assert len(rec.calls) == 2, rec.calls
    hybrid_stage = sorted(p.split(" ")[0] for p in rec.calls[1]["passages"])
    assert hybrid_stage == ["[F2]", "[F3]"], f"semantic 단계가 매긴 히트를 다시 매겼다: {hybrid_stage}"
    # 결과는 전부 다시 매긴 것과 같다 — RRF 상위 6(S3,S1,S2,F2,F3,S4)을 로짓 순으로 → S1, F2, S2.
    assert [h["section_id"] for h in out] == ["S1", "F2", "S2"]
    assert [h["score"] for h in out] == [_sig(5.0), _sig(4.5), _sig(4.0)]
    assert out[1]["snippet"] == _body("F2", 4.5)


# ---------------------------------------------------------------------------
# 3) 스레드로 옮겨 생기는 경합 — 첫 동시 호출
# ---------------------------------------------------------------------------
def test_concurrent_first_calls_load_once_and_both_rerank(ce_on, monkeypatch):
    rec = _install_fake_ce(monkeypatch, load_delay=0.3)
    hits = [{"record_id": "r", "section_id": s, "snippet": _body(s, lg)} for s, lg in (("a", 1.0), ("b", 2.0))]
    outs: list = [None, None]

    def call(i):
        outs[i] = ce_on.maybe_rerank("공차", [dict(h) for h in hits])

    ts = [threading.Thread(target=call, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    assert len(rec.inits) == 1, f"모델을 {len(rec.inits)}번 적재했다"
    for o in outs:
        assert [h["section_id"] for h in o] == ["b", "a"] and all("rerank_score" in h for h in o), (
            "적재 중에 들어온 호출이 재정렬 없이 돌아갔다")
    assert rec.max_active == 1, "predict 가 겹쳐 돌았다(같은 토크나이저 설정을 엇갈려 바꾼다)"


# ---------------------------------------------------------------------------
# 4) 실제 토크나이저로 셈 — 설치된 transformers 의 호출 모양이 맞는지(모델 캐시가 없으면 건너뜀)
# ---------------------------------------------------------------------------
def _local_reranker() -> str | None:
    """받아 둔 bge-reranker-v2-m3 폴더 — 운영과 같은 순서(AIDH_MODELS_DIR, 다음 HF 캐시). 없으면 None."""
    from api.services.embedding import _local_or_repo

    name = _local_or_repo("BAAI/bge-reranker-v2-m3")
    if os.path.isdir(name):
        return name
    try:
        from huggingface_hub import try_to_load_from_cache

        hit = try_to_load_from_cache("BAAI/bge-reranker-v2-m3", "tokenizer.json")
    except Exception:  # noqa: BLE001 — transformers 가 없는 박스
        return None
    return os.path.dirname(hit) if isinstance(hit, str) else None


@pytest.mark.skipif(_local_reranker() is None, reason="bge-reranker-v2-m3 토크나이저가 받아 둔 곳에 없다")
def test_overflow_count_with_real_tokenizer(ce_on):
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(_local_reranker(), model_max_length=512)
    model = SimpleNamespace(tokenizer=tok, max_seq_length=512)
    over = ce_on._input_overflow(model, "공차", [_PAD * 2, "짧은 절"])
    assert over is not None and over["pairs_over"] == 1 and over["pairs"] == 2
    assert over["max_tokens"] == 512 and over["longest"] > 512
