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
import re
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
from .capabilities import compute_capabilities

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 자동 언어 감지 (META_FORMAT_AUDIT A-4 / P1-2)
# ---------------------------------------------------------------------------
# 한글: AC00-D7AF (한글 음절) + 1100-11FF (자모) + 3130-318F (호환 자모).
_KO_RE = re.compile(r"[가-힯ᄀ-ᇿ㄰-㆏]")
# 일본어 가나: 3040-309F (히라가나) + 30A0-30FF (가타카나).
_JA_KANA_RE = re.compile(r"[぀-ゟ゠-ヿ]")
# CJK 통합 한자: 4E00-9FFF.
_ZH_RE = re.compile(r"[一-鿿]")
# 라틴 문자.
_EN_RE = re.compile(r"[A-Za-z]")


def _detect_language_from_content(content: dict[str, Any]) -> str | None:
    """``content`` 본문 텍스트의 문자종 분포로 언어를 추정한다.

    Returns:
        "ko" / "en" / "ja" / "zh" / "mixed" 또는 신호 부족 시 ``None``.
    """
    if not isinstance(content, dict):
        return None
    sections = content.get("sections") or []
    if not isinstance(sections, list):
        return None

    texts: list[str] = []

    def _walk(nodes: Any) -> None:
        if not isinstance(nodes, list):
            return
        for n in nodes:
            if not isinstance(n, dict):
                continue
            blocks = n.get("blocks") or []
            if isinstance(blocks, list):
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    btype = b.get("type")
                    if btype in {"paragraph", "list_item", "quote", "code"}:
                        t = b.get("text") or ""
                        if isinstance(t, str) and t:
                            texts.append(t)
            _walk(n.get("children") or [])

    _walk(sections)
    body = " ".join(texts)[:5000]
    if len(body) < 50:
        return None

    ko = len(_KO_RE.findall(body))
    ja = len(_JA_KANA_RE.findall(body))
    zh = len(_ZH_RE.findall(body))
    en = len(_EN_RE.findall(body))
    total = ko + ja + zh + en
    if total < 30:
        return None

    ko_pct = ko / total
    ja_pct = ja / total
    zh_pct = zh / total
    en_pct = en / total

    # 일본어 가나가 5% 이상 + 한자 존재 → 중국어로 오분류 방지.
    if ja > 0 and ja_pct >= 0.05 and zh > 0:
        # 가나 + 한자(간지) 합산 비율로 일본어 판정.
        jp_combined_pct = (ja + zh) / total
        if jp_combined_pct >= 0.60:
            return "ja"

    if ko_pct >= 0.60:
        return "ko"
    if ja_pct >= 0.60:
        return "ja"
    if zh_pct >= 0.60 and ja == 0:
        return "zh"
    if en_pct >= 0.60:
        return "en"

    # 혼합 판정: 상위 두 클래스가 각각 >= 20% 인지.
    pcts = sorted([ko_pct, ja_pct, zh_pct, en_pct], reverse=True)
    if pcts[0] >= 0.20 and pcts[1] >= 0.20:
        return "mixed"

    return None


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
def _union(*lists: Any) -> list[str]:
    """여러 리스트를 순서 보존 + 중복 제거로 합친다 (앞 인자 우선).

    meta(본문 추출) 와 raw(사용자 입력) 의 tags/agents 를 둘 다 살리기 위함.
    """
    out: list[str] = []
    seen: set[str] = set()
    for lst in lists:
        for v in lst or []:
            s = str(v)
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def _extract_doc(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """DOC variant: 공통 메타와 ``content`` 를 추출."""
    meta = raw.get("meta") or {}
    if not isinstance(meta, dict):
        raise ValueError("DOC.meta must be an object")

    common = {
        "id": raw.get("id") or meta.get("doc_id") or meta.get("id"),
        # Soft taxonomy (Migration 0011) — 변환기는 ``meta.doc_type`` 으로 출력.
        "doc_type": meta.get("doc_type") or raw.get("doc_type"),
        "title": meta.get("title", "") or raw.get("title", ""),
        "summary": meta.get("summary", "") or raw.get("summary", ""),
        # meta(컨버터 본문 추출) + raw(사용자 입력) 를 union 한다. 과거에는
        # ``meta or raw`` 단락이라 본문 추출 태그가 있으면 사용자가 폼에서
        # 넘긴 tags/agents 가 통째로 버려져 bind-matching 이 핵심 문서를
        # 놓치는 문제가 있었다 (실데이터 적재로 발견).
        "tags": _union(meta.get("tags"), raw.get("tags")),
        "agents": _union(meta.get("agent_scope"), raw.get("agents")),
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
        # language: 작성자 명시 여부를 보존하기 위해 기본값 "ko" 를 적용하지 않는다.
        # normalize() 에서 None 이면 본문 자동 감지 → 그래도 없으면 "ko".
        "language": meta.get("language") or raw.get("language"),
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
    """비-DOC 변종에서 공용 메타 필드를 끌어온다.

    ``id`` 폴백 순서: ``raw.id`` → ``raw.data_id`` (Excel DATA 변종) →
    ``meta.doc_id`` → ``meta.id``. Excel 변환기는 top-level ``data_id`` 만
    출력하므로 이 폴백이 없으면 ingest 시 "id is required" 로 거부된다
    (json_schema_rules §11.2 의 흡수 경로 명세와 일치).
    """
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    return {
        "id": (
            raw.get("id")
            or raw.get("data_id")
            or meta.get("doc_id")
            or meta.get("id")
        ),
        # Soft taxonomy (Migration 0011) — raw 우선, 없으면 meta.
        "doc_type": raw.get("doc_type") or meta.get("doc_type"),
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
        # language: 작성자 명시 여부를 보존하기 위해 기본값 "ko" 를 적용하지 않는다.
        # normalize() 에서 None 이면 본문 자동 감지 → 그래도 없으면 "ko".
        "language": raw.get("language") or meta.get("language"),
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

    # 자동 언어 감지 (META_FORMAT_AUDIT A-4 / P1-2):
    # 작성자가 language 를 명시하지 않은 경우(common["language"] is None)에 한해
    # 본문 텍스트의 문자종 분포로 ko/en/ja/zh/mixed 를 추정한다. 감지 실패 시
    # 스키마 기본값 "ko" 로 폴백 (record 생성 시 ``or "ko"`` 가 처리).
    if not common.get("language"):
        detected = _detect_language_from_content(content)
        if detected:
            common["language"] = detected

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
        doc_type=common.get("doc_type"),
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
    # capabilities 자동 산출 (json_schema_rules §13). RecordIn 구성 직후
    # 구조 신호를 검사해 라벨 리스트를 채운다 — embeddings 는 임베딩 잡이 갱신.
    caps = compute_capabilities(record)
    if caps:
        record = record.model_copy(update={"capabilities": caps})
    return record


__all__ = [
    "ID_PATTERN",
    "LEGACY_ID_PATTERN",
    "canonical_json",
    "compute_content_hash",
    "detect_variant",
    "normalize",
]
