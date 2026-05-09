"""``/api/taxonomy`` — 작은 모델용 어휘 발견 / 동의어 매핑 라우터.

낮은 수준 AI 가 시스템 어휘를 한 번에 파악하고 비공식 표현을 정식 태그로
정확히 매핑할 수 있게 한다.

엔드포인트:
    - GET /api/taxonomy/tags          : 태그 + 사용 빈도 + data_type 분포
    - GET /api/taxonomy/data-types    : data_type 분포 + 추천 사용 패턴
    - GET /api/taxonomy/domains       : domain 필드 분포
    - GET /api/taxonomy/agents        : agent_type 카탈로그 + record 수
    - GET /api/taxonomy/tags/resolve  : 비공식 표현 → 정식 태그 매핑 (핵심)
    - GET /api/taxonomy/classification: classification enum + 의미 + 분포
    - GET /api/taxonomy/status        : status enum + 의미 + 분포
    - GET /api/taxonomy/access-pattern: access_pattern enum + 의미 + 분포

본 라우터는 read-only 이며 ``/api/meta/options`` 와 동일하게 메타데이터로
취급되어 인증을 면제한다 (관리 정책 ``AUTH_REQUIRED=true`` 환경에서도 개방).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base import get_session
from api.db.models import Agent, Record
from api.schemas.common import (
    ACCESS_PATTERNS,
    CLASSIFICATIONS,
    STATUSES,
)
from api.schemas.id_format import DATA_TYPES

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/taxonomy", tags=["taxonomy"])


# ---------------------------------------------------------------------------
# 동의어 사전 (synonym dict)
# 정식(canonical) 태그 → 비공식 표현 후보들.
# resolve 엔드포인트가 양방향 매핑에 사용한다.
# 도메인: CAE / 시험 / 시뮬레이션 / 강의 / 일반.
# ---------------------------------------------------------------------------
SYNONYM_DICT: dict[str, list[str]] = {
    # ---- CAE 일반 ----
    "유한요소": ["FEM", "fem", "finite element", "finite element method"],
    "IGA": ["isogeometric", "isogeometric analysis", "아이지에이"],
    "NURBS": ["nurbs", "비균일 유리 B-스플라인"],
    "FE-IGA": ["fe-iga", "fe iga", "복합 해석"],
    "응력": ["stress", "stress tensor"],
    "변형률": ["strain", "strain rate"],
    "변위": ["displacement", "disp"],
    "강성": ["stiffness", "rigidity"],
    "메쉬": ["mesh", "mesh generation", "그리드"],
    "수렴": ["convergence", "수렴성"],
    "비선형": ["nonlinear", "non-linear", "nonlin"],
    "선형": ["linear"],
    # ---- 시뮬레이션 솔버 ----
    "LS-DYNA": ["lsdyna", "ls dyna", "엘에스 다이나"],
    "Abaqus": ["abaqus", "아바커스"],
    "Ansys": ["ansys", "앤시스"],
    "OpenFOAM": ["openfoam", "오픈폼"],
    "디지털트윈": ["digital twin", "DT", "dt"],
    "CFD": ["cfd", "computational fluid dynamics", "전산유체"],
    "FEA": ["fea", "finite element analysis"],
    "충돌해석": ["crash", "crash analysis", "impact"],
    # ---- 시험 (test) ----
    "낙하시험": ["drop test", "droptest", "drop", "낙하 시험", "낙하"],
    "충돌시험": ["impact test", "crash test", "충돌 시험"],
    "인장시험": ["tensile test", "tension test", "인장 시험"],
    "압축시험": ["compression test", "압축 시험"],
    "피로시험": ["fatigue test", "피로 시험"],
    "진동시험": ["vibration test", "진동 시험"],
    "내구시험": ["durability test", "내구 시험"],
    "관통시험": ["nail test", "penetration test", "관통 시험"],
    "온도시험": ["temperature test", "thermal test", "온도 시험"],
    # ---- 배터리 ----
    "배터리": ["battery", "셀", "cell"],
    "BMS": ["bms", "battery management system"],
    "리튬이온": ["li-ion", "lithium ion", "lithium-ion"],
    # ---- 강의 / 학습 ----
    "강의": ["lecture", "강좌", "수업"],
    "튜토리얼": ["tutorial", "tut", "사용법"],
    "가이드": ["guide", "manual", "매뉴얼"],
    "보고서": ["report", "리포트"],
    # ---- 데이터 / 측정 ----
    "측정데이터": ["measurement", "raw data", "측정 데이터"],
    "시뮬결과": ["simulation result", "sim result", "해석 결과"],
    "표": ["table", "테이블", "tabular"],
    "그림": ["figure", "image", "이미지"],
    # ---- KooRemapper / 내부 도구 ----
    "KooRemapper": ["koo remapper", "리매퍼", "remapper"],
    # ---- 일반 도메인 ----
    "CAE": ["cae", "computer aided engineering"],
    "MFG": ["mfg", "manufacturing", "제조"],
    "QA": ["qa", "quality assurance", "품질"],
    "RnD": ["r&d", "research and development", "연구개발"],
}


# ``재료`` / ``공정`` / ``절차`` 등 빈도 높은 도메인 단어도 추가.
SYNONYM_DICT.update({
    "재료": ["material", "재질"],
    "공정": ["process", "프로세스"],
    "절차": ["procedure", "프로토콜"],
    "코드": ["code", "스크립트", "script"],
})


# 역방향 인덱스 — 비공식 표현 (소문자 정규화) → 정식 태그 매핑.
def _build_reverse_index(d: dict[str, list[str]]) -> dict[str, str]:
    rev: dict[str, str] = {}
    for canonical, variants in d.items():
        rev[_normalize(canonical)] = canonical
        for v in variants:
            rev.setdefault(_normalize(v), canonical)
    return rev


_WS_RE = re.compile(r"\s+")


def _normalize(s: str) -> str:
    """간단 정규화: 소문자 + 양끝/중간 공백 압축."""
    return _WS_RE.sub(" ", (s or "").strip().lower())


_REVERSE_SYNONYM: dict[str, str] = _build_reverse_index(SYNONYM_DICT)


# ---------------------------------------------------------------------------
# 사람-읽을 수 있는 enum 의미 사전
# ---------------------------------------------------------------------------
CLASSIFICATION_DESC: dict[str, str] = {
    "public": "외부 공개 가능 — 누구나 열람",
    "internal": "사내 공유 — 임직원만 (기본값)",
    "confidential": "기밀 — 권한 부여된 인원만",
    "restricted": "제한 — 특정 직책/프로젝트로 좁힘",
}

STATUS_DESC: dict[str, str] = {
    "draft": "초안 — 작성 중 (기본값)",
    "review": "검토 중 — 승인 대기",
    "approved": "승인 — 정식 사용 가능",
    "deprecated": "폐기 예정 — 신규 사용 자제",
}

ACCESS_PATTERN_DESC: dict[str, str] = {
    "frequent": "자주 접근 — 캐시/임베딩 우선",
    "occasional": "보통 (기본값) — 표준 접근",
    "rare": "드물게 접근 — 콜드 스토리지 후보",
}

DATA_TYPE_USAGE_HINT: dict[str, str] = {
    "DOC": "문서/매뉴얼/보고서 — sections+blocks 트리. RAG 청크 검색 적합.",
    "DATA": "측정/시험 표 — headers+rows. 수치 분석/통계 적합.",
    "SIM": "시뮬레이션 입출력 — solver+inputs/outputs. 재현 가능성 추적.",
    "CAD": "CAD 모델 — file_format+components. 첨부 바이너리 동반.",
    "LOG": "로그/시계열 — free-form. 모니터링/디버그.",
    "FORM": "양식/체크리스트 — free-form.",
    "OTHER": "기타 분류되지 않은 일반 레코드.",
}

# DATA_TYPE 별 흔한 subtype 힌트 (관습; tags/domain 에서 자주 등장하는 카테고리).
DATA_TYPE_SUBTYPES: dict[str, list[str]] = {
    "DOC": ["manual", "report", "lecture", "guide", "tutorial"],
    "DATA": ["test_data", "simulation_result", "measurement", "raw"],
    "SIM": ["explicit", "implicit", "fluid", "structural"],
    "CAD": ["mcad", "ecad", "drawing", "assembly"],
    "LOG": ["timeseries", "monitoring"],
    "FORM": ["checklist", "form"],
    "OTHER": [],
}


# ---------------------------------------------------------------------------
# 헬퍼: tags 분포 집계
# ---------------------------------------------------------------------------
async def _aggregate_tags(
    session: AsyncSession,
) -> dict[str, dict[str, Any]]:
    """모든 record 를 스캔해 tag→{count, data_types, agents} 누적.

    Records 테이블 전체를 한 번 select 하므로 큰 DB 에선 비싸지만 dialect-
    agnostic 한 단순 구현 (PG/SQLite 모두 동작). 캐시는 라우터 레벨에서
    필요하면 추가.
    """
    rows = (
        await session.execute(
            select(Record.tags, Record.data_type, Record.agents).where(
                Record.deleted_at.is_(None)
            )
        )
    ).all()
    bucket: dict[str, dict[str, Any]] = {}
    for tag_arr, dt, agent_arr in rows:
        if not tag_arr:
            continue
        for tag in tag_arr:
            if not isinstance(tag, str) or not tag.strip():
                continue
            entry = bucket.setdefault(
                tag,
                {"count": 0, "data_types": {}, "agents": set()},
            )
            entry["count"] += 1
            if dt:
                entry["data_types"][dt] = entry["data_types"].get(dt, 0) + 1
            for ag in (agent_arr or []):
                if isinstance(ag, str) and ag.strip():
                    entry["agents"].add(ag)
    return bucket


# ---------------------------------------------------------------------------
# GET /api/taxonomy/tags
# ---------------------------------------------------------------------------
@router.get(
    "/tags",
    summary="태그 카탈로그 + 사용 빈도 + data_type 분포",
)
async def list_tags(
    q: str | None = Query(None, description="태그 prefix 검색 (대소문자 무시)"),
    min_count: int = Query(1, ge=1, description="최소 사용 빈도"),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """모든 태그 + 빈도/타입분포/관련 agent 를 반환한다.

    낮은 수준 AI 가 한 번 호출로 시스템 어휘를 모두 파악하도록 의도.
    """
    bucket = await _aggregate_tags(session)
    items: list[dict[str, Any]] = []
    q_norm = (q or "").strip().lower()
    for tag, entry in bucket.items():
        if entry["count"] < min_count:
            continue
        if q_norm and not tag.lower().startswith(q_norm):
            continue
        items.append(
            {
                "tag": tag,
                "count": entry["count"],
                "data_types": dict(entry["data_types"]),
                "agents": sorted(entry["agents"]),
            }
        )
    items.sort(key=lambda x: (-x["count"], x["tag"]))
    items = items[:limit]
    return {
        "total": len(items),
        "items": items,
        "filters": {"q": q, "min_count": min_count, "limit": limit},
    }


# ---------------------------------------------------------------------------
# GET /api/taxonomy/data-types
# ---------------------------------------------------------------------------
@router.get(
    "/data-types",
    summary="data_type 분포 + 추천 사용 패턴",
)
async def list_data_types(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(Record.data_type, func.count())
            .where(Record.deleted_at.is_(None))
            .group_by(Record.data_type)
        )
    ).all()
    counts: dict[str, int] = {str(k): int(v) for k, v in rows if k}
    items: list[dict[str, Any]] = []
    for dt in DATA_TYPES:
        items.append(
            {
                "data_type": dt,
                "count": int(counts.get(dt, 0)),
                "description": DATA_TYPE_USAGE_HINT.get(dt, ""),
                "subtypes": list(DATA_TYPE_SUBTYPES.get(dt, [])),
                "schema_url": f"/api/schema?data_type={dt}",
                "sample_query": f"/api/records?data_type={dt}",
            }
        )
    items.sort(key=lambda x: (-x["count"], x["data_type"]))
    return {"items": items, "total": len(items)}


# ---------------------------------------------------------------------------
# GET /api/taxonomy/domains
# ---------------------------------------------------------------------------
@router.get(
    "/domains",
    summary="domain 필드 분포",
)
async def list_domains(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """``Record.domain`` (CAE / lecture / test / simulation 등) 분포.

    null/empty 도메인은 ``__null__`` 키로 합쳐서 노출한다.
    """
    rows = (
        await session.execute(
            select(Record.domain, func.count())
            .where(Record.deleted_at.is_(None))
            .group_by(Record.domain)
        )
    ).all()
    items: list[dict[str, Any]] = []
    for k, v in rows:
        items.append(
            {
                "domain": k if (k is not None and k != "") else None,
                "count": int(v),
            }
        )
    items.sort(key=lambda x: (-x["count"], (x["domain"] or "")))
    return {"items": items, "total": len(items)}


# ---------------------------------------------------------------------------
# GET /api/taxonomy/agents
# ---------------------------------------------------------------------------
@router.get(
    "/agents",
    summary="agent 카탈로그 + record 수 + 주요 태그",
)
async def list_agents_taxonomy(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """기존 ``/api/agents`` 와 중복되지 않게 분포 통계 위주 응답."""
    agents = (await session.execute(select(Agent))).scalars().all()
    # 모든 record 를 한 번에 fetch (agents/tags/data_type 컬럼만).
    rec_rows = (
        await session.execute(
            select(Record.agents, Record.tags, Record.data_type).where(
                Record.deleted_at.is_(None)
            )
        )
    ).all()

    items: list[dict[str, Any]] = []
    for ag in agents:
        rec_count = 0
        tag_counter: dict[str, int] = {}
        dt_counter: dict[str, int] = {}
        for rec_agents, rec_tags, rec_dt in rec_rows:
            if rec_agents and ag.agent_type in list(rec_agents):
                rec_count += 1
                for t in (rec_tags or []):
                    if isinstance(t, str) and t.strip():
                        tag_counter[t] = tag_counter.get(t, 0) + 1
                if rec_dt:
                    dt_counter[rec_dt] = dt_counter.get(rec_dt, 0) + 1
        common_tags_top5 = [
            t for t, _ in sorted(
                tag_counter.items(), key=lambda x: (-x[1], x[0])
            )[:5]
        ]
        items.append(
            {
                "agent_type": ag.agent_type,
                "name": ag.name,
                "description": ag.description or "",
                "record_count": rec_count,
                "common_tags": common_tags_top5,
                "data_types": sorted(dt_counter.keys()),
            }
        )
    items.sort(key=lambda x: (-x["record_count"], x["agent_type"]))
    return {"items": items, "total": len(items)}


# ---------------------------------------------------------------------------
# GET /api/taxonomy/tags/resolve
# ---------------------------------------------------------------------------
@router.get(
    "/tags/resolve",
    summary="비공식 표현 → 정식 태그 매핑 (작은 모델 친화)",
)
async def resolve_tag(
    q: str = Query(..., min_length=1, description="검색 표현 (한/영, 약자 등)"),
    limit: int = Query(8, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """``q`` 를 정규화한 뒤 4단계 매칭으로 후보 태그를 반환한다.

    매칭:
        1. exact      — 정규화 후 정식 태그와 동일.
        2. synonym    — 동의어 사전 역방향 매칭.
        3. prefix     — DB 의 실제 태그 중 prefix 일치.
        4. substring  — 위에 잡히지 않은 잔여 substring 일치.
    """
    q_norm = _normalize(q)
    bucket = await _aggregate_tags(session)
    db_tags = list(bucket.keys())

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 1. exact (정규화 후 정식 태그와 일치)
    for tag in db_tags:
        if _normalize(tag) == q_norm and tag not in seen:
            candidates.append(
                {"tag": tag, "score": 1.0, "method": "exact",
                 "count": bucket[tag]["count"]}
            )
            seen.add(tag)

    # 2. synonym 사전 (역방향 인덱스)
    canonical = _REVERSE_SYNONYM.get(q_norm)
    if canonical and canonical not in seen:
        # DB 에 없어도 synonym 후보로 제시 (count=0).
        cnt = bucket.get(canonical, {"count": 0})["count"]
        candidates.append(
            {"tag": canonical, "score": 0.85, "method": "synonym",
             "count": cnt}
        )
        seen.add(canonical)

    # 동의어 그룹 동료 (동일 canonical 의 다른 variants 가 DB 에 있을 수 있음)
    if canonical:
        for variant in SYNONYM_DICT.get(canonical, []):
            v_norm = _normalize(variant)
            for tag in db_tags:
                if _normalize(tag) == v_norm and tag not in seen:
                    candidates.append(
                        {
                            "tag": tag,
                            "score": 0.8,
                            "method": "synonym",
                            "count": bucket[tag]["count"],
                        }
                    )
                    seen.add(tag)

    # 3. prefix
    for tag in db_tags:
        if tag in seen:
            continue
        if tag.lower().startswith(q_norm):
            candidates.append(
                {
                    "tag": tag,
                    "score": 0.7,
                    "method": "prefix",
                    "count": bucket[tag]["count"],
                }
            )
            seen.add(tag)

    # 4. substring
    if q_norm:
        for tag in db_tags:
            if tag in seen:
                continue
            if q_norm in tag.lower():
                candidates.append(
                    {
                        "tag": tag,
                        "score": 0.55,
                        "method": "substring",
                        "count": bucket[tag]["count"],
                    }
                )
                seen.add(tag)

    candidates.sort(
        key=lambda x: (-x["score"], -int(x.get("count", 0)), x["tag"])
    )
    candidates = candidates[:limit]
    return {
        "query": q,
        "normalized": q_norm,
        "candidates": candidates,
        "total": len(candidates),
    }


# ---------------------------------------------------------------------------
# GET /api/taxonomy/classification | /status | /access-pattern
# ---------------------------------------------------------------------------
async def _enum_distribution(
    session: AsyncSession,
    column: Any,
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(column, func.count())
            .where(Record.deleted_at.is_(None))
            .group_by(column)
        )
    ).all()
    return {str(k): int(v) for k, v in rows if k is not None}


def _enum_response(
    field: str,
    values: tuple[str, ...],
    desc_map: dict[str, str],
    counts: dict[str, int],
) -> dict[str, Any]:
    items = [
        {
            "value": v,
            "description": desc_map.get(v, ""),
            "count": int(counts.get(v, 0)),
        }
        for v in values
    ]
    return {"field": field, "items": items, "total": len(items)}


@router.get(
    "/classification",
    summary="classification enum + 의미 + 분포",
)
async def list_classification(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    counts = await _enum_distribution(session, Record.classification)
    return _enum_response(
        "classification", CLASSIFICATIONS, CLASSIFICATION_DESC, counts
    )


@router.get(
    "/status",
    summary="status enum + 의미 + 분포",
)
async def list_status(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    counts = await _enum_distribution(session, Record.status)
    return _enum_response("status", STATUSES, STATUS_DESC, counts)


@router.get(
    "/access-pattern",
    summary="access_pattern enum + 의미 + 분포",
)
async def list_access_pattern(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    counts = await _enum_distribution(session, Record.access_pattern)
    return _enum_response(
        "access_pattern", ACCESS_PATTERNS, ACCESS_PATTERN_DESC, counts
    )


__all__ = ["router", "SYNONYM_DICT"]
