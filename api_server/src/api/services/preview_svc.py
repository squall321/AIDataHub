"""Agent RAG-recipe preview — 저장 전 'prompt+threshold' 미리보기.

운영자가 admin UI 에서 system_prompt / score_threshold / top_k 를 바꾸기 전에
실제 검색 결과 + (가능하면) LLM 답변까지 미리 확인할 수 있게 한다.

흐름:
    1. ``query`` 를 ``search_svc.semantic_search`` 로 검색 (recipe.top_k).
    2. ``score_threshold`` 미만은 LLM 컨텍스트에서 제외.
    3. 임계치 통과 hit 가 0 이면 → refused=True, refusal_message 반환.
    4. ``OPENAI_API_KEY`` 가 설정되어 있으면 LLM 호출, 아니면
       ``llm_used=False`` + 검색 결과만 반환 (UI 가 충분히 가치 있음).

LLM 호출은 ``discover_svc._interpret_with_llm`` 과 동일한 ``AsyncOpenAI``
클라이언트를 쓴다 (OPENAI_BASE_URL 지원 → Ollama/vLLM/Qwen 호환).
"""
from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.services import agent_svc, search_svc

log = logging.getLogger(__name__)

_DEFAULT_REFUSAL = "해당 자료를 찾지 못했습니다."
_DEFAULT_MAX_TOKENS = 400
_DEFAULT_TOP_K = 5
_LLM_TIMEOUT_S = 30


async def preview_recipe(
    session: AsyncSession,
    *,
    query: str,
    agent_type: str | None,
    retrieval_config: dict[str, Any],
    system_prompt: str | None,
    response_config: dict[str, Any],
) -> dict[str, Any]:
    """RAG 레시피를 임시 입력으로 받아 검색 + (선택) LLM 답변을 반환."""
    query = (query or "").strip()
    if not query:
        return {
            "query": "",
            "hits": [],
            "hits_above_threshold": 0,
            "threshold": None,
            "refused": True,
            "refusal_message": "query 가 비어 있습니다.",
            "answer": None,
            "llm_used": False,
            "llm_note": None,
        }

    top_k = _coerce_int(retrieval_config.get("top_k"), _DEFAULT_TOP_K, 1, 50)
    threshold = _coerce_float(
        retrieval_config.get("score_threshold"), default=None, lo=0.0, hi=1.0
    )
    data_type_filter = retrieval_config.get("data_type_filter") or None
    if isinstance(data_type_filter, list) and not data_type_filter:
        data_type_filter = None

    # agent_type 이 주어지면 해당 agent 의 records 로 검색 범위를 제한.
    record_ids: list[str] | None = None
    if agent_type:
        records = await agent_svc.records_for_agent(session, agent_type)
        record_ids = [r.id for r in records] if records else []
        if not record_ids:
            # agent 가 가진 record 가 0 건이면 검색해도 hit 없음 — 단축.
            return {
                "query": query,
                "hits": [],
                "hits_above_threshold": 0,
                "threshold": threshold,
                "refused": True,
                "refusal_message": _refusal_msg(response_config),
                "answer": None,
                "llm_used": False,
                "llm_note": f"agent '{agent_type}' has 0 mapped records",
            }

    raw_hits = await search_svc.semantic_search(
        session,
        query,
        top_k=top_k,
        data_types=data_type_filter,
        record_ids=record_ids,
    )

    hits = [
        {
            "record_id": h["record_id"],
            "section_id": h["section_id"],
            "section_title": h.get("section_title") or "",
            "snippet": h.get("snippet") or "",
            "score": float(h.get("score", 0.0)),
        }
        for h in raw_hits
    ]

    if threshold is not None:
        above = [h for h in hits if h["score"] >= threshold]
    else:
        above = list(hits)
    hits_above = len(above)

    if hits_above == 0:
        return {
            "query": query,
            "hits": hits,
            "hits_above_threshold": 0,
            "threshold": threshold,
            "refused": True,
            "refusal_message": _refusal_msg(response_config),
            "answer": None,
            "llm_used": False,
            "llm_note": (
                "no hits above score_threshold"
                if threshold is not None
                else "no semantic-search hits"
            ),
        }

    # LLM 호출 — 가능하면.
    answer, llm_used, llm_note = await _maybe_call_llm(
        query=query,
        system_prompt=system_prompt,
        hits=above,
        response_config=response_config,
    )

    return {
        "query": query,
        "hits": hits,
        "hits_above_threshold": hits_above,
        "threshold": threshold,
        "refused": False,
        "refusal_message": None,
        "answer": answer,
        "llm_used": llm_used,
        "llm_note": llm_note,
    }


# ---------------------------------------------------------------------------
# LLM call (OpenAI-compatible, optional)
# ---------------------------------------------------------------------------
async def _maybe_call_llm(
    *,
    query: str,
    system_prompt: str | None,
    hits: list[dict[str, Any]],
    response_config: dict[str, Any],
) -> tuple[str | None, bool, str | None]:
    """``OPENAI_API_KEY`` 가 있으면 LLM 호출. 없거나 실패 시 (None, False, note)."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return (None, False, "OPENAI_API_KEY not configured — showing retrieved chunks only")

    try:
        from openai import AsyncOpenAI  # type: ignore
    except ImportError:
        return (None, False, "openai package not installed — showing retrieved chunks only")

    max_tokens = _coerce_int(
        response_config.get("max_tokens"), _DEFAULT_MAX_TOKENS, 50, 4000
    )
    sys_prompt = (system_prompt or "").strip() or _generic_system_prompt(response_config)
    user_msg = _build_user_message(query=query, hits=hits, response_config=response_config)

    base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or None
    model = os.environ.get("OPENAI_ASK_MODEL", "gpt-4o-mini")

    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=max_tokens,
            temperature=0.2,
            timeout=_LLM_TIMEOUT_S,
        )
        text = (resp.choices[0].message.content or "").strip()
        return (text, True, f"model={model}")
    except Exception as exc:  # pragma: no cover — depends on external service
        log.warning("preview LLM call failed: %s", exc)
        return (None, False, _classify_llm_error(exc, model=model))


def _classify_llm_error(exc: Exception, *, model: str) -> str:
    """openai SDK 예외 → 운영자 친화 메시지. 정확한 클래스 이름이 SDK 버전마다
    달라서 클래스명 + 메시지 substring 으로 분류한다."""
    cls = exc.__class__.__name__
    msg = str(exc).lower()
    if cls in ("AuthenticationError",) or "invalid_api_key" in msg or "incorrect api key" in msg:
        return "LLM 인증 실패 — OPENAI_API_KEY 가 잘못되었습니다 (서버 환경변수 확인)."
    if cls in ("RateLimitError",) or "rate limit" in msg or "quota" in msg:
        return "LLM 할당량 초과 — OpenAI 플랜/Ollama 큐 상태를 확인하세요."
    if cls in ("APITimeoutError",) or "timeout" in msg or "timed out" in msg:
        return f"LLM 응답 타임아웃 ({_LLM_TIMEOUT_S}s 초과) — 모델 '{model}' 가 너무 느립니다."
    if cls in ("APIConnectionError", "ConnectionError") or "connection" in msg or "connect" in msg:
        return "LLM 서버 연결 실패 — OPENAI_BASE_URL 가 가리키는 호스트가 살아 있는지 확인하세요."
    if cls in ("NotFoundError",) or "model" in msg and "not" in msg and "found" in msg:
        return f"LLM 모델 '{model}' 을 찾을 수 없음 — OPENAI_ASK_MODEL 환경변수를 확인하세요."
    if cls in ("BadRequestError",) or "context_length" in msg or "maximum context" in msg:
        return "컨텍스트 길이 초과 — top_k 또는 max_tokens 를 줄이세요."
    # fallback — 원본 메시지 첫 120자.
    head = str(exc)[:120].replace("\n", " ")
    return f"LLM 호출 실패 ({cls}): {head}"


def _generic_system_prompt(response_config: dict[str, Any]) -> str:
    cite = bool(response_config.get("citation_required"))
    refusal = response_config.get("refusal_message") or _DEFAULT_REFUSAL
    parts = [
        "당신은 사내 데이터 허브 기반 어시스턴트입니다.",
        "제공된 컨텍스트만 사용해 한국어로 간결히 답하세요.",
    ]
    if cite:
        parts.append("답변에 반드시 출처를 `record_id §section_id` 형식으로 인용하세요.")
    parts.append(f"컨텍스트에서 답을 찾지 못하면 정확히 '{refusal}' 라고만 답하세요.")
    return " ".join(parts)


def _build_user_message(
    *,
    query: str,
    hits: list[dict[str, Any]],
    response_config: dict[str, Any],
) -> str:
    ctx_lines = []
    for h in hits:
        ctx_lines.append(
            f"[{h['record_id']} §{h['section_id']}] (score={h['score']:.3f}) "
            f"{h.get('section_title') or ''}\n{h.get('snippet') or ''}"
        )
    ctx = "\n\n".join(ctx_lines) if ctx_lines else "(no context)"
    return f"질문: {query}\n\n--- 컨텍스트 ---\n{ctx}"


def _refusal_msg(response_config: dict[str, Any]) -> str:
    msg = response_config.get("refusal_message")
    if isinstance(msg, str) and msg.strip():
        return msg
    return _DEFAULT_REFUSAL


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------
def _coerce_int(v: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(v) if v is not None else default
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _coerce_float(v: Any, *, default: float | None, lo: float, hi: float) -> float | None:
    if v is None or v == "":
        return default
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))
