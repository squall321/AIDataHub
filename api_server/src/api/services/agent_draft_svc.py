"""LLM 보조 agent 초안 생성 서비스.

설계 의도:
    기존 허브에 이미 적재된 레코드(tags / doc_type / data_type / 본문 일부)를
    신호로 삼아, "이 데이터군을 담당할 agent" 의 정의 초안을 제안한다.
    사람은 extension UI 에서 검토·수정 후 POST /api/agents 로 저장한다.

동작:
    1. 입력으로 record_ids(특정 레코드) 또는 hint(자연어 의도) 를 받는다.
       둘 다 없으면 최근 레코드 표본을 사용한다.
    2. 표본에서 tag 빈도 / doc_type / data_type 분포 / 제목·요약을 집계.
    3. ``OPENAI_API_KEY`` 가 있으면 LLM 으로 구조화 초안 생성
       (source="llm"). 없거나 실패하면 휴리스틱 폴백 (source="heuristic").
    4. 저장하지 않고 AgentIn 형태의 draft dict 를 반환한다.

LLM 호출은 preview_svc / discover_svc 와 동일한 ``AsyncOpenAI`` 패턴을 쓴다
(``OPENAI_BASE_URL`` 지원 → Ollama/vLLM/Qwen 호환).
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Record, RecordSection

log = logging.getLogger(__name__)

_LLM_TIMEOUT_S = 30
_SAMPLE_RECORD_CAP = 30
_SAMPLE_SECTION_CAP = 4


# ---------------------------------------------------------------------------
# 1) 신호 수집
# ---------------------------------------------------------------------------
async def _gather_signal(
    session: AsyncSession,
    *,
    record_ids: list[str] | None,
) -> dict[str, Any]:
    """표본 레코드에서 tag/doc_type/data_type 분포 + 제목·요약을 집계."""
    stmt = select(Record).where(Record.deleted_at.is_(None))
    if record_ids:
        stmt = stmt.where(Record.id.in_(record_ids))
    stmt = stmt.order_by(Record.created_at.desc()).limit(_SAMPLE_RECORD_CAP)
    recs = (await session.execute(stmt)).scalars().all()

    tag_c: Counter[str] = Counter()
    doc_type_c: Counter[str] = Counter()
    data_type_c: Counter[str] = Counter()
    titles: list[str] = []
    summaries: list[str] = []
    for r in recs:
        for t in r.tags or []:
            tag_c[t] += 1
        if r.doc_type:
            doc_type_c[r.doc_type] += 1
        if r.data_type:
            data_type_c[r.data_type] += 1
        if r.title:
            titles.append(r.title)
        if r.summary:
            summaries.append(r.summary[:200])

    # 대표 섹션 제목 (질의 예시 추론 신호)
    section_titles: list[str] = []
    if recs:
        ids = [r.id for r in recs[:8]]
        srows = (
            await session.execute(
                select(RecordSection.title)
                .where(RecordSection.record_id.in_(ids))
                .where(RecordSection.title.is_not(None))
                .limit(40)
            )
        ).scalars().all()
        section_titles = [s for s in srows if s][:_SAMPLE_SECTION_CAP * 8]

    return {
        "record_count": len(recs),
        "top_tags": [t for t, _ in tag_c.most_common(15)],
        "doc_types": [d for d, _ in doc_type_c.most_common(3)],
        "data_types": [d for d, _ in data_type_c.most_common(5)],
        "sample_titles": titles[:15],
        "sample_summaries": summaries[:8],
        "sample_section_titles": section_titles,
    }


# ---------------------------------------------------------------------------
# 2) 휴리스틱 폴백 초안
# ---------------------------------------------------------------------------
def _slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9가-힣]+", "-", (text or "").strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "agent"


def _clean_title(text: str) -> str:
    """섹션 제목에서 마크다운 링크/번호/특수문자 제거 → 자연스러운 질의어."""
    s = text or ""
    # [라벨](#anchor) → 라벨
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    # 선행 번호 (1. / 2.5.1 / 1) 제거
    s = re.sub(r"^\s*[\d.]+\)?\s*", "", s)
    # em-dash 구분자 정리
    s = s.replace("—", " ").replace("#", " ")
    return re.sub(r"\s+", " ", s).strip()


def _heuristic_draft(signal: dict[str, Any], hint: str | None) -> dict[str, Any]:
    top_tags = signal["top_tags"]
    data_types = signal["data_types"] or ["DOC"]
    doc_types = signal["doc_types"]
    base_name = hint.strip() if hint else (top_tags[0] if top_tags else "데이터")
    agent_type = _slugify(base_name) + "-assistant"
    name = f"{base_name} 어시스턴트"
    sample_qs = []
    for t in (signal["sample_section_titles"] or signal["sample_titles"])[:5]:
        clean = _clean_title(t)
        if clean:
            sample_qs.append(f"{clean} 에 대해 알려줘")
    system_prompt = (
        f"당신은 {base_name} 전문 보조입니다. 2~3문장 이내로 답하고, "
        f"출처는 record_id §섹션 형식으로 인용하세요. "
        f"자료에 없으면 「해당 자료를 찾지 못했습니다」라고만 답합니다."
    )
    return {
        "agent_type": agent_type,
        "name": name,
        "description": (
            f"{base_name} 관련 문서를 검색·요약하는 보조 에이전트 "
            f"(자동 초안, 검토 필요)."
        ),
        "common_tags": top_tags[:8],
        "data_types": data_types,
        "required_doc_type": doc_types[0] if doc_types else None,
        "required_tags": [],
        "excluded_tags": [],
        "retrieval_config": {"top_k": 5, "score_threshold": 0.3},
        "system_prompt": system_prompt,
        "response_config": {
            "max_tokens": 300,
            "citation_required": True,
            "refusal_message": "해당 자료를 찾지 못했습니다.",
        },
        "sample_queries": sample_qs,
        "_source": "heuristic",
        "_note": "OPENAI_API_KEY 미설정 — 빈도 기반 휴리스틱 초안. 검토 후 저장하세요.",
    }


# ---------------------------------------------------------------------------
# 3) LLM 초안
# ---------------------------------------------------------------------------
_DRAFT_SYS = """\
You design RAG agent definitions for a document data hub. Given signal about \
a set of existing records (their tags, doc types, titles, summaries), output \
ONE JSON object describing an agent that would own and answer questions about \
this kind of data. Output ONLY valid minified JSON, no markdown, with keys:
agent_type (kebab-case ascii slug), name, description, common_tags (array),
data_types (subset of DOC/DATA/SIM/CAD/LOG/FORM/OTHER), required_doc_type
(string or null), required_tags (array, usually empty), excluded_tags (array,
usually empty), retrieval_config (object: top_k int, score_threshold float
0..1), system_prompt (a concise persona instruction in the same language as
the data; must tell the model to cite record_id §section and to refuse when
no data), response_config (object: max_tokens int, citation_required bool,
refusal_message string), sample_queries (array of 3-6 realistic user
questions in the data's language)."""


async def _llm_draft(
    signal: dict[str, Any], hint: str | None
) -> dict[str, Any] | None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from openai import AsyncOpenAI  # type: ignore
    except ImportError:
        return None

    base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or None
    model = os.environ.get("OPENAI_ASK_MODEL", "gpt-4o-mini")
    user_msg = json.dumps(
        {"intent_hint": hint or "", "signal": signal}, ensure_ascii=False
    )
    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _DRAFT_SYS},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=900,
            temperature=0.3,
            timeout=_LLM_TIMEOUT_S,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # ```json ... ``` 같은 펜스 제거
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        draft = json.loads(raw)
        if not isinstance(draft, dict) or not draft.get("agent_type"):
            return None
        draft["_source"] = "llm"
        draft["_note"] = f"LLM 초안 (model={model}). 검토·수정 후 저장하세요."
        return draft
    except Exception as exc:  # pragma: no cover — 외부 서비스 의존
        log.warning("agent draft LLM failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------
async def generate_draft(
    session: AsyncSession,
    *,
    record_ids: list[str] | None = None,
    hint: str | None = None,
) -> dict[str, Any]:
    """기존 레코드 신호 + (선택) 자연어 의도 → agent 정의 초안.

    LLM 가능 시 LLM 초안, 아니면 휴리스틱 폴백. 저장은 하지 않는다.
    """
    signal = await _gather_signal(session, record_ids=record_ids)
    if signal["record_count"] == 0 and not hint:
        # 신호도 의도도 없으면 빈 골격만.
        return {
            **_heuristic_draft(
                {
                    "top_tags": [],
                    "data_types": ["DOC"],
                    "doc_types": [],
                    "sample_section_titles": [],
                    "sample_titles": [],
                },
                hint,
            ),
            "_note": "표본 레코드 0건 — 빈 골격. hint 를 주거나 데이터를 먼저 적재하세요.",
            "_signal": signal,
        }
    llm = await _llm_draft(signal, hint)
    draft = llm if llm is not None else _heuristic_draft(signal, hint)
    draft["_signal"] = signal
    return draft


# ---------------------------------------------------------------------------
# 4) 저장 후 매칭 레코드 자동 바인딩 (루프 닫기)
# ---------------------------------------------------------------------------
async def bind_matching_records(
    session: AsyncSession,
    *,
    agent_type: str,
    required_doc_type: str | None,
    required_tags: list[str],
    common_tags: list[str],
    data_types: list[str] | None,
    limit: int = 500,
) -> dict[str, Any]:
    """agent 기대 스키마에 부합하는 기존 레코드의 ``agents`` 배열에
    이 agent_type 을 추가한다 (이미 포함된 건 건너뜀).

    매칭 규칙 (AND):
        - required_doc_type 지정 시 doc_type 일치
        - data_types 지정 시 data_type 포함
        - required_tags 모두 포함 (있으면)
        - required_tags 없으면 common_tags 중 1개 이상 겹침
    """
    stmt = select(Record).where(Record.deleted_at.is_(None))
    if required_doc_type:
        stmt = stmt.where(Record.doc_type == required_doc_type)
    if data_types:
        stmt = stmt.where(Record.data_type.in_(list(data_types)))
    stmt = stmt.limit(limit)
    recs = (await session.execute(stmt)).scalars().all()

    req = set(required_tags or [])
    common = set(common_tags or [])
    bound: list[str] = []
    for r in recs:
        rtags = set(r.tags or [])
        if req:
            if not req.issubset(rtags):
                continue
        elif common:
            if not common.intersection(rtags):
                continue
        cur = list(r.agents or [])
        if agent_type in cur:
            continue
        r.agents = cur + [agent_type]
        bound.append(r.id)

    if bound:
        await session.commit()

    return {
        "agent_type": agent_type,
        "scanned": len(recs),
        "bound_count": len(bound),
        "bound_record_ids": bound[:50],
    }


__all__ = ["generate_draft", "bind_matching_records"]
