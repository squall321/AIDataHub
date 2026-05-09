"""의미 그룹 (semantic group) 클러스터링 서비스.

본 모듈은 ``record_sections.embedding`` (vector(384)) + ``records.tags``
+ 메타를 활용해, 비슷한 의미의 record 군을 자동으로 묶어 작은 AI 가
한 번에 가져갈 수 있게 한다.

제공 API:
    - :func:`build_query_groups`   : 자연어 질의 → top-K 시맨틱 검색 →
                                     그리디 클러스터링 → 그룹 라벨 합성
    - :func:`cluster_around_record`: 한 record 의 의미 그룹 (semantic /
                                     tag / hybrid) 모두 반환
    - :func:`fetch_records_bulk`   : 여러 record id 한 번에 조회
                                     (그룹 fetch 시 N+1 회피)

설계 노트:
    - 알고리즘: 단순 그리디 클러스터링.
        시드 정렬 (score 내림차순) → 첫 시드와 cosine ≥ ``sim_threshold``
        인 record 들을 한 그룹에 묶음 → 다음 미할당 시드 → 반복.
    - K-means 대신 그리디 — 외부 의존 없이 numpy 만 사용. 결과는
        ``n_groups`` 와 ``min_score`` 두 hyperparam 이 결정.
    - 공통 태그/도메인/agent 추출은 set 교집합 기반.
    - 임베딩 정규화는 매 호출마다 수행 (record 수가 많지 않은 워크로드
        가정 — 보통 top-K 50 이하).
"""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Record, RecordSection

from .search_svc import semantic_search

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 내부 유틸 — 코사인 유사도 (numpy)
# ---------------------------------------------------------------------------
def _normalize(vec: Sequence[float]):
    import numpy as np

    a = np.asarray(vec, dtype="float32")
    norm = float(np.linalg.norm(a))
    if norm < 1e-12:
        return None
    return a / norm


def _cosine(a, b) -> float:
    """두 *정규화된* 벡터의 코사인 유사도 ∈ [-1, 1]."""
    import numpy as np

    return float(np.dot(a, b))


# ---------------------------------------------------------------------------
# 그리디 클러스터링
# ---------------------------------------------------------------------------
def greedy_cluster(
    items: list[dict[str, Any]],
    *,
    n_groups: int,
    sim_threshold: float = 0.85,
) -> list[list[dict[str, Any]]]:
    """``items`` 를 그리디 알고리즘으로 ``n_groups`` 까지 묶는다.

    ``items[i]`` 는 ``{"_vec": list[float] | None, ...payload}`` 형태를
    가정한다 (``_vec`` 키가 없거나 None 이면 별도 그룹 후보).

    동작:
        1. ``items`` 를 입력 순서 (caller 가 score 내림차순으로 정렬해
           넘긴다고 가정) 로 순회.
        2. 미할당 첫 항목을 시드로 새 그룹을 연다.
        3. 같은 시드와 cosine ≥ ``sim_threshold`` 인 미할당 항목을 모두
           그 그룹에 흡수.
        4. 다음 미할당 항목 → 새 시드. ``n_groups`` 도달 또는 항목
           소진 시 종료.

    그룹 수가 ``n_groups`` 미만이어도 그대로 반환한다 (실제 데이터가
    적으면 자연스럽게 그룹이 적게 나온다).
    """
    n_groups = max(1, int(n_groups))
    sim_threshold = max(-1.0, min(1.0, float(sim_threshold)))

    # 정규화 벡터 사전 계산 (None 인 것은 normed = None).
    normed: list[Any] = []
    for it in items:
        v = it.get("_vec")
        normed.append(_normalize(v) if v is not None else None)

    assigned = [False] * len(items)
    groups: list[list[dict[str, Any]]] = []

    for i, it in enumerate(items):
        if assigned[i]:
            continue
        if len(groups) >= n_groups:
            # 남은 미할당 항목들은 가장 가까운 기존 그룹에 흡수.
            seed_vec = normed[i]
            best_g = -1
            best_sim = -2.0
            if seed_vec is not None:
                for gi, group in enumerate(groups):
                    g_seed = group[0].get("_normed")
                    if g_seed is None:
                        continue
                    s = _cosine(seed_vec, g_seed)
                    if s > best_sim:
                        best_sim = s
                        best_g = gi
            if best_g < 0:
                # 시드 벡터가 없거나 그룹이 비었다면 마지막 그룹에 추가.
                best_g = len(groups) - 1
            groups[best_g].append({**it, "_normed": normed[i]})
            assigned[i] = True
            continue

        # 새 그룹 시작.
        seed_vec = normed[i]
        new_group: list[dict[str, Any]] = [{**it, "_normed": seed_vec}]
        assigned[i] = True

        if seed_vec is not None:
            for j in range(i + 1, len(items)):
                if assigned[j]:
                    continue
                v_j = normed[j]
                if v_j is None:
                    continue
                if _cosine(seed_vec, v_j) >= sim_threshold:
                    new_group.append({**items[j], "_normed": v_j})
                    assigned[j] = True
        groups.append(new_group)

    return groups


# ---------------------------------------------------------------------------
# 공통 메타 추출
# ---------------------------------------------------------------------------
def _common_subset(values: Iterable[Iterable[str]]) -> list[str]:
    """모든 record 에 공통으로 존재하는 값들 (교집합)."""
    sets: list[set[str]] = []
    for v in values:
        if v is None:
            continue
        sets.append({str(x) for x in v if x})
    if not sets:
        return []
    inter = sets[0].copy()
    for s in sets[1:]:
        inter &= s
    return sorted(inter)


def _majority_string(values: Iterable[str | None]) -> str | None:
    """다수결 문자열 (None / 빈값 제외). 동률은 사전 순 첫번째."""
    counter: Counter[str] = Counter()
    for v in values:
        if v:
            counter[str(v)] += 1
    if not counter:
        return None
    top = counter.most_common(1)[0]
    return top[0]


def _build_label(
    common_tags: list[str],
    common_domain: str | None,
    fallback_query: str,
    suffix_idx: int,
) -> str:
    """그룹 라벨 합성.

    - ``common_tags`` 가 있으면 ``"<query> — <tag1>·<tag2>"`` 형태.
    - 없으면 ``"<query> — 그룹 N"``.
    """
    base = (fallback_query or "그룹").strip() or "그룹"
    if common_tags:
        joined = "·".join(common_tags[:3])
        return f"{base} — {joined}"
    if common_domain:
        return f"{base} — {common_domain}"
    return f"{base} — 그룹 {suffix_idx}"


# ---------------------------------------------------------------------------
# 자연어 질의 → 자동 그룹화 (POST /api/groups/auto)
# ---------------------------------------------------------------------------
async def build_query_groups(
    session: AsyncSession,
    *,
    query: str,
    n_groups: int = 3,
    limit_per_group: int = 5,
    min_score: float = 0.4,
    top_k: int = 50,
    sim_threshold: float = 0.85,
) -> dict[str, Any]:
    """자연어 ``query`` 를 시맨틱 검색 후 그리디 클러스터링.

    절차:
        1. ``query`` → ``semantic_search(top_k)`` (record_sections 단위)
        2. ``min_score`` 이하 결과 컷
        3. record_id 별 최고 점수 1건만 유지 (중복 record 제거)
        4. record 별 첫 sec 의 ``embedding`` 으로 그리디 클러스터
        5. 각 그룹의 공통 태그/도메인/agent 추출
        6. 라벨 합성 → 응답
    """
    n_groups = max(1, int(n_groups))
    limit_per_group = max(1, min(int(limit_per_group), 20))
    min_score = max(0.0, min(float(min_score), 1.0))
    top_k = max(n_groups * limit_per_group, int(top_k))

    sem_hits = await semantic_search(session, query, top_k=top_k)
    sem_hits = [h for h in sem_hits if float(h.get("score", 0.0)) >= min_score]
    if not sem_hits:
        return {
            "query": query,
            "total_records": 0,
            "n_groups_requested": n_groups,
            "groups": [],
        }

    # record 별로 가장 점수 높은 hit 1건만 유지. 입력 순서가 score 내림차
    # 순이므로 처음 등장한 record_id 만 채택하면 충분.
    by_record: dict[str, dict[str, Any]] = {}
    for h in sem_hits:
        rid = h.get("record_id")
        if not rid or rid in by_record:
            continue
        by_record[rid] = h

    record_ids = list(by_record.keys())
    if not record_ids:
        return {
            "query": query,
            "total_records": 0,
            "n_groups_requested": n_groups,
            "groups": [],
        }

    # 각 record 의 메타 (tags / agents / domain / title / data_type 등) 와
    # 임베딩 (해당 hit 의 section 임베딩) 을 한 번에 로드.
    rec_rows = (
        (await session.execute(select(Record).where(Record.id.in_(record_ids))))
        .scalars()
        .all()
    )
    rec_map = {r.id: r for r in rec_rows}

    # 각 hit 의 section 임베딩 — semantic_search 응답에는 embedding 이
    # 포함되지 않으므로 section_id 로 다시 조회. (top-K 작아 N+1 부담 미미.)
    sec_pairs = [
        (h["record_id"], h.get("section_id"))
        for h in by_record.values()
        if h.get("section_id")
    ]
    embedding_lookup: dict[tuple[str, str], list[float] | None] = {}
    if sec_pairs:
        ids = [(rid, sid) for rid, sid in sec_pairs]
        rec_set = {rid for rid, _ in ids}
        sec_set = {sid for _, sid in ids}
        sec_rows = (
            (
                await session.execute(
                    select(RecordSection)
                    .where(RecordSection.record_id.in_(rec_set))
                    .where(RecordSection.section_id.in_(sec_set))
                )
            )
            .scalars()
            .all()
        )
        for s in sec_rows:
            embedding_lookup[(s.record_id, s.section_id)] = s.embedding

    # 클러스터 입력 build — 정렬은 입력 (score 내림차) 그대로 유지.
    cluster_input: list[dict[str, Any]] = []
    for rid, hit in by_record.items():
        rec = rec_map.get(rid)
        if rec is None:
            continue
        sid = hit.get("section_id")
        emb = embedding_lookup.get((rid, sid)) if sid else None
        cluster_input.append(
            {
                "record_id": rid,
                "title": rec.title,
                "data_type": rec.data_type,
                "tags": list(rec.tags or []),
                "agents": list(rec.agents or []),
                "domain": rec.domain,
                "summary": rec.summary or "",
                "score": float(hit.get("score", 0.0)),
                "section_id": sid,
                "snippet": hit.get("snippet", ""),
                "_vec": emb,
            }
        )

    raw_groups = greedy_cluster(
        cluster_input, n_groups=n_groups, sim_threshold=sim_threshold
    )

    # 라벨 / 공통 메타 추출 + 응답 정규화.
    out_groups: list[dict[str, Any]] = []
    for idx, group in enumerate(raw_groups, start=1):
        if not group:
            continue
        common_tags = _common_subset(g.get("tags") or [] for g in group)
        common_agents = _common_subset(g.get("agents") or [] for g in group)
        common_domain = _majority_string(g.get("domain") for g in group)
        # 대표 record = 그룹 내 score 최고.
        rep = max(group, key=lambda g: float(g.get("score", 0.0)))
        records_view = [
            {
                "id": g["record_id"],
                "title": g["title"],
                "data_type": g["data_type"],
                "tags": g.get("tags", []),
                "score": round(float(g.get("score", 0.0)), 4),
                "section_id": g.get("section_id"),
                "snippet": (g.get("snippet") or "")[:200],
            }
            for g in group[:limit_per_group]
        ]
        out_groups.append(
            {
                "label": _build_label(common_tags, common_domain, query, idx),
                "common_tags": common_tags,
                "common_agents": common_agents,
                "common_domain": common_domain,
                "size": len(group),
                "representative_record": {
                    "id": rep["record_id"],
                    "title": rep["title"],
                    "data_type": rep["data_type"],
                    "score": round(float(rep.get("score", 0.0)), 4),
                },
                "records": records_view,
            }
        )

    return {
        "query": query,
        "total_records": sum(g["size"] for g in out_groups),
        "n_groups_requested": n_groups,
        "groups": out_groups,
    }


# ---------------------------------------------------------------------------
# record 한 건의 의미 그룹 (GET /api/records/{id}/cluster)
# ---------------------------------------------------------------------------
async def cluster_around_record(
    session: AsyncSession,
    *,
    anchor_id: str,
    mode: str = "hybrid",
    sim_threshold: float = 0.85,
    tag_threshold: float = 0.6,
    limit: int = 20,
) -> dict[str, Any]:
    """``anchor_id`` 와 같은 의미 그룹의 record 모두 반환.

    mode:
        - ``semantic``: anchor 의 첫 section 임베딩을 기준으로 cosine
          ≥ ``sim_threshold`` 인 record. anchor 임베딩이 없으면 빈 결과.
        - ``tag``: anchor.tags 와 jaccard ≥ ``tag_threshold`` 인 record.
        - ``hybrid``: 두 신호 합산 (semantic 점수 0.6 + tag jaccard 0.4),
          최종 score ≥ ``sim_threshold * 0.6 + tag_threshold * 0.4`` 컷.

    응답:
        ``{anchor_record, cluster_size, items: [{id, title, score,
        shared_tags}], mode}``
    """
    mode = (mode or "hybrid").strip().lower()
    if mode not in ("semantic", "tag", "hybrid"):
        raise ValueError(
            f"unsupported mode: {mode!r} (expected semantic|tag|hybrid)"
        )

    anchor = (
        await session.execute(select(Record).where(Record.id == anchor_id))
    ).scalar_one_or_none()
    if anchor is None:
        raise LookupError(anchor_id)

    anchor_tags = set(str(t) for t in (anchor.tags or []) if t)

    # anchor 의 첫 (level 가장 작고 section_id 가장 작은) section 임베딩 로드.
    anchor_sec = (
        await session.execute(
            select(RecordSection)
            .where(RecordSection.record_id == anchor.id)
            .where(RecordSection.embedding.is_not(None))
            .order_by(RecordSection.level.asc(), RecordSection.section_id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    anchor_vec = _normalize(anchor_sec.embedding) if anchor_sec else None

    # 후보군: 모든 다른 record (anchor 제외).
    candidates = (
        (
            await session.execute(
                select(Record).where(Record.id != anchor.id)
            )
        )
        .scalars()
        .all()
    )

    # 각 candidate 의 첫 임베딩 (semantic 또는 hybrid 모드일 때만 필요).
    cand_embeds: dict[str, list[float] | None] = {}
    if mode in ("semantic", "hybrid") and anchor_vec is not None and candidates:
        cand_ids = [c.id for c in candidates]
        sec_rows = (
            (
                await session.execute(
                    select(RecordSection)
                    .where(RecordSection.record_id.in_(cand_ids))
                    .where(RecordSection.embedding.is_not(None))
                    .order_by(
                        RecordSection.record_id.asc(),
                        RecordSection.level.asc(),
                        RecordSection.section_id.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        # record 당 첫 section 임베딩만 유지.
        for s in sec_rows:
            cand_embeds.setdefault(s.record_id, s.embedding)

    items: list[dict[str, Any]] = []
    for c in candidates:
        c_tags = set(str(t) for t in (c.tags or []) if t)
        # tag jaccard
        if anchor_tags or c_tags:
            jaccard = len(anchor_tags & c_tags) / max(
                len(anchor_tags | c_tags), 1
            )
        else:
            jaccard = 0.0
        shared = sorted(anchor_tags & c_tags)

        sem_sim = 0.0
        if anchor_vec is not None:
            v = cand_embeds.get(c.id)
            if v is not None:
                vn = _normalize(v)
                if vn is not None:
                    sem_sim = max(0.0, _cosine(anchor_vec, vn))

        if mode == "semantic":
            if anchor_vec is None or sem_sim < sim_threshold:
                continue
            score = sem_sim
        elif mode == "tag":
            if jaccard < tag_threshold:
                continue
            score = jaccard
        else:  # hybrid
            combined = sem_sim * 0.6 + jaccard * 0.4
            cutoff = sim_threshold * 0.6 + tag_threshold * 0.4
            if combined < cutoff:
                continue
            score = combined

        items.append(
            {
                "id": c.id,
                "title": c.title,
                "data_type": c.data_type,
                "score": round(float(score), 4),
                "shared_tags": shared,
                "tag_jaccard": round(float(jaccard), 4),
                "semantic_sim": round(float(sem_sim), 4),
            }
        )

    items.sort(key=lambda r: r["score"], reverse=True)
    items = items[: max(1, int(limit))]

    return {
        "anchor_record": {
            "id": anchor.id,
            "title": anchor.title,
            "data_type": anchor.data_type,
            "tags": sorted(anchor_tags),
        },
        "mode": mode,
        "cluster_size": len(items),
        "items": items,
    }


# ---------------------------------------------------------------------------
# 여러 record 한 번에 (POST /api/records/bulk)
# ---------------------------------------------------------------------------
async def fetch_records_bulk(
    session: AsyncSession,
    *,
    ids: Sequence[str],
    include_sections: bool = False,
) -> dict[str, Any]:
    """여러 ``ids`` 를 한 번에 조회. 그룹 fetch 시 N+1 회피.

    ``include_sections=True`` 면 ``record_sections`` 도 같이 묶어 반환
    (DOC variant 외에는 보통 빈 리스트).
    """
    cleaned = [str(i).strip() for i in (ids or []) if str(i).strip()]
    if not cleaned:
        return {"items": [], "missing": []}

    rows = (
        (await session.execute(select(Record).where(Record.id.in_(cleaned))))
        .scalars()
        .all()
    )
    found = {r.id: r for r in rows}
    missing = [i for i in cleaned if i not in found]

    sections_map: dict[str, list[dict[str, Any]]] = {}
    if include_sections and rows:
        sec_rows = (
            (
                await session.execute(
                    select(RecordSection)
                    .where(RecordSection.record_id.in_(list(found.keys())))
                    .order_by(
                        RecordSection.record_id.asc(),
                        RecordSection.level.asc(),
                        RecordSection.section_id.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        for s in sec_rows:
            sections_map.setdefault(s.record_id, []).append(
                {
                    "section_id": s.section_id,
                    "level": s.level,
                    "title": s.title,
                    "content_text": s.content_text or "",
                    "figure_refs": list(s.figure_refs or []),
                    "table_refs": list(s.table_refs or []),
                }
            )

    items: list[dict[str, Any]] = []
    for rid in cleaned:
        r = found.get(rid)
        if r is None:
            continue
        item = {
            "id": r.id,
            "data_type": r.data_type,
            "title": r.title,
            "summary": r.summary or "",
            "tags": list(r.tags or []),
            "agents": list(r.agents or []),
            "domain": r.domain,
            "classification": r.classification,
            "status": r.status,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        if include_sections:
            item["sections"] = sections_map.get(r.id, [])
        items.append(item)

    return {"items": items, "missing": missing}


__all__ = [
    "build_query_groups",
    "cluster_around_record",
    "fetch_records_bulk",
    "greedy_cluster",
]
