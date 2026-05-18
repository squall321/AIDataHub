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
"""
from __future__ import annotations

import hashlib
import logging
import os
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
    recommended_similarity_threshold: float = 0.85

    @abstractmethod
    def encode(self, text: str) -> list[float]:
        """단일 텍스트 → 길이 :attr:`dim` 의 ``list[float]``."""

    def encode_many(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.encode(t) for t in texts]


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

    def __init__(self, model_name: str | None = None) -> None:
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
        # E5 계열 prefix 자동 적용 여부 (모델 이름에 'e5' 포함 시 ON)
        self._uses_e5_prefix = "e5" in resolved_name.lower()
        self._model_name = resolved_name
        self._model = SentenceTransformer(resolved_name)
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
                f"model '{resolved_name}' dim={actual_dim} 가 EMBEDDING_DIM={self.dim} "
                f"와 다름 — 마이그레이션 0004 의 vector({self.dim}) 와 정합 안 됨. "
                f"384차원 모델 (예: intfloat/multilingual-e5-small) 을 사용하거나 "
                f"마이그레이션을 갱신할 것."
            )

    def _wrap(self, text: str, *, is_query: bool) -> str:
        text = (text or "")[:8000]
        if not self._uses_e5_prefix:
            return text
        return f"{'query' if is_query else 'passage'}: {text}"

    def encode(self, text: str) -> list[float]:
        wrapped = self._wrap(text, is_query=False)
        if not wrapped.strip():
            return [0.0] * self.dim
        v = self._model.encode(wrapped, normalize_embeddings=True)
        return v.astype("float32").tolist()

    def encode_query(self, text: str) -> list[float]:
        """검색 질의용 임베딩 (E5 prefix=query)."""
        wrapped = self._wrap(text, is_query=True)
        if not wrapped.strip():
            return [0.0] * self.dim
        v = self._model.encode(wrapped, normalize_embeddings=True)
        return v.astype("float32").tolist()

    def encode_many(self, texts: Sequence[str]) -> list[list[float]]:
        # 배치 처리로 추론 비용 대폭 감소 (CPU 8core, batch=32 시 ~6ms/record)
        wrapped = [self._wrap(t, is_query=False) for t in texts]
        if not wrapped:
            return []
        vecs = self._model.encode(
            wrapped,
            normalize_embeddings=True,
            batch_size=32,
            show_progress_bar=False,
        )
        return [v.astype("float32").tolist() for v in vecs]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def get_embedder() -> Embedder:
    """``EMBEDDING_PROVIDER`` env 에 따라 embedder 인스턴스를 반환.

    - ``"openai"`` → :class:`OpenAIEmbedder`
    - ``"e5_small"`` → :class:`SentenceTransformerEmbedder` (multilingual-e5-small)
    - ``"sentence_transformers"`` → :class:`SentenceTransformerEmbedder`
      (``SENTENCE_TRANSFORMER_MODEL`` env 또는 default)
    - 그 외/미설정 → :class:`HashEmbedder` (default)

    외부 의존 경로 (openai / sentence_transformers) 에서 패키지·키가 없는
    경우 :class:`RuntimeError` 가 raise 되며, caller 측은 fallback 여부를
    결정해야 한다 (본 함수는 silent fallback 하지 않는다 — 운영 환경에서
    의도치 않게 hash 로 떨어지는 것을 막기 위함).
    """
    provider = (os.environ.get("EMBEDDING_PROVIDER") or "hash").strip().lower()
    if provider == "openai":
        return OpenAIEmbedder()
    if provider == "e5_small":
        return SentenceTransformerEmbedder(_local_or_repo("intfloat/multilingual-e5-small"))
    if provider == "e5_base":
        return SentenceTransformerEmbedder(_local_or_repo("intfloat/multilingual-e5-base"))
    if provider == "e5_large":
        return SentenceTransformerEmbedder(_local_or_repo("intfloat/multilingual-e5-large"))
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
]
