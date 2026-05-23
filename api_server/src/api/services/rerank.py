"""Cross-encoder rerank — semantic top-N 의 false-positive 차단용.

활성화:
    env ``AIDH_RERANK_PROVIDER=bge_m3``  → BGE Reranker v2 m3 (multilingual, 568MB)
    env 미설정 또는 다른 값 → ``rerank()`` 가 no-op (입력 그대로 반환).
    기본 off → 회귀 0.

설계:
    - 첫 호출 시 모델 lazy load. 같은 프로세스 내에서 모듈 캐시 (싱글톤).
    - sentence-transformers ``CrossEncoder`` 사용 (transformers 위에 얇은 래퍼).
    - 입력: query + hits 리스트 ([{record_id, section_id, snippet/content_text/...}]).
    - 점수: query-passage 코사인이 아닌 "직접 추론한 relevance" (0..1 정규화는
      sigmoid). 본 점수를 ``rerank_score`` 키로 추가하고, 이를 기준으로
      내림차순 재정렬. 원 ``score`` 는 ``score_pre_rerank`` 에 보존.
    - 모델 미설치/로드 실패 → 경고 로그 후 no-op (검색 자체는 항상 응답).

사용:
    >>> from api.services.rerank import maybe_rerank
    >>> hits = await semantic_search(...)
    >>> hits = maybe_rerank(query, hits, top_k=10)  # provider off 이면 즉시 반환.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


_model: Any = None  # lazy singleton
_load_attempted: bool = False
_load_failed: bool = False


def _provider() -> str:
    return (os.environ.get("AIDH_RERANK_PROVIDER") or "").strip().lower()


def _is_enabled() -> bool:
    return _provider() in ("bge_m3", "bge-m3", "bge_reranker_v2_m3")


def _load_model() -> Any | None:
    """싱글톤 lazy load. 실패 시 ``None`` 반환 + _load_failed=True."""
    global _model, _load_attempted, _load_failed
    if _load_attempted:
        return _model
    _load_attempted = True
    try:
        from sentence_transformers import CrossEncoder  # type: ignore[import-not-found]
    except ImportError as e:
        log.warning("rerank: sentence-transformers 미설치 — disabling (%s)", e)
        _load_failed = True
        return None
    try:
        # 로컬 캐시 우선 — embedding.py 의 _local_or_repo 와 동일 정책.
        from .embedding import _local_or_repo
        name = _local_or_repo("BAAI/bge-reranker-v2-m3")
    except Exception:
        name = "BAAI/bge-reranker-v2-m3"
    try:
        log.info("rerank: loading cross-encoder %s", name)
        _model = CrossEncoder(name)
    except Exception as e:  # pragma: no cover — 모델 미다운로드/네트워크 폐쇄망
        log.warning("rerank: model load failed — disabling (%s)", e)
        _model = None
        _load_failed = True
    return _model


def maybe_rerank(
    query: str,
    hits: list[dict[str, Any]],
    *,
    top_k: int | None = None,
    text_field_priority: tuple[str, ...] = ("content_text", "snippet", "title"),
) -> list[dict[str, Any]]:
    """hits 를 cross-encoder 점수 기준으로 재정렬.

    provider 미설정 또는 모델 load 실패 시 즉시 입력 반환.

    Args:
        query: 사용자 질의.
        hits: 후보 결과들. 텍스트는 ``text_field_priority`` 순으로 첫 non-empty 사용.
        top_k: rerank 후 자를 상한. None 이면 입력 길이 보존.
        text_field_priority: passage 텍스트 추출 후보 키 순서.

    Returns:
        재정렬된 hits. 각 항목에 ``rerank_score`` 추가, 원 ``score`` 는
        ``score_pre_rerank`` 에 보존. 미활성 시 입력 그대로.
    """
    if not _is_enabled() or not hits or not (query or "").strip():
        return hits

    model = _load_model()
    if model is None or _load_failed:
        return hits

    # 각 hit 에서 passage 텍스트 추출
    passages: list[str] = []
    for h in hits:
        text = ""
        for key in text_field_priority:
            v = h.get(key)
            if isinstance(v, str) and v.strip():
                text = v
                break
        passages.append(text or "")

    pairs = [(query, p) for p in passages]
    try:
        # CrossEncoder.predict 는 score array 반환 (raw logits 또는 정규화 점수).
        raw = model.predict(pairs, batch_size=16, show_progress_bar=False)
    except Exception as e:  # pragma: no cover
        log.warning("rerank: predict failed — returning original order (%s)", e)
        return hits

    # 점수 정규화 — sigmoid 로 [0,1]. logits 일 때만 의미 있음; 이미 [0,1] 면 거의 그대로.
    import math

    rescored: list[tuple[float, dict[str, Any]]] = []
    for h, s in zip(hits, raw):
        try:
            sf = float(s)
        except Exception:
            sf = 0.0
        norm = 1.0 / (1.0 + math.exp(-sf))
        # 원 score 보존 + rerank 점수 부착
        h2 = dict(h)
        if "score" in h2 and "score_pre_rerank" not in h2:
            h2["score_pre_rerank"] = h2["score"]
        h2["rerank_score"] = round(norm, 6)
        h2["score"] = round(norm, 6)  # 통일 — 후속 임계 검사 일관성
        rescored.append((norm, h2))

    rescored.sort(key=lambda t: t[0], reverse=True)
    out = [item for _, item in rescored]
    if top_k is not None:
        out = out[: max(1, int(top_k))]
    return out


__all__ = ["maybe_rerank"]
