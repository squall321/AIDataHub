"""임베딩 유사도 기반 제안형 자동분류 (Phase 5 — 룰 없는 방식).

사용자 우려 반영: 데이터 종류가 수백 개여도 **새 타입마다 룰을 추가하지 않는다.**
새 표/문서가 들어오면 "기존 데이터 중 가장 비슷한 것"을 벡터 유사도로 찾아,
그 이웃들의 team/group/graph_type 을 **제안**한다 (확정 X — 적대검증 B3:
잘못된 자동분류가 엉뚱한 곳에 저장하는 것을 막기 위해 사람이 확인).

설계:
    - 시그니처 = 제목/caption + 컬럼명(헤더) → 짧은 문자열을 e5-base 로 임베딩.
    - 같은 data_type 의 기존 레코드 시그니처와 cosine 비교 → top-k 이웃.
    - 이웃들의 메타(team/group/graph_type) 다수결 → 제안값 + confidence.
    - confidence 가 낮으면 Claude 가 반드시 사용자에게 되묻는다.

규모: 현재는 같은 data_type 레코드를 on-the-fly 임베딩 (코퍼스 소규모).
데이터가 수만 건+ 로 커지면 records 에 signature_embedding 컬럼 + pgvector ANN
으로 교체 (이 함수 인터페이스는 그대로 — 내부만 교체). max_candidates 초과 시
잘린 사실을 note 로 보고 (silent cap 금지).
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# e5-base 같은 의미 페어 ~0.86~0.94, 무관 페어 ~0.7~0.85 (recommended 0.92).
_HIGH = 0.93
_MED = 0.88


def _signature(*, title: str = "", caption: str = "", headers: list | None = None, notes: str = "") -> str:
    """레코드/입력의 짧은 식별 시그니처. 임베딩 입력용."""
    parts: list[str] = []
    head = (title or caption or "").strip()
    if head:
        parts.append(head)
    if headers:
        cols = ", ".join(str(h) for h in headers[:20] if h)
        if cols:
            parts.append(f"columns: {cols}")
    if notes:
        parts.append(str(notes)[:200])
    return " | ".join(parts) or "(empty)"


def _record_signature(rec: Any) -> str:
    c = rec.content if isinstance(rec.content, dict) else {}
    return _signature(
        title=rec.title or "",
        caption=str(c.get("caption") or ""),
        headers=c.get("headers") if isinstance(c.get("headers"), list) else None,
        notes=str(c.get("notes") or ""),
    )


def _confidence(score: float) -> str:
    if score >= _HIGH:
        return "high"
    if score >= _MED:
        return "medium"
    return "low"


async def suggest_by_similarity(
    session: AsyncSession,
    *,
    title: str = "",
    caption: str = "",
    headers: list | None = None,
    notes: str = "",
    data_type: str = "DATA",
    top_k: int = 5,
    max_candidates: int = 300,
) -> dict[str, Any]:
    """입력 데이터와 비슷한 기존 레코드 → team/group/graph_type 제안.

    반환:
        {neighbors:[{id,title,team,group,graph_type,score}],
         suggested:{team?,group?,graph_type?,tags?},
         confidence:'high|medium|low|none', note}
    """
    import numpy as np

    from ..db.models import Record
    from .embedding import get_embedder

    dt = (data_type or "DATA").upper()
    qsig = _signature(title=title, caption=caption, headers=headers, notes=notes)

    emb = await asyncio.to_thread(get_embedder)
    qvec = np.asarray(await asyncio.to_thread(emb.encode_query, qsig), dtype="float32")

    # 비교 대상: 같은 data_type, soft-delete 제외, 최근 우선
    rows = (
        await session.execute(
            select(Record)
            .where(Record.data_type == dt, Record.deleted_at.is_(None))
            .order_by(Record.created_at.desc())
            .limit(max_candidates + 1)
        )
    ).scalars().all()
    truncated = len(rows) > max_candidates
    rows = rows[:max_candidates]

    if not rows:
        return {"neighbors": [], "suggested": {}, "confidence": "none",
                "note": f"비교할 기존 {dt} 레코드가 없습니다 — 첫 데이터라 제안 불가, 직접 지정하세요."}

    sigs = [_record_signature(r) for r in rows]
    mat = np.asarray(await asyncio.to_thread(emb.encode_many, sigs), dtype="float32")
    scores = mat @ qvec  # 정규화 벡터라 dot = cosine

    order = np.argsort(scores)[::-1][:top_k]
    neighbors: list[dict[str, Any]] = []
    for i in order:
        r = rows[int(i)]
        c = r.content if isinstance(r.content, dict) else {}
        neighbors.append({
            "id": r.id, "title": r.title, "team": r.team, "group": r.group,
            "doc_type": r.doc_type, "tags": list(r.tags or []),
            "graph_type": c.get("graph_type"), "score": round(float(scores[int(i)]), 3),
        })

    # 다수결 — medium 이상 이웃만 (낮으면 1등만 참고). 확정 아닌 제안.
    strong = [n for n in neighbors if n["score"] >= _MED] or neighbors[:1]
    top_score = neighbors[0]["score"] if neighbors else 0.0

    def _majority(key: str) -> Any:
        vals = [n[key] for n in strong if n.get(key)]
        if not vals:
            return None
        return max(set(vals), key=vals.count)

    suggested: dict[str, Any] = {}
    # tags / doc_type / graph_type — 일관 추론 가능한 분류 메타. AI 가 채워 제안.
    for k in ("doc_type", "graph_type"):
        v = _majority(k)
        if v is not None:
            suggested[k] = {"value": v, "source": f"유사 레코드 {strong[0]['id']} 등 {len(strong)}건",
                            "score": top_score}
    # tags 는 strong 이웃들의 합집합 (등장 2회 이상 우선) — 일관 태깅 유도.
    tag_freq: dict[str, int] = {}
    for n in strong:
        for t in n.get("tags") or []:
            tag_freq[t] = tag_freq.get(t, 0) + 1
    common_tags = [t for t, f in sorted(tag_freq.items(), key=lambda x: -x[1]) if f >= 1][:8]
    if common_tags:
        suggested["tags"] = {"value": common_tags,
                             "source": f"유사 레코드 {len(strong)}건의 태그",
                             "score": top_score}

    # team / group — 자동 확정 금지. 추론값은 '후보'로만 주고 사람이 확인하게 유도.
    team_candidates = sorted({n["team"] for n in strong if n.get("team")})
    group_candidates = sorted({n["group"] for n in strong if n.get("group")})
    needs_human: dict[str, Any] = {}
    if team_candidates:
        needs_human["team"] = {"candidates": team_candidates,
                               "most_similar": neighbors[0]["team"] if neighbors else None,
                               "ask": "team 을 자동으로 정하지 말고 사용자에게 확인하세요."}
    if group_candidates:
        needs_human["group"] = {"candidates": group_candidates,
                                "most_similar": neighbors[0]["group"] if neighbors else None,
                                "ask": "group 을 자동으로 정하지 말고 사용자에게 확인하세요."}

    note = ("doc_type/tags/graph_type 은 유사 데이터 기준 제안이니 검토 후 채우세요. "
            "team/group 은 자동 확정하지 말고 needs_human 의 후보를 사용자에게 보여주고 "
            "어디에 속하는지 물어보세요 (유사도가 높아도 다른 팀 데이터일 수 있음).")
    if top_score < _MED:
        note = ("유사도가 낮습니다 — 같은 종류 데이터가 거의 없습니다. 분류 메타를 "
                "추측하지 말고 사용자에게 team/group/doc_type 을 직접 물어보세요.")
    if truncated:
        note += f" [참고: {dt} {max_candidates}건 초과라 최근 {max_candidates}건만 비교]"

    return {"neighbors": neighbors, "suggested": suggested,
            "needs_human": needs_human,
            "confidence": _confidence(top_score), "note": note}


__all__ = ["suggest_by_similarity"]
