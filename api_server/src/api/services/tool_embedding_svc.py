"""Wave-7 P1 — wave-5 도구 (mcp_uploads) description embedding sync + search.

mcp_uploads.description / llm_hints 를 임베딩해 ``recommend_agents(q)`` 응답에
``relevant_tools`` 를 동봉. sample_embedding_svc 와 같은 패턴.

핵심 함수:
    - :func:`build_description_text` — manifest → 합성 텍스트
    - :func:`sync_tool_embedding`    — mcp_uploads 한 행의 description_embedding 갱신
    - :func:`search_tools`           — 자연어 query → top-k tools (cosine)
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import Float, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import MCPUpload

from .sql_compat import is_postgres

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# manifest → 합성 description_text
# ---------------------------------------------------------------------------
def build_description_text(manifest: dict[str, Any]) -> str:
    """매니페스트에서 임베딩 원본 텍스트 합성.

    포함:
        1. description (필수)
        2. title (있으면)
        3. llm_hints.when_to_use
        4. llm_hints.example_calls[*].natural_language

    예시:
        >>> build_description_text({
        ...     "name": "csv_summary",
        ...     "title": "CSV → 컬럼별 통계 요약",
        ...     "description": "CSV 통계 계산.",
        ...     "llm_hints": {
        ...         "when_to_use": "EDA 요청 시.",
        ...         "example_calls": [{"natural_language": "이 CSV 통계 보여줘"}]
        ...     }
        ... })
        'CSV → 컬럼별 통계 요약. CSV 통계 계산. 사용 시점: EDA 요청 시. 예시 질의: 이 CSV 통계 보여줘'
    """
    parts: list[str] = []
    title = (manifest.get("title") or "").strip()
    if title:
        parts.append(title.rstrip("."))
    desc = (manifest.get("description") or "").strip()
    if desc:
        parts.append(desc.rstrip("."))
    hints = manifest.get("llm_hints") or {}
    when = (hints.get("when_to_use") or "").strip() if isinstance(hints, dict) else ""
    if when:
        parts.append(f"사용 시점: {when.rstrip('.')}")
    if isinstance(hints, dict):
        ex = hints.get("example_calls") or []
        if isinstance(ex, list):
            nls = [
                (c.get("natural_language") or "").strip()
                for c in ex
                if isinstance(c, dict)
            ]
            nls = [n for n in nls if n]
            if nls:
                parts.append("예시 질의: " + " / ".join(nls[:5]))
    return ". ".join(parts) if parts else (manifest.get("name") or "")


# ---------------------------------------------------------------------------
# Sync — mcp_uploads 한 행의 description_embedding 갱신
# ---------------------------------------------------------------------------
async def sync_tool_embedding(
    session: AsyncSession,
    *,
    name: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """매니페스트로부터 description_text + description_embedding 계산 후 UPDATE.

    호출자가 commit 하지 않아도 됨 — 이 함수가 commit 한다.
    """
    from .embedding import get_embedder

    text = build_description_text(manifest)
    if not text:
        log.warning("sync_tool_embedding: 빈 description_text (name=%s)", name)
        await session.execute(
            update(MCPUpload)
            .where(MCPUpload.name == name)
            .values(description_text=None, description_embedding=None)
        )
        await session.commit()
        return {"name": name, "embedded": False, "reason": "empty"}

    embedder = get_embedder()
    vec = embedder.encode(text)
    await session.execute(
        update(MCPUpload)
        .where(MCPUpload.name == name)
        .values(
            description_text=text,
            description_embedding=(list(vec) if vec is not None else None),
        )
    )
    await session.commit()
    return {
        "name": name,
        "embedded": vec is not None,
        "text_len": len(text),
        "model": getattr(embedder, "name", "unknown"),
    }


# ---------------------------------------------------------------------------
# Search — query → top-k tools (cosine)
# ---------------------------------------------------------------------------
async def search_tools(
    session: AsyncSession,
    query: str,
    *,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """자연어 → top-k tools (cosine 유사도).

    Returns:
        ``[{name, description, score, manifest}]``. score ∈ [0,1].

    deprecated_at IS NOT NULL 행은 제외.
    """
    if not (query or "").strip():
        return []

    from .embedding import get_embedder

    embedder = get_embedder()
    qvec = embedder.encode_query(query)
    top_k = max(1, min(int(top_k), 20))

    if is_postgres(session):
        distance = MCPUpload.description_embedding.op("<=>", return_type=Float())(qvec).label(
            "distance"
        )
        stmt = (
            select(
                MCPUpload.name,
                MCPUpload.manifest,
                MCPUpload.description_text,
                distance,
            )
            .where(MCPUpload.description_embedding.is_not(None))
            .where(MCPUpload.deprecated_at.is_(None))
            .order_by(distance.asc())
            .limit(top_k)
        )
        rows = (await session.execute(stmt)).all()
        out: list[dict[str, Any]] = []
        for row in rows:
            d = float(row.distance) if row.distance is not None else 2.0
            sim = max(0.0, 1.0 - d / 2.0)
            manifest = row.manifest or {}
            out.append(
                {
                    "name": row.name,
                    "description": (manifest.get("description") or row.description_text or "")[:300],
                    "title": manifest.get("title") or "",
                    "score": round(sim, 4),
                    "compatible_agents": list(manifest.get("restrict_agents") or []) or None,
                    "manifest_url": f"/api/mcp_tools/{row.name}",
                }
            )
        return out

    # SQLite (test) — numpy 폴백
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        return []

    rows = (
        await session.execute(
            select(
                MCPUpload.name,
                MCPUpload.manifest,
                MCPUpload.description_text,
                MCPUpload.description_embedding,
            )
            .where(MCPUpload.description_embedding.is_not(None))
            .where(MCPUpload.deprecated_at.is_(None))
        )
    ).all()
    if not rows:
        return []
    q = np.asarray(qvec, dtype=float)
    qn = float(np.linalg.norm(q))
    if qn == 0.0:
        return []
    scored: list[tuple[float, Any]] = []
    for r in rows:
        v = np.asarray(r.description_embedding, dtype=float)
        vn = float(np.linalg.norm(v))
        if vn == 0.0:
            continue
        sim = float(np.dot(q, v) / (qn * vn))
        sim01 = max(0.0, min(1.0, (sim + 1.0) / 2.0))
        scored.append((sim01, r))
    scored.sort(key=lambda t: t[0], reverse=True)
    out2: list[dict[str, Any]] = []
    for s, r in scored[:top_k]:
        manifest = r.manifest or {}
        out2.append(
            {
                "name": r.name,
                "description": (manifest.get("description") or r.description_text or "")[:300],
                "title": manifest.get("title") or "",
                "score": round(s, 4),
                "compatible_agents": list(manifest.get("restrict_agents") or []) or None,
                "manifest_url": f"/api/mcp_tools/{r.name}",
            }
        )
    return out2
