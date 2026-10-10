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
    - 모델 입력 상한은 ``AIDH_RERANK_MAX_LENGTH``(기본 512토큰)다. 히트 본문은 자르지 않는다 —
      상한은 모델이 쌍(질의+본문)의 앞 몇 토큰을 읽고 순위를 매기느냐이고, 넘친 쌍 수는 WARNING 으로 남긴다.
    - ``maybe_rerank`` 는 동기 함수다(predict 가 수 초). async 코드에서는 ``asyncio.to_thread`` 로 부른다 —
      직접 부르면 그동안 이벤트 루프가 서서 AIDataHub 의 다른 요청이 전부 멈춘다(uvicorn 워커 하나).

사용:
    >>> from api.services.rerank import maybe_rerank
    >>> hits = await semantic_search(...)
    >>> hits = await asyncio.to_thread(maybe_rerank, query, hits, top_k=10)  # provider off 이면 즉시 반환.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

log = logging.getLogger(__name__)


_model: Any = None  # lazy singleton
_load_attempted: bool = False
_load_failed: bool = False

# 재정렬 입력 상한(토큰). 모델의 한도(bge-reranker-v2-m3 는 8192)가 아니라 **읽히는 길이**다. 히트 본문이
# 절 전문이 된 뒤(10-10) 상한 없이 두니 쌍마다 8192토큰까지 읽어, 같은 쌍 수의 연산이 200자 시절의 8~16배가
# 됐다(코퍼스 절 p50 1,608자·p99 2,803자·최대 60,038자). 512 는 BGE 리랭커 공식 사용 예의 값이고 200자 시절의
# 약 3배다. 히트 본문(snippet)은 그대로 전문이다 — 이 값은 순위를 매길 때 모델이 쌍의 앞 몇 토큰을 읽느냐다.
_DEFAULT_MAX_LENGTH = 512

# 적재·셈·predict 를 한 번에 한 호출만 한다. 호출부가 maybe_rerank 를 스레드로 돌리므로(asyncio.to_thread)
# 잠금이 없으면 (1) 첫 동시 호출이 모델을 두 번 적재하거나, 적재 중에 들어온 호출이 아직 None 인 모델을 보고
# 재정렬 없이 돌아가고, (2) 같은 fast tokenizer 의 truncation 설정을 셈(끔)과 predict(켬)가 엇갈려 바꿔
# 'Already borrowed' 로 죽거나 자르지 않은 쌍이 predict 로 간다. 모델은 하나라 겹쳐 돌려도 빨라지지 않는다.
_lock = threading.Lock()


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
        max_length = int(os.environ.get("AIDH_RERANK_MAX_LENGTH") or _DEFAULT_MAX_LENGTH)
        log.info("rerank: loading cross-encoder %s (입력 상한 %d토큰)", name, max_length)
        _model = CrossEncoder(name, max_length=max_length)
    except Exception as e:  # pragma: no cover — 모델 미다운로드/네트워크 폐쇄망
        log.warning("rerank: model load failed — disabling (%s)", e)
        _model = None
        _load_failed = True
    return _model


def _input_overflow(model: Any, query: str, passages: list[str]) -> dict | None:
    """입력 상한을 넘는 쌍(질의+본문)이 있으면 ``{pairs_over, pairs, max_tokens, longest}``, 없으면 None.

    넘친 쌍은 모델이 앞 ``max_tokens`` 토큰만 읽고 순위를 매긴다(히트 본문은 그대로다). 셀 수 없으면
    None — 셈 실패가 재정렬을 막지 않는다. ``_lock`` 안에서 부른다(predict 와 같은 토크나이저를 쓴다).
    """
    # sentence-transformers 5.x 는 max_seq_length, 3.x·4.x 는 max_length 다.
    limit = getattr(model, "max_seq_length", None) or getattr(model, "max_length", None)
    tok = getattr(model, "tokenizer", None)
    if not limit or tok is None or not passages:
        return None
    try:
        enc = tok([query] * len(passages), passages, add_special_tokens=True,
                  truncation=False, verbose=False)
        lens = [len(ids) for ids in enc["input_ids"]]
    except Exception as e:  # noqa: BLE001
        log.warning("rerank: 입력 길이를 세지 못했다 — 상한을 넘은 쌍 수를 모른다 (%s)", e)
        return None
    over = [n for n in lens if n > int(limit)]
    if not over:
        return None
    return {"pairs_over": len(over), "pairs": len(lens), "max_tokens": int(limit), "longest": max(over)}


def maybe_rerank(
    query: str,
    hits: list[dict[str, Any]],
    *,
    top_k: int | None = None,
    text_field_priority: tuple[str, ...] = ("content_text", "snippet", "title"),
    reuse_scored: bool = False,
) -> list[dict[str, Any]]:
    """hits 를 cross-encoder 점수 기준으로 재정렬.

    provider 미설정 또는 모델 load 실패 시 즉시 입력 반환. 동기 함수라 async 코드에서는
    ``asyncio.to_thread`` 로 부른다(모듈 docstring 참조).

    Args:
        query: 사용자 질의.
        hits: 후보 결과들. 텍스트는 ``text_field_priority`` 순으로 첫 non-empty 사용.
        top_k: rerank 후 자를 상한. None 이면 입력 길이 보존.
        text_field_priority: passage 텍스트 추출 후보 키 순서.
        reuse_scored: True 면 ``rerank_score`` 가 이미 붙은 히트는 다시 매기지 않고 그 점수를 쓴다.
            **같은 질의로** 매긴 점수일 때만 켠다 — hybrid_search 가 자기 semantic_search 단계의 히트에
            쓴다(같은 질의·같은 본문이면 점수도 같아서 다시 매기면 연산만 두 번이다).

    Returns:
        재정렬된 hits. 각 항목에 ``rerank_score`` 추가, 원 ``score`` 는
        ``score_pre_rerank`` 에 보존. 미활성 시 입력 그대로.
    """
    if not _is_enabled() or not hits or not (query or "").strip():
        return hits

    # 각 hit 에서 passage 텍스트 추출 — 자르지 않는다. 자르는 것은 모델의 입력 상한이고, 넘친 쌍은 센다.
    passages: list[str] = []
    for h in hits:
        text = ""
        for key in text_field_priority:
            v = h.get(key)
            if isinstance(v, str) and v.strip():
                text = v
                break
        passages.append(text or "")

    todo = [i for i, h in enumerate(hits)
            if not (reuse_scored and isinstance(h.get("rerank_score"), (int, float)))]
    raw_by_i: dict[int, Any] = {}
    with _lock:
        model = _load_model()
        if model is None or _load_failed:
            return hits
        if todo:
            over = _input_overflow(model, query, [passages[i] for i in todo])
            if over:
                log.warning(
                    "rerank: 쌍 %d/%d 개가 재정렬 입력 상한 %d토큰을 넘어 앞부분만 읽고 순위를 매겼다(가장 긴 쌍 "
                    "%d토큰). 히트 본문은 자르지 않았다 — 더 읽히려면 AIDH_RERANK_MAX_LENGTH 를 올린다(모델 한도 "
                    "8192, 연산은 길이에 비례해 는다)",
                    over["pairs_over"], over["pairs"], over["max_tokens"], over["longest"])
            pairs = [(query, passages[i]) for i in todo]
            try:
                # CrossEncoder.predict 는 score array 반환 (raw logits 또는 정규화 점수).
                raw = model.predict(pairs, batch_size=16, show_progress_bar=False)
            except Exception as e:  # pragma: no cover
                log.warning("rerank: predict failed — returning original order (%s)", e)
                return hits
            raw_by_i = dict(zip(todo, raw))

    # 점수 정규화 — sigmoid 로 [0,1]. logits 일 때만 의미 있음; 이미 [0,1] 면 거의 그대로.
    import math

    rescored: list[tuple[float, dict[str, Any]]] = []
    for i, h in enumerate(hits):
        if i in raw_by_i:
            try:
                sf = float(raw_by_i[i])
            except Exception:
                sf = 0.0
            norm = 1.0 / (1.0 + math.exp(-sf))
        else:
            norm = float(h["rerank_score"])  # reuse_scored — 같은 질의로 이미 매긴 점수
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


def rerank_enabled() -> bool:
    """크로스인코더가 켜져 있나 — 호출부가 **후보를 넉넉히 받을지** 정할 때 쓴다.
    꺼져 있는데 3배로 받아 오면 DB 만 더 읽고 버리는 셈이 된다."""
    return _is_enabled()


__all__ = ["maybe_rerank", "rerank_enabled"]
