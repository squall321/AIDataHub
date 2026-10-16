# 임베딩 슬롯 둘(v1 · m3) — 읽기는 활성 슬롯, 백필은 채움 목록을 따른다
"""임베딩 슬롯.

슬롯은 '임베더 하나 + 그 임베더가 채우는 열 묶음' 이다. 임베더를 바꿀 때 열을 비우고 다시 채우면(예전
alembic 0013 의 384→768) 그동안 의미검색이 빈다. 그래서 새 열을 나란히 두고 채운 뒤 설정 한 줄로 읽기를
옮긴다 — 되돌리기도 그 한 줄이다(HWAXPortal ``docs/embedder-bge-m3``).

- ``v1`` — 지금의 열(``record_sections.embedding`` · ``records.signature_embedding`` ·
  ``agent_sample_embeddings.embedding`` · ``mcp_uploads.description_embedding``)과 ``EMBEDDING_PROVIDER`` ·
  ``EMBEDDING_DIM``(dev · cae00 은 e5_base 768).
- ``m3`` — 새 열 ``*_m3 halfvec(1024)`` 과 그 기록 열(``*_m3_at`` · ``*_m3_model``), 공급자 ``bge_m3``.

설정(바꾸면 API 재기동)
- ``AIDH_EMBED_ACTIVE=v1|m3``(기본 v1) — 읽기(의미검색 · 하이브리드 · agent_search · 유사도 · 추천 · 도구 검색 ·
  ``/api/embed``)와 그 자리에서 곧바로 쓰는 임베딩(업로드 절 · 시그니처 · 에이전트 표본 · 도구 설명)이 이 슬롯이다.
- ``AIDH_EMBED_FILL=v1[,m3]``(기본 v1) — 백필(인앱 스윕 · ``POST /api/jobs/embed`` · CLI)이 채워 두는 슬롯.
  **활성 슬롯은 적지 않아도 늘 채운다** — 활성을 m3 로 옮기는 한 줄만으로 새 행이 m3 에도 찬다(v1 은 FILL 에
  남아 있으면 계속 채워져 되돌리기가 된다).

모르는 값은 조용히 v1 로 떨어지지 않고 ``SlotConfigError`` 다 — 부팅(``validate_settings``)에서 막는다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

SLOT_NAMES = ("v1", "m3")

# 표 이름 — 백필 · 잠금 · 상태 보고가 같은 이름을 쓴다.
TABLES = ("records", "agent_sample_embeddings", "mcp_uploads", "record_sections")


class SlotConfigError(RuntimeError):
    """AIDH_EMBED_ACTIVE · AIDH_EMBED_FILL 이 잘못됐다 — 조용히 넘어가지 않는다."""


@dataclass(frozen=True)
class SlotColumns:
    """한 표에서 그 슬롯이 쓰는 열 — 벡터, 채운 시각, 채운 모델. 시각 · 모델 열이 없는 표(v1 의 세 표)는 None."""

    vec: str
    at: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class Slot:
    name: str
    provider: str
    dim: int
    halfvec: bool
    columns: dict[str, SlotColumns] = field(default_factory=dict)

    def cols(self, table: str) -> SlotColumns:
        return self.columns[table]

    def vec_attr(self, model: Any):
        """ORM 모델의 그 슬롯 벡터 열(InstrumentedAttribute)."""
        return getattr(model, self.columns[model.__tablename__].vec)


_V1_COLUMNS = {
    "record_sections": SlotColumns("embedding", "embedded_at", "embedding_model"),
    "records": SlotColumns("signature_embedding"),
    "agent_sample_embeddings": SlotColumns("embedding"),
    "mcp_uploads": SlotColumns("description_embedding"),
}

_M3_COLUMNS = {
    "record_sections": SlotColumns("embedding_m3", "embedding_m3_at", "embedding_m3_model"),
    "records": SlotColumns(
        "signature_embedding_m3", "signature_embedding_m3_at", "signature_embedding_m3_model"
    ),
    "agent_sample_embeddings": SlotColumns("embedding_m3", "embedding_m3_at", "embedding_m3_model"),
    "mcp_uploads": SlotColumns(
        "description_embedding_m3", "description_embedding_m3_at", "description_embedding_m3_model"
    ),
}

M3_DIM = 1024


def slot(name: str) -> Slot:
    """이름으로 슬롯을 만든다. v1 의 공급자 · 차원은 부를 때의 환경(EMBEDDING_PROVIDER · EMBEDDING_DIM)이다."""
    if name == "v1":
        return Slot(
            name="v1",
            provider=(os.environ.get("EMBEDDING_PROVIDER") or "hash").strip().lower(),
            dim=int(os.environ.get("EMBEDDING_DIM", "384")),
            halfvec=False,
            columns=_V1_COLUMNS,
        )
    if name == "m3":
        return Slot(name="m3", provider="bge_m3", dim=M3_DIM, halfvec=True, columns=_M3_COLUMNS)
    raise SlotConfigError(f"임베딩 슬롯 {name!r} 를 모른다 — {' · '.join(SLOT_NAMES)} 중 하나")


def _parse_name(knob: str, raw: str) -> str:
    v = raw.strip().lower()
    if v not in SLOT_NAMES:
        raise SlotConfigError(f"{knob}={raw!r} 의 {v!r} 를 모른다 — {' · '.join(SLOT_NAMES)} 중에서 고른다")
    return v


def active_name() -> str:
    return _parse_name("AIDH_EMBED_ACTIVE", os.environ.get("AIDH_EMBED_ACTIVE") or "v1")


def fill_names() -> list[str]:
    """채울 슬롯 — AIDH_EMBED_FILL 에 활성 슬롯을 더한 것(v1 · m3 순서)."""
    raw = os.environ.get("AIDH_EMBED_FILL")
    if raw is None or not raw.strip():
        raw = "v1"
    names = {_parse_name("AIDH_EMBED_FILL", part) for part in raw.split(",") if part.strip()}
    if not names:
        raise SlotConfigError(f"AIDH_EMBED_FILL={raw!r} 에 슬롯이 없다")
    names.add(active_name())
    return [n for n in SLOT_NAMES if n in names]


def active() -> Slot:
    return slot(active_name())


def fill() -> list[Slot]:
    return [slot(n) for n in fill_names()]


def embedder(s: Slot):
    """그 슬롯의 임베더. v1 은 ``embedding.get_embedder()`` 를 그대로 부른다(시험이 바꿔 끼우는 자리)."""
    from . import embedding

    if s.name == "v1":
        return embedding.get_embedder()
    return embedding.get_embedder_for(s.provider)


def model_id(s: Slot) -> str:
    """그 슬롯 모델의 정식 이름 — 모델을 올리지 않고 안다(/api/embed 의 빈 요청 · 상태 보고)."""
    from . import embedding

    if s.provider in embedding.PROVIDER_MODEL_IDS:
        return embedding.PROVIDER_MODEL_IDS[s.provider]
    if s.provider in ("sentence_transformers", "st", "sbert"):
        return os.environ.get("SENTENCE_TRANSFORMER_MODEL") or "intfloat/multilingual-e5-small"
    return s.provider


def query_param(s: Slot, vec):
    """질의 벡터를 SQL 에 넘길 꼴. halfvec 열은 ``CAST(:q AS HALFVEC(1024))`` 로 형을 박아 넘긴다 —
    연산자 ``<=>`` 와 HNSW(halfvec_cosine_ops)가 오른쪽도 halfvec 이길 바란다. v1 은 예전 그대로(리스트)."""
    if not s.halfvec:
        return vec
    from sqlalchemy import cast, literal

    from ..db.models import HalfVecType

    t = HalfVecType(s.dim)
    return cast(literal(list(vec), type_=t), t)


# ---------------------------------------------------------------------------
# 슬롯마다 다른 절대 점수 문턱 — 코사인 분포가 모델마다 다르다(e5 는 무관한 글도 0.7~0.9).
# ---------------------------------------------------------------------------
# 날 코사인(정규화 벡터의 내적)에 거는 문턱만 여기 둔다. 검색 점수((1+cos)/2)에 거는 에이전트별 score_threshold
# (dev 0.3 774개 · 0.5 1개) · 미리보기 · 군집 min_score(0.4)는 코사인 −0.4 · 0 · −0.2 에 해당하는데, 같은 표본에서
# 질의–절 코사인의 최솟값이 e5 0.65 · BGE-M3 0.11 이라 두 모델 모두 하나도 거르지 않는다 — 그대로 둔다.
# m3 값은 e5 문턱이 가르는 몫(분위)을 BGE-M3 분포에서 같은 몫이 되는 값으로 옮긴 것이다(결정의 비율을 지킨다).
# 잰 표본 — dev 의 절 8,905개(평가셋 정답 카드 236장 + 무작위 0.6%) · 레코드 시그니처 6,029개 · 질의 397개,
# 2026-10-16. 잰 방법과 수는 docs/embedder-bge-m3.md, dev 전체로 다시 재는 법은 scripts/embed_calibrate.py.
THRESHOLDS: dict[str, dict[str, float]] = {
    "v1": {
        # similarity_svc — 시그니처 최근접 이웃의 확신(high · medium)
        "signature_high": 0.93,
        "signature_medium": 0.88,
        # cluster_svc.build_query_groups — 한 질의의 히트 절끼리 같은 그룹으로 묶는 코사인
        "cluster_sim": 0.85,
        # cluster_svc.cluster_around_record — 레코드 첫 절끼리 같은 의미 그룹으로 보는 코사인
        "cluster_record_sim": 0.85,
    },
    "m3": {
        "signature_high": 0.74,       # e5 0.93 이상 29.6% 와 같은 몫
        "signature_medium": 0.55,     # e5 0.88 이상 96.9% 와 같은 몫
        "cluster_sim": 0.55,          # 히트 절 쌍 — e5 0.85 이상 18.3%
        "cluster_record_sim": 0.58,   # 레코드 첫 절 쌍 — e5 0.85 이상 1.74%
    },
}


def threshold(s: Slot, key: str) -> float:
    return THRESHOLDS[s.name][key]


# ---------------------------------------------------------------------------
# 부팅 검증
# ---------------------------------------------------------------------------
def validate_settings() -> dict[str, Any]:
    """설정만 본다(DB · 모델 없이) — 모르는 슬롯 이름이면 SlotConfigError. 부팅 첫 줄에서 부른다."""
    a = active_name()
    f = fill_names()
    return {"active": a, "fill": f}


def model_files_present(repo_id: str) -> bool:
    """모델 파일이 이 박스에 있나 — 내려받지 않고 본다(/opt/models 의 폴더 또는 HF 캐시)."""
    from . import embedding

    local = embedding._local_or_repo(repo_id)
    if local != repo_id:
        return True
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:  # pragma: no cover — sentence-transformers 가 있으면 함께 있다
        return False
    hit = try_to_load_from_cache(repo_id, "config.json")
    return isinstance(hit, str)


async def check_columns(conn) -> list[str]:
    """채울 · 읽을 슬롯의 열이 DB 에 있고 차원 · 형이 맞는지 — 틀린 것을 문장으로 돌려준다(PG 만, SQLite 는 [])."""
    if conn.dialect.name != "postgresql":
        return []
    from sqlalchemy import text

    rows = (
        await conn.execute(
            text(
                "SELECT c.relname AS table_name, a.attname AS column_name, t.typname AS type_name, "
                "a.atttypmod AS typmod FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = current_schema() "
                "JOIN pg_type t ON t.oid = a.atttypid "
                "WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relname = ANY(:tables)"
            ),
            {"tables": list(TABLES)},
        )
    ).all()
    have = {(r.table_name, r.column_name): (r.type_name, int(r.typmod)) for r in rows}
    problems: list[str] = []
    for name in set(fill_names()) | {active_name()}:
        s = slot(name)
        if s.name == "v1":
            continue  # v1 의 차원 어긋남은 예전처럼 경고만 한다(main._check_embedding_dim_consistency)
        want_type = "halfvec" if s.halfvec else "vector"
        for table in TABLES:
            col = s.cols(table).vec
            got = have.get((table, col))
            if got is None:
                problems.append(f"{table}.{col} 이 없다 — alembic upgrade head 를 먼저 돌린다")
                continue
            typ, dim = got
            if typ != want_type or dim != s.dim:
                problems.append(f"{table}.{col} 이 {typ}({dim}) 다 — 슬롯 {s.name} 은 {want_type}({s.dim})")
    return problems


__all__ = [
    "SLOT_NAMES",
    "TABLES",
    "Slot",
    "SlotColumns",
    "SlotConfigError",
    "active",
    "active_name",
    "check_columns",
    "embedder",
    "fill",
    "fill_names",
    "model_files_present",
    "model_id",
    "query_param",
    "slot",
    "threshold",
    "validate_settings",
]
