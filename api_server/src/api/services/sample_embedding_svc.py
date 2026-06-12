"""Agent sample-queries embedding sync + cosine search.

agent.sample_queries → ``agent_sample_embeddings`` 테이블 (Migration 0016) 의
임베딩 행들. recommend_svc 가 record-section 검색과 함께 이 테이블을 코사인
검색해 라우팅 점수에 합산한다.

핵심 함수:
    - :func:`sync_agent_samples`  — agent_type 의 sample 행들을 전체 교체
    - :func:`search_samples`      — 자연어 query → top-k (agent_type, score)
    - :func:`count_agent_samples` — UI 가 인덱싱 상태를 표시할 때 사용
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import Float, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import AgentSampleEmbedding

from .sql_compat import is_postgres

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sync — replace all sample-embedding rows for one agent (idempotent)
# ---------------------------------------------------------------------------
async def sync_agent_samples(
    session: AsyncSession,
    *,
    agent_type: str,
    sample_queries: Sequence[str],
) -> dict:
    """``agent_sample_embeddings`` 의 agent_type 행들을 sample_queries 로 전체 교체.

    빈 리스트가 들어오면 모든 행이 삭제만 된다 (사용자가 sample 을 비웠다는 의도).

    호출자가 ``session.commit`` 을 별도 호출할 필요는 없다 — 이 함수가 commit 한다.
    """
    from .embedding import get_embedder

    # 1) 기존 행 삭제
    await session.execute(
        delete(AgentSampleEmbedding).where(AgentSampleEmbedding.agent_type == agent_type)
    )

    sample_queries = [s for s in (sample_queries or []) if s and s.strip()]
    if not sample_queries:
        await session.commit()
        return {"agent_type": agent_type, "count": 0}

    # 2) 임베딩 계산 — 가능하면 batch.
    embedder = get_embedder()
    encode_many = getattr(embedder, "encode_many", None)
    if callable(encode_many):
        vectors = encode_many(sample_queries)
    else:
        vectors = [embedder.encode(q) for q in sample_queries]

    # 3) insert
    for text, vec in zip(sample_queries, vectors):
        session.add(
            AgentSampleEmbedding(
                agent_type=agent_type,
                sample_text=text,
                embedding=list(vec) if vec is not None else None,
            )
        )

    await session.commit()
    return {"agent_type": agent_type, "count": len(sample_queries)}


async def count_agent_samples(
    session: AsyncSession, agent_type: str
) -> int:
    """현재 인덱싱된 sample 행 수 (UI 의 'X queries indexed' 표시용)."""
    stmt = select(func.count(AgentSampleEmbedding.id)).where(
        AgentSampleEmbedding.agent_type == agent_type
    )
    return int((await session.execute(stmt)).scalar_one() or 0)


# ---------------------------------------------------------------------------
# Search — query → ranked (agent_type, score) by cosine
# ---------------------------------------------------------------------------
async def search_samples(
    session: AsyncSession,
    query: str,
    *,
    top_k: int = 20,
) -> list[dict]:
    """자연어 → top-k ``agent_sample_embeddings`` rows (코사인 유사도).

    응답: ``[{agent_type, sample_text, score}]``. score ∈ [0,1].

    PG: pgvector ``<=>`` 거리. SQLite (test): numpy 폴백.
    """
    if not (query or "").strip():
        return []

    from .embedding import get_embedder

    embedder = get_embedder()
    qvec = embedder.encode_query(query)
    top_k = max(1, min(int(top_k), 100))

    if is_postgres(session):
        distance = AgentSampleEmbedding.embedding.op("<=>", return_type=Float())(qvec).label(
            "distance"
        )
        stmt = (
            select(
                AgentSampleEmbedding.agent_type.label("agent_type"),
                AgentSampleEmbedding.sample_text.label("sample_text"),
                distance,
            )
            .where(AgentSampleEmbedding.embedding.is_not(None))
            .order_by(distance.asc())
            .limit(top_k)
        )
        rows = (await session.execute(stmt)).all()
        out: list[dict] = []
        for row in rows:
            d = float(row.distance) if row.distance is not None else 2.0
            sim = max(0.0, 1.0 - d / 2.0)
            out.append(
                {
                    "agent_type": row.agent_type,
                    "sample_text": row.sample_text,
                    "score": round(sim, 4),
                }
            )
        return out

    # SQLite (테스트) — 모든 행을 가져와 numpy 코사인 계산
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover
        log.warning("numpy not installed — SQLite sample search returns empty")
        return []

    stmt = select(
        AgentSampleEmbedding.agent_type,
        AgentSampleEmbedding.sample_text,
        AgentSampleEmbedding.embedding,
    ).where(AgentSampleEmbedding.embedding.is_not(None))
    rows = (await session.execute(stmt)).all()
    if not rows:
        return []
    q = np.asarray(qvec, dtype=float)
    q_norm = float(np.linalg.norm(q))
    if q_norm == 0.0:
        return []
    scored: list[tuple[float, str, str]] = []
    for r in rows:
        v = np.asarray(r.embedding, dtype=float)
        v_norm = float(np.linalg.norm(v))
        if v_norm == 0.0:
            continue
        sim = float(np.dot(q, v) / (q_norm * v_norm))
        sim = max(0.0, min(1.0, (sim + 1.0) / 2.0))  # [-1,1] → [0,1]
        scored.append((sim, r.agent_type, r.sample_text))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [
        {"agent_type": a, "sample_text": s, "score": round(sc, 4)}
        for sc, a, s in scored[:top_k]
    ]
