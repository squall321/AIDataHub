"""변종 JSON → 통합 ``RecordIn`` 정규화.

입력 형태:
    1. 정규화된 형태 (``id``/``data_type``/``content`` 키 포함) — 그대로 사용.
    2. DOC 변종 raw — Word→JSON 변환 산출물 (``schema_version``/``meta``/``sections``).
    3. DATA 변종 raw — ``headers`` + ``rows``.
    4. SIM 변종 raw — ``solver`` + ``inputs``.
    5. CAD 변종 raw — ``cad_type`` + ``file_format``.
    6. 그 외 — ``OTHER`` 로 처리하고 raw 전체를 ``content`` 에 보존.

출력:
    ``api.schemas.RecordIn`` 인스턴스 (id 검증 + 변종별 ``content`` 검증 통과).
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from ..schemas import (
    CADContent,
    DataContent,
    DocumentContent,
    RecordIn,
    SimContent,
)
from ..schemas.id_format import (
    ID_PATTERN,
    LEGACY_ID_PATTERN,
    is_legacy_id,
    normalize_id,
    parse_id,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------
def canonical_json(obj: Any) -> str:
    """결정적 JSON 문자열 (키 정렬·콤팩트 구분자·NaN 거부).

    동일 입력은 항상 동일 문자열을 반환하므로 해시 계산에 적합하다.
    """
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def compute_content_hash(content: dict[str, Any]) -> str:
    """``content`` dict 의 SHA-256 해시 (hex digest)."""
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


def detect_variant(raw: dict[str, Any]) -> str:
    """raw JSON 의 형태로 변종을 추정해 ``DataType`` 문자열을 반환한다."""
    if not isinstance(raw, dict):
        raise TypeError("raw must be a dict")

    # 입력에 명시적 data_type 이 있으면 우선
    explicit = raw.get("data_type")
    if isinstance(explicit, str) and explicit:
        return explicit.upper()

    # DOC: Word→JSON 변환 산출물
    if (
        "schema_version" in raw
        and "meta" in raw
        and "sections" in raw
    ):
        return "DOC"

    # DATA: 표 형태
    if "headers" in raw and "rows" in raw:
        return "DATA"

    # SIM: 시뮬레이션
    if "solver" in raw and "inputs" in raw:
        return "SIM"

    # CAD: CAD 메타데이터
    if "cad_type" in raw:
        return "CAD"

    return "OTHER"


# ---------------------------------------------------------------------------
# 변종별 추출/검증
# ---------------------------------------------------------------------------
def _extract_doc(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """DOC variant: 공통 메타와 ``content`` 를 추출."""
    meta = raw.get("meta") or {}
    if not isinstance(meta, dict):
        raise ValueError("DOC.meta must be an object")

    common = {
        "id": raw.get("id") or meta.get("doc_id") or meta.get("id"),
        "title": meta.get("title", "") or raw.get("title", ""),
        "summary": meta.get("summary", "") or raw.get("summary", ""),
        "tags": list(meta.get("tags") or raw.get("tags") or []),
        "agents": list(meta.get("agent_scope") or raw.get("agents") or []),
        "schema_version": str(raw.get("schema_version", "1.0")),
        "source_file": meta.get("source_file") or raw.get("source_file"),
        "author": meta.get("author", "") or raw.get("author", ""),
        "department": meta.get("department", "") or raw.get("department", ""),
        "project": meta.get("project") or raw.get("project"),
        "version": str(meta.get("version", "1.0") or "1.0"),
        # Agent discovery hints (Migration 0007) — meta 우선, 없으면 raw.
        "agent_hints": meta.get("agent_hints") or raw.get("agent_hints"),
        "related_record_ids": list(
            meta.get("related_record_ids")
            or raw.get("related_record_ids")
            or []
        ),
        "query_examples": list(
            meta.get("query_examples") or raw.get("query_examples") or []
        ),
        "access_pattern": (
            meta.get("access_pattern")
            or raw.get("access_pattern")
            or "occasional"
        ),
        # Extended classification metadata (Migration 0006) — meta 우선, 없으면 raw.
        "classification": meta.get("classification") or raw.get("classification"),
        "status": meta.get("status") or raw.get("status"),
        "domain": meta.get("domain") or raw.get("domain"),
        "subject_keywords": list(
            meta.get("subject_keywords") or raw.get("subject_keywords") or []
        ),
        "source_system": meta.get("source_system") or raw.get("source_system"),
        "language": meta.get("language") or raw.get("language") or "ko",
        "parent_record_id": (
            meta.get("parent_record_id") or raw.get("parent_record_id")
        ),
        "derivation": meta.get("derivation") or raw.get("derivation"),
        # quality_score 는 0 이 유효값이므로 명시적 None 검사.
        "quality_score": (
            meta.get("quality_score")
            if meta.get("quality_score") is not None
            else raw.get("quality_score")
        ),
        "valid_from": meta.get("valid_from") or raw.get("valid_from"),
        "valid_until": meta.get("valid_until") or raw.get("valid_until"),
    }

    # content 는 DOC 본문(meta/toc/sections/figures/tables/sources) 자체.
    doc_content = {
        "schema_version": str(raw.get("schema_version", "1.0")),
        "meta": meta,
        "toc": list(raw.get("toc") or []),
        "sections": list(raw.get("sections") or []),
        "figures": list(raw.get("figures") or []),
        "tables": list(raw.get("tables") or []),
        "sources": list(raw.get("sources") or []),
    }
    # Pydantic 검증
    DocumentContent.model_validate(doc_content)
    return common, doc_content


def _extract_data(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """DATA variant."""
    inner = raw.get("content") if isinstance(raw.get("content"), dict) else raw
    data_content = {
        "caption": inner.get("caption", "") or raw.get("title", ""),
        "headers": list(inner.get("headers") or []),
        "rows": list(inner.get("rows") or []),
        "units": inner.get("units"),
        "notes": inner.get("notes", ""),
    }
    DataContent.model_validate(data_content)

    common = _common_fields(raw, default_title=data_content["caption"] or "DATA")
    return common, data_content


def _extract_sim(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """SIM variant."""
    inner = raw.get("content") if isinstance(raw.get("content"), dict) else raw
    sim_content = {
        "solver": inner.get("solver", ""),
        "solver_version": inner.get("solver_version"),
        "inputs": inner.get("inputs") or {},
        "outputs": inner.get("outputs") or {},
        "runtime": inner.get("runtime"),
    }
    SimContent.model_validate(sim_content)

    common = _common_fields(raw, default_title=f"SIM:{sim_content['solver']}")
    return common, sim_content


def _extract_cad(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """CAD variant."""
    inner = raw.get("content") if isinstance(raw.get("content"), dict) else raw
    cad_content = {
        "cad_type": inner.get("cad_type", ""),
        "file_format": inner.get("file_format", ""),
        "file_metadata": inner.get("file_metadata") or {},
        "components": inner.get("components") or [],
    }
    CADContent.model_validate(cad_content)

    common = _common_fields(
        raw,
        default_title=f"CAD:{cad_content['cad_type']}/{cad_content['file_format']}",
    )
    return common, cad_content


def _extract_other(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """알 수 없는 형태 — raw 전체를 ``content`` 로 보존."""
    common = _common_fields(raw, default_title=str(raw.get("id") or "OTHER"))
    # raw 자체를 content 로 사용하되, 직접 변경하지 않도록 얕은 복사.
    content = dict(raw)
    return common, content


def _common_fields(raw: dict[str, Any], default_title: str = "") -> dict[str, Any]:
    """비-DOC 변종에서 공용 메타 필드를 끌어온다."""
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    return {
        "id": raw.get("id"),
        "title": raw.get("title") or default_title,
        "summary": raw.get("summary", ""),
        "tags": list(raw.get("tags") or []),
        "agents": list(raw.get("agents") or raw.get("agent_scope") or []),
        "schema_version": str(raw.get("schema_version", "1.0")),
        "source_file": raw.get("source_file"),
        "author": raw.get("author", ""),
        "department": raw.get("department", ""),
        "project": raw.get("project"),
        "version": str(raw.get("version", "1.0") or "1.0"),
        # Agent discovery hints (Migration 0007).
        "agent_hints": raw.get("agent_hints") or meta.get("agent_hints"),
        "related_record_ids": list(
            raw.get("related_record_ids")
            or meta.get("related_record_ids")
            or []
        ),
        "query_examples": list(
            raw.get("query_examples") or meta.get("query_examples") or []
        ),
        "access_pattern": (
            raw.get("access_pattern")
            or meta.get("access_pattern")
            or "occasional"
        ),
        # Extended classification metadata (Migration 0006) — raw 우선, 없으면 meta.
        "classification": raw.get("classification") or meta.get("classification"),
        "status": raw.get("status") or meta.get("status"),
        "domain": raw.get("domain") or meta.get("domain"),
        "subject_keywords": list(
            raw.get("subject_keywords") or meta.get("subject_keywords") or []
        ),
        "source_system": raw.get("source_system") or meta.get("source_system"),
        "language": raw.get("language") or meta.get("language") or "ko",
        "parent_record_id": (
            raw.get("parent_record_id") or meta.get("parent_record_id")
        ),
        "derivation": raw.get("derivation") or meta.get("derivation"),
        # quality_score 는 0 이 유효값이므로 명시적 None 검사.
        "quality_score": (
            raw.get("quality_score")
            if raw.get("quality_score") is not None
            else meta.get("quality_score")
        ),
        "valid_from": raw.get("valid_from") or meta.get("valid_from"),
        "valid_until": raw.get("valid_until") or meta.get("valid_until"),
    }


# ---------------------------------------------------------------------------
# 메인 진입점
# ---------------------------------------------------------------------------
def normalize(raw: dict[str, Any]) -> RecordIn:
    """raw JSON dict 를 ``RecordIn`` 으로 변환·검증한다.

    동작:
        - 변종 자동 감지 → 변종별 ``content`` 추출/검증.
        - ID 정규화: 레거시 ID 면 변종 접두사를 붙인다 (예: ``HE-CAE-…`` → ``DOC-HE-CAE-…``).
        - ``id`` 누락 시 ``ValueError``.
    """
    if not isinstance(raw, dict):
        raise TypeError(f"normalize() expects dict, got {type(raw).__name__}")

    variant = detect_variant(raw)

    if variant == "DOC":
        common, content = _extract_doc(raw)
    elif variant == "DATA":
        common, content = _extract_data(raw)
    elif variant == "SIM":
        common, content = _extract_sim(raw)
    elif variant == "CAD":
        common, content = _extract_cad(raw)
    else:
        # LOG / FORM / OTHER / 미지정
        common, content = _extract_other(raw)
        if variant not in ("LOG", "FORM", "OTHER"):
            variant = "OTHER"

    rid = common.get("id")
    if not rid:
        raise ValueError("id is required (input has no 'id' field nor 'meta.doc_id')")

    # 레거시 ID 면 detected variant 를 기본 data_type 으로 사용.
    if is_legacy_id(rid):
        logger.warning(
            "Legacy id %r detected — prefixing with %r", rid, variant
        )
        rid = normalize_id(rid, default_data_type=variant)
    else:
        # 정식 ID 라도 한 번 검증.
        parsed = parse_id(rid)
        # ID 안의 data_type 과 detected variant 가 충돌하면 ID 우선 (사용자 명시).
        variant = parsed["data_type"]

    record = RecordIn(
        id=rid,
        data_type=variant,
        title=common.get("title") or rid,
        summary=common.get("summary", ""),
        tags=common.get("tags", []),
        agents=common.get("agents", []),
        schema_version=common.get("schema_version", "1.0"),
        content=content,
        source_file=common.get("source_file"),
        author=common.get("author", ""),
        department=common.get("department", ""),
        project=common.get("project"),
        version=common.get("version", "1.0"),
        # Agent discovery hints (Migration 0007). RecordIn 의 기본값이 있으므로
        # 빈 값이면 그쪽이 사용된다.
        agent_hints=common.get("agent_hints"),
        related_record_ids=common.get("related_record_ids", []),
        query_examples=common.get("query_examples", []),
        access_pattern=common.get("access_pattern", "occasional"),
        # Extended classification metadata (Migration 0006). 누락된 값은 모델
        # 기본값(internal/draft/ko/original) 또는 None 으로 흘려보낸다.
        classification=common.get("classification") or "internal",
        status=common.get("status") or "draft",
        domain=common.get("domain"),
        subject_keywords=common.get("subject_keywords") or [],
        source_system=common.get("source_system"),
        language=common.get("language") or "ko",
        parent_record_id=common.get("parent_record_id"),
        derivation=common.get("derivation") or "original",
        quality_score=common.get("quality_score"),
        valid_from=common.get("valid_from"),
        valid_until=common.get("valid_until"),
    )
    return record


__all__ = [
    "ID_PATTERN",
    "LEGACY_ID_PATTERN",
    "canonical_json",
    "compute_content_hash",
    "detect_variant",
    "normalize",
]
