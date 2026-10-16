"""텍스트 → 벡터 (시맨틱 검색용 embedder).

본 모듈은 세 가지 embedder 를 제공한다:

- :class:`HashEmbedder` — 외부 의존성 없는 결정론적 해시 기반. sha256(text)
  으로 시드된 numpy 정규 분포에서 384차원 벡터를 뽑은 뒤 L2 정규화한다.
  의미적 유사도 품질은 약하지만 동일 텍스트는 항상 동일 벡터를 반환하므로
  파이프라인 정합성 검증·로컬 smoke 에 충분하다.
- :class:`OpenAIEmbedder` — OpenAI ``text-embedding-3-small`` 호출. 차원은
  ``dimensions`` 파라미터로 384 로 잘라낸다 (3-small 의 default 1536). 사용
  시 ``OPENAI_API_KEY`` 환경 변수가 필요하다.
- :class:`SentenceTransformerEmbedder` — Hugging Face sentence-transformers.
  default ``intfloat/multilingual-e5-small`` (118MB, dim=384, 100+ 언어,
  한국어 우수). CPU 추론 OK (~50ms/record), 외부 API 불필요. E5 계열은 입력에
  prefix(`passage:`/`query:`) 가 필요하며 어댑터가 자동 처리.

선택 전략 (환경 변수):
    EMBEDDING_PROVIDER=hash       → HashEmbedder (default)
    EMBEDDING_PROVIDER=openai     → OpenAIEmbedder
    EMBEDDING_PROVIDER=e5_small   → SentenceTransformerEmbedder("intfloat/multilingual-e5-small")
    EMBEDDING_PROVIDER=sentence_transformers
                                  → SentenceTransformerEmbedder(
                                       SENTENCE_TRANSFORMER_MODEL or default)

차원은 마이그레이션 0004 의 ``vector(384)`` 컬럼과 정합을 위해
:data:`EMBEDDING_DIM` (384) 으로 고정한다. 차원을 바꾸려면 반드시 컬럼
마이그레이션도 함께 갱신할 것.

``bge_m3`` 는 EMBEDDING_PROVIDER 로 고르는 것이 아니라 임베딩 슬롯 ``m3`` 의 공급자다(``embed_slots``).
차원은 모델이 정한 1024 로 고정이고 열은 ``halfvec(1024)`` 다 — EMBEDDING_DIM 과 상관없다.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from abc import ABC, abstractmethod
from collections.abc import Sequence

logger = logging.getLogger(__name__)

EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "384"))
"""임베딩 벡터 차원. 환경변수 ``EMBEDDING_DIM`` 으로 외부화 (Migration 0013).

기본값 384 는 e5_small / openai (truncated) / hash 모두와 정합. e5_base 사용
시 ``EMBEDDING_DIM=768`` + alembic 0013 의 ``vector(768)`` 컬럼을 동반해야 한다.
"""


def _local_or_repo(repo_id: str) -> str:
    """HF repo id 를 받아, 로컬에 받아둔 모델 폴더가 있으면 그 경로를,
    없으면 repo_id 를 그대로 반환한다 (폐쇄망 우선 — HF 접근 회피).

    탐색 위치: ``AIDH_MODELS_DIR`` (기본 ``/opt/models``) 아래
        - ``<basename>``                 예: multilingual-e5-base
        - ``<basename: 'multilingual-' 제거>``  예: e5-base
    유효 모델 폴더 판정 = ``config.json`` 존재.
    예) intfloat/multilingual-e5-base → /opt/models/e5-base 가 있으면 그것.
    """
    base = repo_id.split("/")[-1]               # multilingual-e5-base
    short = base.replace("multilingual-", "")   # e5-base
    root = os.environ.get("AIDH_MODELS_DIR", "/opt/models")
    for name in (short, base):
        cand = os.path.join(root, name)
        if os.path.isfile(os.path.join(cand, "config.json")):
            logger.info("embedding model: local %s (repo %s 대신)", cand, repo_id)
            return cand
    return repo_id


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
class Embedder(ABC):
    """텍스트 → 384차원 벡터.

    구현체는 :meth:`encode` 만 제공하면 된다 (:meth:`encode_many` 는 default
    루프 구현 사용). dim 은 클래스 속성으로 노출한다 — 호출자가 컬럼/벡터
    차원 정합을 사전 검증할 수 있다.

    ``recommended_similarity_threshold`` — 이 embedder 로 만든 벡터 cosine
    유사도 기반 클러스터링/필터링에서 "같은 의미" 로 간주할 권장 임계값.
    embedder 마다 baseline 점수가 다르므로 (Hash 는 무관 페어 0~0.1 / e5 는
    무관 페어도 0.7~0.9) caller 가 이 값을 사용하면 안전한 default 가 된다.
    """

    dim: int = EMBEDDING_DIM
    name: str = "base"
    # 모델의 정식 이름(HF 리포 id 등) — 박스마다 로컬 경로가 달라도 같다. /api/embed 응답의 model 이
    # 이것이고, 소비처(포털 대화 검색 · 에이전트 서버)가 벡터 캐시를 이 값으로 가른다.
    model_id: str = ""
    recommended_similarity_threshold: float = 0.85

    @abstractmethod
    def encode(self, text: str) -> list[float]:
        """단일 텍스트 → 길이 :attr:`dim` 의 ``list[float]``."""

    def encode_query(self, text: str) -> list[float]:
        """검색 질의용 임베딩. 비대칭 모델 (E5 등) 은 override 해서 query
        prefix 를 적용한다. 대칭 모델은 encode 와 동일하면 충분."""
        return self.encode(text)

    def encode_many(self, texts: Sequence[str], *, batch_size: int | None = None) -> list[list[float]]:
        return [self.encode(t) for t in texts]

    # 모델 입력 한도 — 넘친 뒤쪽은 벡터에 들어가지 않는다. 이 한도는 **모델의 것**이라
    # 우리가 늘릴 수 없고, 대신 넘쳤다는 사실을 조용히 두지 않는다(input_overflow 참조).
    def input_limit(self) -> int | None:
        """모델이 한 번에 읽는 최대 입력 토큰. 한도가 없거나 모르면 None."""
        return None

    def input_tokens(self, text: str, *, is_query: bool = False) -> int | None:
        """이 텍스트가 모델 입력(접두 포함)으로 몇 토큰인가. 셀 수 없으면 None."""
        return None

    def input_tokens_many(self, texts: Sequence[str]) -> list[int | None]:
        """여러 글(적재 쪽)의 토큰 수 — 백필이 한 청크를 한 번에 센다."""
        return [self.input_tokens(t) for t in texts]


def input_overflow(embedder: Embedder, text: str, *, is_query: bool = False) -> dict | None:
    """모델 입력 한도를 넘으면 ``{tokens, max_tokens, chars, model}``, 아니면 None.

    e5 계열은 512 토큰(한글 대략 600~900자)까지만 읽고 나머지를 **말없이** 버린다. 적재(passage)
    에서는 긴 절의 뒤쪽 내용으로는 영영 안 찾히고, 질의(query)에서는 긴 질문·브리프의 앞부분만
    검색에 쓰인다. 호출부는 이 값으로 그 사실을 응답·잡 결과·로그에 남긴다.
    """
    try:
        limit = embedder.input_limit()
        if not limit:
            return None
        n = embedder.input_tokens(text or "", is_query=is_query)
    except Exception:  # noqa: BLE001 — 세기 실패가 검색·적재를 막아선 안 된다
        return None
    if n is None or n <= limit:
        return None
    return {"tokens": int(n), "max_tokens": int(limit), "chars": len(text or ""),
            "model": getattr(embedder, "name", "")}


# ---------------------------------------------------------------------------
# HashEmbedder (default, dependency-light)
# ---------------------------------------------------------------------------
class HashEmbedder(Embedder):
    """결정론적 해시 기반 embedder — 외부 API 불필요.

    sha256(text) 의 첫 4바이트를 numpy 의 ``default_rng`` seed 로 사용해
    표준 정규 분포에서 ``EMBEDDING_DIM`` 개 샘플을 뽑은 뒤 L2 정규화한다.
    동일 텍스트는 항상 동일 벡터 → 시스템 정합성 검증·smoke·CI 용으로
    충분하다. 의미적 유사도 신호는 약하므로 production 검색 품질은
    OpenAIEmbedder 또는 sentence-transformers 기반 구현 권장.
    """

    name = "hash-sha256-v1"
    model_id = "hash-sha256-v1"
    # Hash 는 무관 텍스트 cosine 이 -0.1 ~ +0.1 부근. 같은 텍스트만 1.0 →
    # 동일성 검증용 default.
    recommended_similarity_threshold: float = 0.85

    def encode(self, text: str) -> list[float]:
        import numpy as np

        digest = hashlib.sha256((text or "").encode("utf-8")).digest()
        seed = int.from_bytes(digest[:4], "big")
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(self.dim).astype("float32")
        norm = float(np.linalg.norm(v))
        if norm > 1e-12:
            v /= norm
        return v.tolist()


# ---------------------------------------------------------------------------
# OpenAIEmbedder
# ---------------------------------------------------------------------------
class OpenAIEmbedder(Embedder):
    """OpenAI ``text-embedding-3-small`` (또는 호환 모델) 기반 embedder.

    설정:
        - ``OPENAI_API_KEY`` (필수) — OpenAI API 키
        - ``OPENAI_EMBEDDING_MODEL`` (선택, default ``text-embedding-3-small``)

    텍스트는 8000자로 자른 뒤 호출한다 (길이 임계는 현재 토큰 비용 + 안전
    마진 기준의 휴리스틱 — 정밀 토큰 카운트가 필요하면 tiktoken 추가).
    차원은 OpenAI 파라미터 ``dimensions=384`` 로 강제 — 마이그레이션 0004
    컬럼 차원 384 와 정합.
    """

    name = "openai-text-embedding-3-small-d384"
    # OpenAI 3-small 은 무관 페어 baseline 0.05 ~ 0.15 — 0.80 이상이면 강한
    # 의미 일치.
    recommended_similarity_threshold: float = 0.80

    def __init__(self) -> None:
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover — 환경 의존
            raise RuntimeError(
                "openai 패키지 미설치 — `pip install openai` 후 다시 시도"
            ) from e
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY 환경 변수가 필요합니다")
        self._client = OpenAI()
        self._model = os.environ.get(
            "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
        )
        self.model_id = f"openai/{self._model}-d{self.dim}"

    def encode(self, text: str) -> list[float]:
        text = (text or "")[:8000]
        if not text.strip():
            # 빈 텍스트는 0벡터 → caller 측에서 skip 하는 것이 일반적이지만
            # 호환성을 위해 반환은 한다.
            return [0.0] * self.dim
        resp = self._client.embeddings.create(
            model=self._model,
            input=text,
            dimensions=self.dim,
        )
        return list(resp.data[0].embedding)


# ---------------------------------------------------------------------------
# SentenceTransformerEmbedder (CPU-friendly multilingual)
# ---------------------------------------------------------------------------
class SentenceTransformerEmbedder(Embedder):
    """Hugging Face sentence-transformers 기반 embedder.

    default 모델 ``intfloat/multilingual-e5-small`` 은 118MB, dim=384, 100+ 언어
    (한국어 우수). CPU 만으로 ~50ms/record (배치=32 시 ~6ms/record). 외부 API
    의존 X. 첫 로드 시 모델 자동 다운로드 (~118MB) → HuggingFace 캐시.

    E5 계열은 입력 prefix 가 필요:
        - ``passage: {텍스트}`` — 적재 시 (record 본문)
        - ``query: {질의}`` — 검색 시 (사용자 질의)

    어댑터가 :meth:`encode` 에서는 ``passage:``, :meth:`encode_query` 에서는
    ``query:`` 자동 prefix. caller 가 검색 query 를 임베딩할 때는 반드시
    :meth:`encode_query` 를 호출.

    설정 (환경 변수):
        - ``SENTENCE_TRANSFORMER_MODEL`` (선택) — model name 오버라이드
        - 기본 모델: ``intfloat/multilingual-e5-small`` (dim=384)

    의존성:
        ``pip install sentence-transformers`` (~2GB; transformers, torch 포함)
    """

    name = "sentence-transformers-e5-small-d384"
    # multilingual-e5 계열은 무관 텍스트 baseline 이 0.7~0.85 부근으로 높다.
    # 0.92 이상이면 강한 의미 일치 (실측: 같은 의미 페어 0.86~0.94 / 무관
    # 페어 0.77~0.91). caller 는 이 값을 default 로 쓰면 안전.
    recommended_similarity_threshold: float = 0.92

    def __init__(
        self,
        model_name: str | None = None,
        *,
        dim: int | None = None,
        e5_prefix: bool | None = None,
        fp16: bool = False,
        max_seq_length: int | None = None,
        batch_size: int | None = None,
        model_id: str | None = None,
    ) -> None:
        """``dim`` 을 주면 그 차원을 기대한다(없으면 EMBEDDING_DIM) — 슬롯 m3 의 bge_m3 는 1024 고정이다.

        ``e5_prefix`` 는 접두사(``query:``/``passage:``)를 붙일지다. None 이면 예전처럼 모델 이름에 e5 가
        있는지로 정한다 — 이름이 로컬 경로일 수 있어 공급자가 아는 값이면 직접 준다(bge_m3 는 False).
        ``fp16`` 은 CUDA 에 올렸을 때만 반정밀도로 돌린다(CPU 는 그대로 fp32). ``max_seq_length`` 는 모델이
        읽는 토큰 상한을 모델 기본값보다 낮출 때 준다 — 넘친 뒤쪽은 벡터에 들지 않고 input_overflow 가 그 수를 낸다.
        ``batch_size`` 는 encode_many 를 호출자가 묶음 크기 없이 부를 때의 기본값이다(없으면 AIDH_EMBED_BATCH ·
        CUDA 여부로 정한다).
        """
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover — 환경 의존
            raise RuntimeError(
                "sentence-transformers 패키지 미설치 — "
                "`pip install sentence-transformers` 후 다시 시도"
            ) from e

        resolved_name = (
            model_name
            or os.environ.get("SENTENCE_TRANSFORMER_MODEL")
            or "intfloat/multilingual-e5-small"
        )
        # E5 계열 prefix 자동 적용 여부 (모델 이름에 'e5' 포함 시 ON) — 공급자가 주면 그 값.
        self._uses_e5_prefix = ("e5" in resolved_name.lower()) if e5_prefix is None else bool(e5_prefix)
        self._model_name = resolved_name
        if dim is not None:
            self.dim = int(dim)
        self._fp16 = bool(fp16)
        self._max_seq = int(max_seq_length) if max_seq_length else None
        self._batch = int(batch_size) if batch_size else None
        self.model_id = model_id or resolved_name
        self._name = resolved_name
        # 장치 선택 — GPU 가 vLLM 등으로 점유된 박스에서 CUDA OOM 으로 임베딩이 죽는다(실측).
        # EMBEDDING_DEVICE=cpu 로 강제하거나, 미지정 시 CUDA 실패하면 CPU 로 폴백한다.
        _dev = (os.environ.get("EMBEDDING_DEVICE") or "").strip() or None
        try:
            self._model = self._tune(SentenceTransformer(resolved_name, device=_dev))
        except Exception:  # noqa: BLE001 — 장치 문제면 CPU 로 재시도(소규모 코퍼스엔 충분)
            self._model = self._tune(SentenceTransformer(resolved_name, device="cpu"))
        self.name = f"sentence-transformers-{resolved_name.split('/')[-1]}-d{self.dim}"

        # 모델의 실제 dim 검증 (다른 모델 사용 시 차원 불일치 조기 감지)
        # sentence-transformers v5+ 는 get_embedding_dimension, v4 이하는
        # get_sentence_embedding_dimension. 둘 다 시도 (deprecation 회피).
        actual_dim = self.dim
        for getter_name in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
            getter = getattr(self._model, getter_name, None)
            if getter is None:
                continue
            try:
                actual_dim = int(getter())
                break
            except Exception:  # pragma: no cover
                continue
        if actual_dim != self.dim:
            raise RuntimeError(
                f"model '{resolved_name}' dim={actual_dim} 가 기대 차원 {self.dim}(EMBEDDING_DIM 또는 슬롯의 고정 "
                f"차원) 와 다름 — 그 열의 vector({self.dim})·halfvec({self.dim}) 와 정합 안 됨. "
                f"차원이 맞는 모델을 쓰거나 마이그레이션을 갱신할 것."
            )

    def _tune(self, model):
        """읽는 토큰 상한과 반정밀도를 건다 — 첫 로드와 CPU 강등 재로드가 함께 쓴다(재로드가 상한을 잃지 않게)."""
        n = getattr(self, "_max_seq", None)
        if n:
            model.max_seq_length = n
        if getattr(self, "_fp16", False) and str(getattr(model, "device", "")).startswith("cuda"):
            model.half()
        return model

    def _guard(self) -> threading.Lock:
        """이 모델(토크나이저 포함)을 한 번에 한 호출만 쓰게 하는 잠금.

        HF fast tokenizer(Rust)는 여러 스레드가 함께 쓰면 'Already borrowed' 로 죽는다 — 셈(input_tokens,
        truncation 끔)과 인코딩(truncation 켬)이 그 설정을 서로 바꾸기 때문이다. dev 에서 백필 묶음이 이것으로
        실패했다(2026-10-16, 백필은 스레드에서 encode_many, 검색 질의는 다른 스레드에서 encode_query). 백필은
        묶음마다 잠금을 놓으므로 질의는 길어야 한 묶음을 기다린다. ``__new__`` 로 만든 시험용 객체도 쓸 수
        있게 처음 부를 때 만든다(setdefault 는 GIL 아래 원자적이다).
        """
        lock = self.__dict__.get("_lock")
        if lock is None:
            lock = self.__dict__.setdefault("_lock", threading.Lock())
        return lock

    def _to_cpu_and_retry(self, fn, *a, **kw):
        """GPU OOM 은 로드가 아니라 encode 시점에 난다(실측: 'encode failed: CUDA error: out of
        memory'). GPU 를 쓰되, 순간 점유로 실패하면 CPU 로 영구 강등해 서비스가 죽지 않게 한다.

        ``fn`` 은 재시도 때 **바뀐 모델**을 다시 읽어야 한다 — 호출부는 ``lambda: self._model.encode(…)`` 로
        넘긴다(예전엔 ``self._model.encode`` 를 넘겨 재시도가 옛 GPU 모델로 다시 돌았다)."""
        try:
            return fn(*a, **kw)
        except Exception as exc:  # noqa: BLE001
            if "out of memory" not in str(exc).lower() and "cuda" not in str(exc).lower():
                raise
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
            # ⚠ log 가 아니라 logger 다(이 모듈에 log 는 없다). 예전엔 여기서 NameError 가 나서
            # **서비스를 살리려고 만든 CPU 폴백 경로가 진입하는 순간 죽었다** — GPU OOM 때
            # 정확히 이 줄에 도달한다.
            logger.warning("embedding: GPU 실패 → CPU 로 전환 (%s)", str(exc)[:120])
            # ⚠ 여기서 다시 가져온다. SentenceTransformer 는 __init__ 의 지역 import 라 이 메서드에서는
            # 이름이 없다 — 예전엔 GPU 가 점유된 박스에서 의미검색이 전부 NameError 로 죽었다(2026-10-10).
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

            self._model = self._tune(SentenceTransformer(self._name, device="cpu"))
            return fn(*a, **kw)

    def input_limit(self) -> int | None:
        n = getattr(getattr(self, "_model", None), "max_seq_length", None)
        return int(n) if n else None

    def input_tokens(self, text: str, *, is_query: bool = False) -> int | None:
        tok = getattr(getattr(self, "_model", None), "tokenizer", None)
        if tok is None:
            return None
        with self._guard():
            ids = tok(self._wrap(text, is_query=is_query), add_special_tokens=True,
                      truncation=False, verbose=False)["input_ids"]
        return len(ids)

    def input_tokens_many(self, texts: Sequence[str]) -> list[int | None]:
        tok = getattr(getattr(self, "_model", None), "tokenizer", None)
        if tok is None or not texts:
            return [None] * len(texts)
        wrapped = [self._wrap(t, is_query=False) for t in texts]
        with self._guard():
            ids = tok(wrapped, add_special_tokens=True, truncation=False, verbose=False)["input_ids"]
        return [len(x) for x in ids]

    def _wrap(self, text: str, *, is_query: bool) -> str:
        # 8,000자 선절단은 토크나이저 비용 보호일 뿐이다 — 모델은 그 전에 max_seq_length(e5 512 토큰
        # ≒ 한글 600~900자)에서 자른다. 실효 한도는 모델 쪽이고 넘친 사실은 input_overflow 가 낸다.
        text = (text or "")[:8000]
        if not self._uses_e5_prefix:
            return text
        return f"{'query' if is_query else 'passage'}: {text}"

    def encode(self, text: str) -> list[float]:
        wrapped = self._wrap(text, is_query=False)
        if not wrapped.strip():
            return [0.0] * self.dim
        with self._guard():
            v = self._to_cpu_and_retry(lambda: self._model.encode(wrapped, normalize_embeddings=True))
        return v.astype("float32").tolist()

    def encode_query(self, text: str) -> list[float]:
        """검색 질의용 임베딩 (E5 prefix=query)."""
        wrapped = self._wrap(text, is_query=True)
        if not wrapped.strip():
            return [0.0] * self.dim
        with self._guard():
            v = self._to_cpu_and_retry(lambda: self._model.encode(wrapped, normalize_embeddings=True))
        return v.astype("float32").tolist()

    def _default_batch(self) -> int:
        # CPU 8core, batch=32 시 ~6ms/record. GPU(예: A4000) 면 batch=128~256 안전(e5 기준).
        # 공급자가 정한 값(bge_m3 — 긴 입력이라 작게)이 먼저, 그다음 ``AIDH_EMBED_BATCH``, 없으면 CUDA 여부로.
        if getattr(self, "_batch", None):
            return int(self._batch)
        batch_env = os.environ.get("AIDH_EMBED_BATCH")
        if batch_env:
            try:
                return max(1, int(batch_env))
            except ValueError:
                return 32
        try:
            import torch  # type: ignore[import-not-found]
            if torch.cuda.is_available():
                return 128
        except Exception:
            pass
        return 32

    def encode_many(self, texts: Sequence[str], *, batch_size: int | None = None) -> list[list[float]]:
        # 배치 처리로 추론 비용 대폭 감소. 묶음은 우리가 나눈다 — 묶음마다 잠금을 잡았다 놓아 검색 질의가
        # 한 묶음 넘게 기다리지 않게 한다. 긴 글부터 묶는 것은 sentence-transformers 가 안에서 하는 것과 같다
        # (한 묶음 안의 덧채움이 준다).
        wrapped = [self._wrap(t, is_query=False) for t in texts]
        if not wrapped:
            return []
        bs = max(1, int(batch_size)) if batch_size else self._default_batch()
        order = sorted(range(len(wrapped)), key=lambda i: -len(wrapped[i]))
        out: list[list[float]] = [[] for _ in wrapped]
        for start in range(0, len(order), bs):
            idx = order[start : start + bs]
            with self._guard():
                vecs = self._model.encode(
                    [wrapped[i] for i in idx],
                    normalize_embeddings=True,
                    batch_size=len(idx),
                    show_progress_bar=False,
                )
            for i, v in zip(idx, vecs, strict=True):
                out[i] = v.astype("float32").tolist()
        return out


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
# provider 별 싱글톤 캐시. SentenceTransformer 로드는 ~1GB 디스크 I/O + 수 초가
# 들기 때문에, 요청마다 재인스턴스화하면 semantic 검색이 질의당 1.6s+ 로
# 느려지고 그동안 이벤트 루프 전체가 정지한다 (실측 2026-06-10).
_EMBEDDER_CACHE: dict[str, Embedder] = {}
# 같은 공급자를 두 스레드가 동시에 처음 부르면 모델을 두 번 올린다(bge-m3 는 2.2GB) — 처음 만들 때만 잡는다.
_BUILD_LOCK = threading.Lock()

# 공급자 → 모델의 정식 이름(HF 리포 id). 로컬 폴더(/opt/models)에서 읽어도 이 이름을 model_id 로 쓴다.
PROVIDER_MODEL_IDS: dict[str, str] = {
    "e5_small": "intfloat/multilingual-e5-small",
    "e5_base": "intfloat/multilingual-e5-base",
    "e5_large": "intfloat/multilingual-e5-large",
    "bge_m3": "BAAI/bge-m3",
}

# bge_m3 가 한 번에 읽는 토큰 상한 기본값. 모델 한도는 8,192 지만 묶음 메모리와 연산이 길이에 비례해 는다.
# 코퍼스 절은 p50 1,608자 · p99 2,803자(2026-10-10)라 2,048 토큰이면 거의 모든 절을 통째로 읽는다(넘친 절 수는
# 백필 결과와 경고 로그의 over_model_limit 이 낸다). 더 읽히려면 AIDH_BGE_M3_MAX_SEQ 를 올린다.
BGE_M3_MAX_SEQ_DEFAULT = 2048
# bge_m3 encode_many 묶음 기본값 — e5(128)보다 작게 둔다. 긴 절 32개(2,048 토큰)가 fp16 에서 2GB 안쪽이다.
BGE_M3_BATCH_DEFAULT = 32


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        raise RuntimeError(f"{name}={raw!r} 는 정수가 아니다") from None
    if v <= 0:
        raise RuntimeError(f"{name}={raw!r} 는 1 이상이어야 한다")
    return v


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "on", "yes"):
        return True
    if raw in ("0", "false", "off", "no"):
        return False
    raise RuntimeError(f"{name}={raw!r} 를 모른다 — 1/0 · on/off · true/false 중 하나")


def get_embedder() -> Embedder:
    """``EMBEDDING_PROVIDER`` env 에 따라 embedder 인스턴스를 반환.

    - ``"openai"`` → :class:`OpenAIEmbedder`
    - ``"e5_small"`` → :class:`SentenceTransformerEmbedder` (multilingual-e5-small)
    - ``"sentence_transformers"`` → :class:`SentenceTransformerEmbedder`
      (``SENTENCE_TRANSFORMER_MODEL`` env 또는 default)
    - 그 외/미설정 → :class:`HashEmbedder` (default)

    같은 provider 에 대해 프로세스 당 1회만 인스턴스화 (모듈 레벨 캐시).
    실패 (패키지/키 부재) 는 캐시하지 않으므로 환경 수정 후 재시도 가능.

    외부 의존 경로 (openai / sentence_transformers) 에서 패키지·키가 없는
    경우 :class:`RuntimeError` 가 raise 되며, caller 측은 fallback 여부를
    결정해야 한다 (본 함수는 silent fallback 하지 않는다 — 운영 환경에서
    의도치 않게 hash 로 떨어지는 것을 막기 위함).

    이것은 슬롯 v1 의 임베더다. 슬롯을 아는 호출부는 ``embed_slots.embedder(slot)`` 을 쓴다.
    """
    return get_embedder_for(os.environ.get("EMBEDDING_PROVIDER") or "hash")


def get_embedder_for(provider: str) -> Embedder:
    """공급자 이름으로 임베더를 얻는다(프로세스당 한 번 만든다). 슬롯 m3 는 ``bge_m3`` 로 부른다."""
    provider = (provider or "hash").strip().lower()
    cached = _EMBEDDER_CACHE.get(provider)
    if cached is not None:
        return cached
    with _BUILD_LOCK:
        cached = _EMBEDDER_CACHE.get(provider)
        if cached is not None:
            return cached
        emb = _build_embedder(provider)
        _EMBEDDER_CACHE[provider] = emb
        return emb


def _build_embedder(provider: str) -> Embedder:
    if provider == "openai":
        return OpenAIEmbedder()
    if provider in ("e5_small", "e5_base", "e5_large"):
        repo = PROVIDER_MODEL_IDS[provider]
        return SentenceTransformerEmbedder(_local_or_repo(repo), model_id=repo)
    if provider == "bge_m3":
        # 접두사 없음(모델이 query·document 둘 다 빈 프롬프트다), 1024차원 고정, CUDA 면 fp16.
        repo = PROVIDER_MODEL_IDS[provider]
        return SentenceTransformerEmbedder(
            _local_or_repo(repo),
            dim=1024,
            e5_prefix=False,
            fp16=_env_flag("AIDH_BGE_M3_FP16", True),
            max_seq_length=_env_int("AIDH_BGE_M3_MAX_SEQ", BGE_M3_MAX_SEQ_DEFAULT),
            batch_size=_env_int("AIDH_BGE_M3_BATCH", BGE_M3_BATCH_DEFAULT),
            model_id=repo,
        )
    if provider in ("sentence_transformers", "st", "sbert"):
        return SentenceTransformerEmbedder()
    if provider not in ("hash", ""):
        logger.warning(
            "unknown EMBEDDING_PROVIDER=%r — falling back to HashEmbedder",
            provider,
        )
    return HashEmbedder()


__all__ = [
    "EMBEDDING_DIM",
    "Embedder",
    "HashEmbedder",
    "OpenAIEmbedder",
    "SentenceTransformerEmbedder",
    "get_embedder",
    "get_embedder_for",
    "input_overflow",
]
