# GPU OOM CPU 폴백 경로 — 서비스를 살리려는 그 경로가 NameError 로 죽지 않는지
import sys
import types

import pytest


def _embedder_cls():
    from api.services.embedding import SentenceTransformerEmbedder
    return SentenceTransformerEmbedder


def test_cpu_fallback_logs_and_retries_without_nameerror(monkeypatch):
    """⚠ 회귀 방지 — 예전엔 이 줄이 `log.warning` 이었다. 이 모듈에는 `log` 가 없어서
    GPU OOM 이 나는 순간(=폴백이 필요한 바로 그 순간) NameError 로 죽었다."""
    cls = _embedder_cls()
    e = cls.__new__(cls)          # __init__ 를 건너뛴다(모델 로드 불필요)
    e._name = "dummy/model"

    loaded = []

    class _FakeST:
        def __init__(self, name, device=None):
            loaded.append((name, device))

    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        types.SimpleNamespace(SentenceTransformer=_FakeST))
    monkeypatch.setattr("api.services.embedding.SentenceTransformer", _FakeST, raising=False)

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("CUDA error: out of memory")
        return "ok"

    assert e._to_cpu_and_retry(flaky) == "ok"
    assert calls["n"] == 2                     # 실패 → CPU 재로드 → 재시도
    assert loaded == [("dummy/model", "cpu")]  # CPU 로 강등했다


def test_non_oom_errors_are_reraised_not_swallowed(monkeypatch):
    cls = _embedder_cls()
    e = cls.__new__(cls)
    e._name = "dummy/model"

    def boom():
        raise ValueError("그냥 오류")

    with pytest.raises(ValueError):
        e._to_cpu_and_retry(boom)


def test_dim_validation_is_reachable_and_guards_mismatch(monkeypatch):
    """차원 검증 블록이 __init__ 안에 있어야 실효가 있다 — 예전엔 _to_cpu_and_retry 뒤에
    도달 불가 코드로 놓여 있어 docstring 이 약속한 가드가 무력했다."""
    import inspect

    from api.services import embedding as m

    src = inspect.getsource(m.SentenceTransformerEmbedder.__init__)
    assert "EMBEDDING_DIM" in src and "actual_dim" in src
    # _to_cpu_and_retry 안에는 남아 있으면 안 된다(그쪽이면 도달 불가다).
    assert "actual_dim" not in inspect.getsource(m.SentenceTransformerEmbedder._to_cpu_and_retry)
