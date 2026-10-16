# 임베딩 공급자 bge_m3 와 모델 잠금 시험 — 접두사 없음 · 1024차원 · 토큰 상한 · fp16 · 여러 스레드가 한 모델을 함께 쓰기
"""bge_m3 는 임베딩 슬롯 m3 의 공급자다(``embed_slots``). 실제 모델을 띄우지 않고 sentence-transformers 대역으로 본다.

모델 잠금 — HF fast tokenizer(Rust)는 여러 스레드가 함께 쓰면 'Already borrowed' 로 죽는다. dev 에서 임베딩 백필 묶음이
그렇게 실패했다(2026-10-16, 백필은 스레드에서 encode_many, 검색 질의는 다른 스레드에서 encode_query).
"""
from __future__ import annotations

import sys
import threading
import time
import types

import pytest


# ---------------------------------------------------------------------------
# 1. 공급자 bge_m3
# ---------------------------------------------------------------------------
class _FakeST:
    """sentence_transformers.SentenceTransformer 대역 — 모델을 올리지 않는다."""

    made: list = []

    def __init__(self, name, device=None):
        self.name = name
        self.device = "cuda:0" if device in (None, "cuda") else device
        self.max_seq_length = 8192
        self.halved = False
        _FakeST.made.append(self)

    def half(self):
        self.halved = True
        return self

    def get_embedding_dimension(self):
        return 1024


def _bge(monkeypatch, device=None, **env):
    from api.services import embedding

    _FakeST.made = []
    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        types.SimpleNamespace(SentenceTransformer=_FakeST))
    monkeypatch.setenv("AIDH_MODELS_DIR", "/nonexistent-models")
    if device:
        monkeypatch.setenv("EMBEDDING_DEVICE", device)
    else:
        monkeypatch.delenv("EMBEDDING_DEVICE", raising=False)
    for k in ("AIDH_BGE_M3_MAX_SEQ", "AIDH_BGE_M3_BATCH", "AIDH_BGE_M3_FP16"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return embedding._build_embedder("bge_m3")


def test_bge_m3_공급자는_접두사_없이_1024차원이고_정식_이름을_단다(monkeypatch):
    e = _bge(monkeypatch)
    assert e.dim == 1024 and e.model_id == "BAAI/bge-m3"
    assert e._wrap("배터리 스웰링", is_query=True) == "배터리 스웰링"
    assert e._wrap("배터리 스웰링", is_query=False) == "배터리 스웰링"
    assert e.name.endswith("-d1024")


def test_bge_m3_는_경로에_e5_가_있어도_접두사를_붙이지_않는다(monkeypatch, tmp_path):
    """예전 판정은 '모델 이름에 e5 가 있으면' 이었다 — 로컬 경로(/home/e5user/…)에도 걸린다."""
    from api.services import embedding

    root = tmp_path / "e5user"
    (root / "bge-m3").mkdir(parents=True)
    (root / "bge-m3" / "config.json").write_text("{}")
    _bge(monkeypatch)
    monkeypatch.setenv("AIDH_MODELS_DIR", str(root))
    e = embedding._build_embedder("bge_m3")
    assert "e5" in _FakeST.made[-1].name and e._wrap("질의", is_query=True) == "질의"


def test_bge_m3_읽는_토큰_상한과_반정밀도(monkeypatch):
    e = _bge(monkeypatch)
    m = _FakeST.made[-1]
    assert m.max_seq_length == 2048 and e.input_limit() == 2048   # 기본값 — 모델 한도 8192 보다 낮춘다
    assert m.halved is True                                      # CUDA 면 fp16
    assert e._default_batch() == 32

    e = _bge(monkeypatch, device="cpu", AIDH_BGE_M3_MAX_SEQ="4096", AIDH_BGE_M3_BATCH="8")
    m = _FakeST.made[-1]
    assert m.max_seq_length == 4096 and m.halved is False and e._default_batch() == 8  # CPU 는 fp32

    # 묶음 기본값은 장치마다 — CPU 는 묶음 하나가 모델 잠금을 오래 쥐어(2 절/초) 같은 모델의 질의가 기다린다
    e = _bge(monkeypatch, device="cpu")
    assert e._default_batch() == 4

    _bge(monkeypatch, AIDH_BGE_M3_FP16="0")
    assert _FakeST.made[-1].halved is False


def test_bge_m3_손잡이가_틀리면_이름을_대고_멈춘다(monkeypatch):
    with pytest.raises(RuntimeError, match="AIDH_BGE_M3_MAX_SEQ"):
        _bge(monkeypatch, AIDH_BGE_M3_MAX_SEQ="많이")
    with pytest.raises(RuntimeError, match="AIDH_BGE_M3_FP16"):
        _bge(monkeypatch, AIDH_BGE_M3_FP16="maybe")


def test_cpu_강등_재로드도_토큰_상한을_다시_건다(monkeypatch):
    e = _bge(monkeypatch)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("CUDA error: out of memory")
        return "ok"

    assert e._to_cpu_and_retry(flaky) == "ok"
    reloaded = _FakeST.made[-1]
    assert reloaded.device == "cpu" and reloaded.max_seq_length == 2048 and reloaded.halved is False


# ---------------------------------------------------------------------------
# 2. 스레드 안전 — 같은 모델을 백필 스레드와 질의 스레드가 함께 쓴다
# ---------------------------------------------------------------------------
class _BorrowCheck:
    """HF fast tokenizer 처럼 동시에 들어오면 'Already borrowed' 로 죽는 대역."""

    def __init__(self):
        self._busy = threading.Lock()
        self.max_seq_length = 512

    def _enter(self):
        if not self._busy.acquire(blocking=False):
            raise RuntimeError("Already borrowed")
        time.sleep(0.002)
        self._busy.release()

    def encode(self, texts, **kw):
        import numpy as np

        self._enter()
        n = len(texts) if isinstance(texts, list) else 1
        out = np.ones((n, 4), dtype="float32") / 2.0
        return out if isinstance(texts, list) else out[0]

    def tokenizer(self, text, **kw):
        self._enter()
        if isinstance(text, list):
            return {"input_ids": [t.split() for t in text]}
        return {"input_ids": text.split()}


def test_여러_스레드가_한_모델을_동시에_써도_빌림_오류가_없다():
    """dev 에서 백필 묶음이 'Already borrowed' 로 실패했다(2026-10-16) — 인코딩 · 토큰 셈을 모델마다 하나씩만."""
    from api.services.embedding import SentenceTransformerEmbedder

    e = SentenceTransformerEmbedder.__new__(SentenceTransformerEmbedder)
    e._uses_e5_prefix = True
    e.dim = 4
    e._model = _BorrowCheck()
    errors: list[BaseException] = []

    def work(k):
        try:
            for _ in range(20):
                if k % 3 == 0:
                    e.encode_many([f"절 {i}" for i in range(5)], batch_size=2)
                elif k % 3 == 1:
                    e.encode_query("배터리 스웰링")
                else:
                    e.input_tokens("긴 본문 " * 3)
                    e.input_tokens_many(["가 나", "다 라 마"])
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(k,)) for k in range(9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:3]


def test_encode_many_는_묶음마다_잠금을_놓고_입력_순서를_지킨다():
    from api.services.embedding import SentenceTransformerEmbedder

    class _Model:
        def __init__(self):
            self.batches = []

        def encode(self, texts, **kw):
            import numpy as np

            self.batches.append(list(texts))
            return np.asarray([[float(len(t)), 0.0] for t in texts], dtype="float32")

    e = SentenceTransformerEmbedder.__new__(SentenceTransformerEmbedder)
    e._uses_e5_prefix = False
    e._model = _Model()
    texts = ["aa", "a", "aaaa", "aaa", "aaaaa"]
    out = e.encode_many(texts, batch_size=2)
    assert [v[0] for v in out] == [2.0, 1.0, 4.0, 3.0, 5.0]           # 입력 순서 그대로
    assert e._model.batches == [["aaaaa", "aaaa"], ["aaa", "aa"], ["a"]]  # 긴 글부터 묶음 2개씩




def test_encode_many_는_글자_수가_아니라_토큰_수로_줄_세운다():
    """한국어 · 영어가 섞이면 같은 글자 수의 토큰 수가 몇 배씩 다르다 — 글자 수로 묶으면 덧채움이 79% 였다."""
    import numpy as np

    from api.services.embedding import SentenceTransformerEmbedder

    class _Model:
        max_seq_length = 100

        def __init__(self):
            self.batches = []

        def tokenizer(self, texts, **kw):
            return {"input_ids": [t.split() for t in texts]}

        def encode(self, texts, **kw):
            self.batches.append(list(texts))
            return np.zeros((len(texts), 2), dtype="float32")

    e = SentenceTransformerEmbedder.__new__(SentenceTransformerEmbedder)
    e._uses_e5_prefix = False
    e._model = _Model()
    # 토큰 6 · 1 · 2 · 1 / 글자 11 · 14 · 3 · 5
    texts = ["a b c d e f", "가나다라마바사아자차카타파하", "x y", "긴단어하나"]
    e.encode_many(texts, batch_size=2)
    assert e._model.batches == [["a b c d e f", "x y"], ["가나다라마바사아자차카타파하", "긴단어하나"]]
