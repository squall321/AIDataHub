"""포맷 유사 부모(campaign) 추천 서비스.

하이브리드 데이터 모델: campaign(부모) record 1건 + 개별 specimen(자식)
record N건이 ``parent_record_id`` 로 연결된다. 새 specimen 을 적재했을 때
"같은 포맷이었던 부모"를 찾아 사람이 확인 후 연결하도록 후보를 제안한다.

LLM 불필요 — 결정론적 휴리스틱:
    - doc_type 동일        (같은 템플릿 — 가장 강한 신호)
    - team/group 동일      (같은 시험 범위)
    - data_type 동일
    - 섹션 제목 구조 겹침   (같은 문서 골격 = Jaccard)
    - 태그 겹침
제외: 자기 자신, 그리고 자기 자신을 부모로 가리키면 순환.
가점: 이미 자식을 가진 record(진짜 campaign), 더 오래된 record.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Record, RecordSection

log = logging.getLogger(__name__)

# 가중치 (합 = 1.0 기준, 가점은 별도 부스트)
_W_DOC_TYPE = 0.35
_W_TEAM_GROUP = 0.20
_W_DATA_TYPE = 0.10
_W_SECTION = 0.25
_W_TAGS = 0.10


async def _section_titles(session: AsyncSession, record_id: str) -> set[str]:
    rows = (
        await session.execute(
            select(RecordSection.title)
            .where(RecordSection.record_id == record_id)
            .where(RecordSection.title.is_not(None))
        )
    ).scalars().all()
    return {str(t).strip().lower() for t in rows if t and str(t).strip()}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


async def suggest_parents(
    session: AsyncSession,
    *,
    record_id: str,
    top_k: int = 5,
) -> dict[str, Any]:
    """record_id 와 포맷이 유사한 부모 후보를 점수순으로 반환.

    Returns:
        {"record_id", "candidates": [{record_id, score, confidence,
         why, doc_type, title, child_count}], "note"}
    """
    me = (
        await session.execute(select(Record).where(Record.id == record_id))
    ).scalar_one_or_none()
    if me is None:
        raise ValueError(f"record not found: {record_id}")

    my_sections = await _section_titles(session, record_id)
    my_tags = set(me.tags or [])

    # 후보 풀 — 자기 자신 제외, soft-delete 제외.
    # 같은 team 우선이지만 너무 좁히면 후보가 없으므로 team 까지만 1차 필터.
    stmt = (
        select(Record)
        .where(Record.deleted_at.is_(None))
        .where(Record.id != record_id)
        .where(Record.team == me.team)
        .limit(400)
    )
    cands = (await session.execute(stmt)).scalars().all()

    # 자식 보유 카운트 (진짜 campaign 가점) — 한 번에 집계.
    child_counts: dict[str, int] = {}
    if cands:
        cc_rows = (
            await session.execute(
                select(Record.parent_record_id, func.count())
                .where(Record.parent_record_id.is_not(None))
                .group_by(Record.parent_record_id)
            )
        ).all()
        child_counts = {pid: int(n) for pid, n in cc_rows if pid}

    scored: list[dict[str, Any]] = []
    for c in cands:
        # 순환 방지: 후보가 나를 부모로 가리키면 제외.
        if c.parent_record_id == record_id:
            continue
        score = 0.0
        reasons: list[str] = []

        if me.doc_type and c.doc_type and me.doc_type == c.doc_type:
            score += _W_DOC_TYPE
            reasons.append(f"doc_type={c.doc_type}")
        if c.group == me.group:
            score += _W_TEAM_GROUP
            reasons.append(f"{c.team}/{c.group}")
        if c.data_type == me.data_type:
            score += _W_DATA_TYPE
            reasons.append(f"data_type={c.data_type}")

        c_sections = await _section_titles(session, c.id)
        sec_sim = _jaccard(my_sections, c_sections)
        if sec_sim > 0:
            score += _W_SECTION * sec_sim
            reasons.append(f"섹션구조 {sec_sim:.0%}")

        tag_sim = _jaccard(my_tags, set(c.tags or []))
        if tag_sim > 0:
            score += _W_TAGS * tag_sim
            reasons.append(f"태그 {tag_sim:.0%}")

        # 가점: 이미 자식 보유(진짜 campaign).
        n_child = child_counts.get(c.id, 0)
        if n_child > 0:
            score += 0.15
            reasons.append(f"기존 자식 {n_child}건")

        if score <= 0:
            continue

        score = round(min(score, 1.0), 4)
        conf = "high" if score >= 0.6 else ("medium" if score >= 0.35 else "low")
        scored.append(
            {
                "record_id": c.id,
                "title": c.title,
                "doc_type": c.doc_type,
                "data_type": c.data_type,
                "score": score,
                "confidence": conf,
                "child_count": n_child,
                "why": " · ".join(reasons),
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:top_k]
    note = (
        "사람이 확인 후 PATCH /api/records/{id} 의 parent_record_id 로 연결하세요."
        if top
        else "포맷이 유사한 부모 후보가 없습니다. 이 record 가 campaign(부모)일 수 있습니다."
    )
    return {"record_id": record_id, "candidates": top, "note": note}


__all__ = ["suggest_parents"]
