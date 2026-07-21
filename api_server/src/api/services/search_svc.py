"""검색 비즈니스 로직.

- ``tag_search``       : 모든 태그를 포함하는 레코드 (ARRAY @> on PG, 파이썬 후필터 on SQLite)
- ``fts_search``       : 본문/요약 텍스트 ILIKE 기반 단순 FTS
- ``semantic_search``  : pgvector ``<=>`` (cosine) 또는 numpy 폴백 시맨틱 검색
- ``data_for_agent``   : ``/api/data`` 엔드포인트의 핵심 로직

ARRAY/JSONB 등 방언 의존 표현은 모두 :mod:`api.services.sql_compat` 의
헬퍼를 경유한다. 이 모듈은 직접 ``op('@>')`` / ``func.unnest`` 등을 호출하지
않는다.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import Float, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import AgentRecord, Record, RecordSection

from .sql_compat import (
    ArrayPredicate,
    array_contains,
    array_overlap,
    fts_match,
    is_postgres,
    paginate_rows,
    summary_ilike,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backward-compat re-exports (old name → new helper).
# Routes 와 다른 서비스 모듈에서 ``from .search_svc import array_overlap`` 형태로
# 참조하던 코드가 있을 수 있으므로 호환을 유지한다.
# ---------------------------------------------------------------------------
def array_contains_all(
    column, values: Sequence[str], session: AsyncSession
) -> ArrayPredicate:
    return array_contains(column, values, session)


def array_overlap_compat(
    column, values: Sequence[str], session: AsyncSession
) -> ArrayPredicate:
    return array_overlap(column, values, session)


# ---------------------------------------------------------------------------
# Tag search
# ---------------------------------------------------------------------------
async def tag_search(
    session: AsyncSession,
    tags: Sequence[str],
    *,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Record], int]:
    """태그 모두 포함(AND) 검색.

    ``Record.tags @> [tags...]`` 의미. SQLite 에서는 파이썬 후필터로 동등한
    동작을 보장한다.
    """
    pred = array_contains(Record.tags, list(tags), session)
    stmt = (
        select(Record)
        .where(pred.where_clause)
        .where(Record.deleted_at.is_(None))
        .order_by(Record.updated_at.desc(), Record.id.desc())
    )
    pyfilters = [pred] if pred.python_filter is not None else []
    return await paginate_rows(
        session, stmt, limit=limit, offset=offset, extra_python_predicates=pyfilters
    )


# ---------------------------------------------------------------------------
# FTS-ish search (ILIKE on summary + section text)
# ---------------------------------------------------------------------------
async def fts_search(
    session: AsyncSession,
    q: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """텍스트 검색.

    PostgreSQL: ``to_tsvector('simple', col) @@ websearch_to_tsquery('simple', q)``
    SQLite (테스트): ``ILIKE %q%`` 폴백.

    2단 전략: 기본 (AND) 매칭이 0건이고 질의가 다단어면 토큰 OR 로 1회
    재시도한다. 'simple' config 는 한국어 형태소를 모르므로 자연어 질의는
    토큰 하나만 어긋나도 AND 가 통째로 실패하기 때문.

    ``fts_match`` 헬퍼가 dialect 분기를 담당한다.
    """
    if not q.strip():
        return [], 0

    async def _run(any_token: bool) -> tuple[list, list]:
        section_stmt = (
            select(
                Record.id.label("record_id"),
                Record.title.label("title"),
                Record.data_type.label("data_type"),
                Record.tags.label("tags"),
                RecordSection.section_id.label("section_id"),
                RecordSection.title.label("section_title"),
                RecordSection.content_text.label("content_text"),
                RecordSection.section_path.label("section_path"),
                RecordSection.figure_refs.label("figure_refs"),
                RecordSection.table_refs.label("table_refs"),
            )
            .join(RecordSection, RecordSection.record_id == Record.id)
            .where(fts_match(RecordSection.content_text, q, session, any_token=any_token))
            .where(Record.deleted_at.is_(None))
        )
        record_stmt = select(Record).where(
            or_(
                fts_match(Record.title, q, session, any_token=any_token),
                fts_match(Record.summary, q, session, any_token=any_token),
            )
        ).where(Record.deleted_at.is_(None))
        s_rows = (await session.execute(section_stmt.limit(limit * 3))).all()
        r_rows = (await session.execute(record_stmt.limit(limit * 3))).scalars().all()
        return s_rows, r_rows

    section_rows, record_rows = await _run(False)
    if not section_rows and not record_rows and len(q.split()) >= 2:
        section_rows, record_rows = await _run(True)

    seen: set[tuple[str, str | None]] = set()
    items: list[dict] = []
    for row in section_rows:
        key = (row.record_id, row.section_id)
        if key in seen:
            continue
        seen.add(key)
        entry: dict = {
            "record_id": row.record_id,
            "title": row.title,
            "data_type": row.data_type,
            "section_id": row.section_id,
            "section_title": row.section_title,
            "snippet": _make_snippet(row.content_text or "", q),
            "tags": list(row.tags or []),
        }
        sp = getattr(row, "section_path", None)
        if sp:
            entry["section_path"] = sp
        figs = list(getattr(row, "figure_refs", None) or [])
        if figs:
            entry["figure_refs"] = figs
        tabs = list(getattr(row, "table_refs", None) or [])
        if tabs:
            entry["table_refs"] = tabs
        items.append(entry)
    for rec in record_rows:
        key = (rec.id, None)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "record_id": rec.id,
                "title": rec.title,
                "data_type": rec.data_type,
                "section_id": None,
                "section_title": None,
                "snippet": _make_snippet(rec.summary or "", q),
                "tags": list(rec.tags or []),
            }
        )

    total = len(items)
    return items[offset : offset + limit], total


def _make_snippet(text: str, q: str, *, length: int = 300) -> str:
    if not text:
        return ""
    lower = text.lower()
    needle = q.lower()
    idx = lower.find(needle)
    if idx < 0:
        return text[:length]
    start = max(0, idx - 60)
    end = min(len(text), start + length)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


# ---------------------------------------------------------------------------
# Semantic search (pgvector cosine on PG, numpy fallback on SQLite)
# ---------------------------------------------------------------------------
async def semantic_search(
    session: AsyncSession,
    query: str,
    *,
    top_k: int = 5,
    data_types: Sequence[str] | None = None,
    record_ids: Sequence[str] | None = None,
    tag_boost: dict[str, float] | None = None,
    min_score: float | None = None,
) -> list[dict]:
    """시맨틱 검색.

    동작:
        1. ``query`` 를 :func:`api.services.embedding.get_embedder` 로 인코딩.
        2. PostgreSQL: ``embedding <=> :query_vec`` (cosine distance) ORDER BY
           오름차순 + LIMIT — pgvector ivfflat 인덱스 활용.
        3. SQLite (테스트): 모든 미- ``NULL`` 임베딩 행을 가져와 numpy 로
           코사인 유사도 계산 (작은 데이터셋 가정).
        4. 응답: ``[{section_id, record_id, title, section_title,
           content_text[:200], score, data_type, tags}]`` (score 는 0..1
           코사인 유사도, 1 이 최고).

    ``data_types`` / ``record_ids`` 필터는 양쪽 백엔드에서 동일하게 적용.

    에이전트 인지 랭킹 / 스코어 임계값 (양쪽 백엔드 동일 적용):
        - ``tag_boost`` : ``{tag: delta}`` 매핑. 각 결과의 ``tags`` 에 대해
          ``sum(tag_boost.get(tag, 0.0))`` 를 ``score`` 에 가산하고 최종
          ``score`` 를 1.0 으로 클램프 + ``round(.., 4)``. 가산 후 ``score``
          내림차순으로 재정렬한다. None/빈 dict 이면 무동작.
        - ``min_score`` : 가산 후 ``score`` 가 이 값보다 작은(strict) 결과를
          제거한다. None 이면 무동작.

    연산 순서: 기본 유사도 계산 → ``tag_boost`` 가산+재정렬 → ``min_score``
    필터 → ``top_k`` 자르기. ``tag_boost``/``min_score`` 가 활성이면 DB/numpy
    에서 ``max(top_k * 3, top_k)`` 만큼 넉넉히 후보를 가져온 뒤 파이썬에서
    가산→필터→``[:top_k]`` 순으로 처리한다. 둘 다 비활성이면 기존의 SQL
    ``LIMIT top_k`` 동작을 그대로 유지해 성능 저하를 피한다.
    """
    from .embedding import get_embedder

    if not (query or "").strip():
        return []

    embedder = get_embedder()
    # E5 비대칭 모델: 질의는 query prefix — passage prefix 로 인코딩하면
    # ranking 이 모델 학습 의도와 어긋난다.
    qvec = embedder.encode_query(query)
    top_k = max(1, min(int(top_k), 100))

    # tag_boost / min_score 가 활성이면 재랭킹/필터를 위해 후보 풀을 넓게 가져온다.
    rerank = bool(tag_boost) or (min_score is not None)
    fetch_k = max(top_k * 3, top_k) if rerank else top_k

    def _apply_rerank(results: list[dict]) -> list[dict]:
        """tag_boost 가산+재정렬 → min_score 필터 → top_k 자르기."""
        if tag_boost:
            for r in results:
                delta = sum(tag_boost.get(t, 0.0) for t in r.get("tags") or [])
                if delta:
                    r["score"] = round(min(1.0, r["score"] + delta), 4)
            results.sort(key=lambda r: r["score"], reverse=True)
        if min_score is not None:
            results = [r for r in results if r["score"] >= min_score]
        return results[:top_k]

    if is_postgres(session):
        # pgvector cosine distance: ``<=>`` (0 = identical, 2 = opposite).
        # similarity = 1 - distance/2 로 0..1 정규화 (cosine sim ∈ [-1,1]).
        # ``return_type=Float()`` 명시 — 미명시 시 SQLAlchemy 가 결과 타입을 Vector
        # 로 추정해 pgvector ORM result processor 가 float scalar 를 vector 처럼
        # 파싱하다 TypeError 발생 (`'float' object is not subscriptable`).
        distance = RecordSection.embedding.op("<=>", return_type=Float())(qvec).label("distance")
        stmt = (
            select(
                RecordSection.id.label("section_pk"),
                RecordSection.section_id.label("section_id"),
                RecordSection.record_id.label("record_id"),
                RecordSection.title.label("section_title"),
                RecordSection.content_text.label("content_text"),
                RecordSection.section_path.label("section_path"),
                RecordSection.figure_refs.label("figure_refs"),
                RecordSection.table_refs.label("table_refs"),
                Record.title.label("title"),
                Record.data_type.label("data_type"),
                Record.tags.label("tags"),
                distance,
            )
            .join(Record, Record.id == RecordSection.record_id)
            .where(RecordSection.embedding.is_not(None))
            .where(Record.deleted_at.is_(None))
        )
        if data_types:
            stmt = stmt.where(Record.data_type.in_(list(data_types)))
        if record_ids:
            stmt = stmt.where(RecordSection.record_id.in_(list(record_ids)))
        stmt = stmt.order_by(distance.asc()).limit(fetch_k)
        rows = (await session.execute(stmt)).all()
        results: list[dict] = []
        for row in rows:
            d = float(row.distance) if row.distance is not None else 2.0
            sim = max(0.0, 1.0 - d / 2.0)
            entry: dict = {
                "record_id": row.record_id,
                "section_id": row.section_id,
                "title": row.title,
                "section_title": row.section_title,
                "data_type": row.data_type,
                "snippet": (row.content_text or "")[:200],
                "score": round(sim, 4),
                "tags": list(row.tags or []),
            }
            # 선택적 필드 — 빈 값이면 키 생략해 payload 슬림 유지.
            sp = getattr(row, "section_path", None)
            if sp:
                entry["section_path"] = sp
            figs = list(getattr(row, "figure_refs", None) or [])
            if figs:
                entry["figure_refs"] = figs
            tabs = list(getattr(row, "table_refs", None) or [])
            if tabs:
                entry["table_refs"] = tabs
            results.append(entry)
        if rerank:
            results = _apply_rerank(results)
        return results

    # SQLite 폴백 — 전체 로드 후 numpy 로 코사인.
    import numpy as np

    stmt = (
        select(RecordSection, Record)
        .join(Record, Record.id == RecordSection.record_id)
        .where(RecordSection.embedding.is_not(None))
        .where(Record.deleted_at.is_(None))
    )
    if data_types:
        stmt = stmt.where(Record.data_type.in_(list(data_types)))
    if record_ids:
        stmt = stmt.where(RecordSection.record_id.in_(list(record_ids)))
    rows = (await session.execute(stmt)).all()

    if not rows:
        return []

    q = np.asarray(qvec, dtype="float32")
    qnorm = float(np.linalg.norm(q))
    if qnorm < 1e-12:
        return []
    q /= qnorm

    scored: list[tuple[float, RecordSection, Record]] = []
    for sec, rec in rows:
        emb = sec.embedding
        if emb is None:
            continue
        v = np.asarray(emb, dtype="float32")
        if v.shape != q.shape:
            continue
        vnorm = float(np.linalg.norm(v))
        if vnorm < 1e-12:
            continue
        sim = float(np.dot(q, v) / vnorm)
        # cosine sim ∈ [-1, 1] → [0, 1] (음수는 0 으로 클립).
        sim01 = max(0.0, min(1.0, (sim + 1.0) / 2.0))
        scored.append((sim01, sec, rec))

    scored.sort(key=lambda t: t[0], reverse=True)
    out: list[dict] = []
    for score, sec, rec in scored[:fetch_k]:
        entry: dict = {
            "record_id": rec.id,
            "section_id": sec.section_id,
            "title": rec.title,
            "section_title": sec.title,
            "data_type": rec.data_type,
            "snippet": (sec.content_text or "")[:200],
            "score": round(score, 4),
            "tags": list(rec.tags or []),
        }
        sp = getattr(sec, "section_path", None)
        if sp:
            entry["section_path"] = sp
        figs = list(getattr(sec, "figure_refs", None) or [])
        if figs:
            entry["figure_refs"] = figs
        tabs = list(getattr(sec, "table_refs", None) or [])
        if tabs:
            entry["table_refs"] = tabs
        out.append(entry)
    if rerank:
        out = _apply_rerank(out)
    return out


# ---------------------------------------------------------------------------
# /api/data — Cline SR core
# ---------------------------------------------------------------------------
async def data_for_agent(
    session: AsyncSession,
    agent: str,
    *,
    query: str | None = None,
    data_types: Sequence[str] | None = None,
    limit: int = 5,
) -> dict:
    """에이전트가 사용할 레코드 후보를 반환한다.

    1) ``agent`` ∈ ``records.agents`` 인 레코드 후보군 결정.
    2) DOC 타입은 ``record_sections.content_text`` 에 ILIKE %q% 매칭.
       나머지는 ``records.summary``/``title`` 매칭.
    3) ``AgentRecord.priority`` + 매칭 카운트로 단순 relevance 산출.
    """
    limit = max(1, min(limit, 20))

    overlap_pred = array_overlap(Record.agents, [agent], session)
    candidate_stmt = select(Record).where(overlap_pred.where_clause)
    if data_types:
        candidate_stmt = candidate_stmt.where(Record.data_type.in_(list(data_types)))

    candidates_raw: list[Record] = (
        (await session.execute(candidate_stmt)).scalars().unique().all()
    )
    if overlap_pred.python_filter is not None:
        candidates: list[Record] = overlap_pred.apply_python(candidates_raw)
    else:
        candidates = list(candidates_raw)

    priority_map: dict[str, int] = {}
    if candidates:
        ids = [r.id for r in candidates]
        prio_rows = (
            await session.execute(
                select(AgentRecord.record_id, AgentRecord.priority).where(
                    (AgentRecord.agent_type == agent)
                    & AgentRecord.record_id.in_(ids)
                )
            )
        ).all()
        priority_map = {row.record_id: int(row.priority) for row in prio_rows}

    results: list[dict] = []
    q = (query or "").strip()
    pattern_present = bool(q)

    for rec in candidates:
        priority = priority_map.get(rec.id, 1)

        if rec.data_type == "DOC" and pattern_present:
            sec_rows = (
                await session.execute(
                    select(RecordSection)
                    .where(RecordSection.record_id == rec.id)
                    .where(summary_ilike(RecordSection.content_text, q))
                )
            ).scalars().all()
            if not sec_rows:
                results.append(
                    {
                        "record_id": rec.id,
                        "title": rec.title,
                        "data_type": rec.data_type,
                        "section_id": None,
                        "section_title": None,
                        "snippet": _make_snippet(rec.summary or "", q),
                        "relevance": _score(priority, 0),
                        "tags": list(rec.tags or []),
                    }
                )
                continue
            for sec in sec_rows:
                hits = (sec.content_text or "").lower().count(q.lower()) if q else 0
                results.append(
                    {
                        "record_id": rec.id,
                        "title": rec.title,
                        "data_type": rec.data_type,
                        "section_id": sec.section_id,
                        "section_title": sec.title,
                        "snippet": _make_snippet(sec.content_text or "", q),
                        "relevance": _score(priority, hits),
                        "tags": list(rec.tags or []),
                    }
                )
        else:
            haystack = " ".join([rec.title or "", rec.summary or ""])
            if q and q.lower() not in haystack.lower():
                hits = 0
            else:
                hits = haystack.lower().count(q.lower()) if q else 0
            results.append(
                {
                    "record_id": rec.id,
                    "title": rec.title,
                    "data_type": rec.data_type,
                    "section_id": None,
                    "section_title": None,
                    "snippet": _make_snippet(rec.summary or rec.title or "", q),
                    "relevance": _score(priority, hits),
                    "tags": list(rec.tags or []),
                }
            )

    results.sort(key=lambda r: r["relevance"], reverse=True)
    total = len(results)
    return {
        "agent": agent,
        "query": query,
        "results": results[:limit],
        "total_matched": total,
    }


def _score(priority: int, hits: int) -> float:
    """단순 relevance = priority 가중 + 매칭 횟수.

    priority 범위 1-5 가정. 매칭 0 회 → 0.1 floor.
    """
    base = priority / 5.0
    boost = min(hits * 0.1, 0.5)
    score = base * 0.7 + boost + 0.05
    return round(min(score, 1.0), 3)


# ---------------------------------------------------------------------------
# Hybrid search (Reciprocal Rank Fusion)
# ---------------------------------------------------------------------------
async def hybrid_search(
    session: AsyncSession,
    q: str,
    *,
    top_k: int = 10,
    data_types: Sequence[str] | None = None,
    record_ids: Sequence[str] | None = None,
    rrf_k: int = 60,
    fetch_multiplier: int = 3,
) -> list[dict]:
    """semantic + fts 결과를 Reciprocal Rank Fusion 으로 결합.

    동작:
        1. ``semantic_search`` 와 ``fts_search`` 를 각각 ``top_k * fetch_multiplier``
           만큼 호출.
        2. 각 결과의 rank 로 RRF score 계산: ``score = sum(1 / (rrf_k + rank_i))``.
           rrf_k 기본 60 (TREC 권장 default — Cormack et al., 2009).
        3. 합산 score 내림차순으로 정렬 후 top_k 반환.

    응답 schema 는 semantic_search 와 동일하되 추가로:
        - ``score``: RRF 점수 (0..1 cosine 이 아님, 0.01~0.05 범위가 일반적)
        - ``score_semantic``: 원 semantic score (있을 때만)
        - ``score_fts_rank``: FTS 결과 내 rank (1-based, 있을 때만)

    설계 노트:
        - FTS 는 score 없이 rank 만 의미가 있으므로 RRF 가 자연스러운 결합.
        - 중복 키는 (record_id, section_id) — section 없는 fts 결과 (Record-level
          매칭) 는 (record_id, None) 으로 별개 hit 으로 취급.
    """
    if not (q or "").strip():
        return []

    fetch_k = max(top_k * fetch_multiplier, top_k)
    rrf_k = max(1, int(rrf_k))

    # 1) semantic 결과 가져오기
    sem_hits = await semantic_search(
        session,
        q,
        top_k=fetch_k,
        data_types=data_types,
        record_ids=record_ids,
    )

    # 2) FTS 결과 가져오기 (record_ids/data_types 필터는 후처리)
    fts_items, _ = await fts_search(session, q, limit=fetch_k)
    rid_set = set(record_ids) if record_ids else None
    dt_set = set(data_types) if data_types else None
    if rid_set is not None:
        fts_items = [it for it in fts_items if it.get("record_id") in rid_set]
    if dt_set is not None:
        fts_items = [it for it in fts_items if it.get("data_type") in dt_set]

    # 3) RRF 합산
    fused: dict[tuple[str, str | None], dict] = {}

    def _key(item: dict) -> tuple[str, str | None]:
        return (item.get("record_id") or "", item.get("section_id"))

    for rank, item in enumerate(sem_hits, start=1):
        k = _key(item)
        entry = fused.setdefault(k, dict(item))
        entry["score_semantic"] = float(item.get("score") or 0.0)
        entry["rrf_score"] = entry.get("rrf_score", 0.0) + 1.0 / (rrf_k + rank)

    for rank, item in enumerate(fts_items, start=1):
        k = _key(item)
        entry = fused.setdefault(k, dict(item))
        entry["score_fts_rank"] = rank
        entry["rrf_score"] = entry.get("rrf_score", 0.0) + 1.0 / (rrf_k + rank)

    # 4) 정렬 + 응답 정규화
    fused_list = list(fused.values())
    fused_list.sort(key=lambda x: x.get("rrf_score", 0.0), reverse=True)

    out: list[dict] = []
    # rerank 입력은 자르기 전 후보를 넉넉히 (top_k * 2) 넘겨야 의미 있음.
    rerank_in_k = min(len(fused_list), max(top_k, top_k * 2))
    for item in fused_list[:rerank_in_k]:
        rrf = round(float(item.pop("rrf_score", 0.0)), 6)
        item["score"] = rrf  # 통일된 score 필드 (semantic 응답과 호환)
        out.append(item)

    # 5) 선택적 cross-encoder rerank — env AIDH_RERANK_PROVIDER 활성 시만.
    try:
        from .rerank import maybe_rerank
        out = maybe_rerank(q, out, top_k=top_k)
    except Exception:  # pragma: no cover — rerank 실패가 검색을 막아선 안 됨
        out = out[:top_k]
    else:
        # rerank 가 no-op 이었으면 (provider off) 입력 길이를 보존했을 수 있음 → top_k.
        out = out[:top_k]
    return out


__all__ = [
    "array_contains_all",
    "array_overlap_compat",
    "data_for_agent",
    "fts_search",
    "hybrid_search",
    "semantic_search",
    "tag_search",
]
