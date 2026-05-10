"""``records.capabilities`` 자동 산출 (json_schema_rules §13).

``RecordIn.content`` 의 구조적 신호를 검사해 표준 라벨 집합을 반환한다.
``CAPABILITY_LABELS`` 에 정의되지 않은 라벨은 결과에 포함되지 않는다.

주의:
    ``"embeddings"`` 라벨은 본 함수가 부여하지 않는다 — 임베딩 잡(embedding job)
    이 인덱싱 후 별도로 갱신한다.
"""
from __future__ import annotations

from typing import Any

from ..schemas import RecordIn
from ..schemas.common import CAPABILITY_LABELS


def _non_empty(value: Any) -> bool:
    """list/dict/str 의 비어있지 않음 여부."""
    if value is None:
        return False
    if isinstance(value, (list, dict, str, tuple, set)):
        return len(value) > 0
    return bool(value)


def compute_capabilities(record_in: RecordIn) -> list[str]:
    """``RecordIn`` 의 content 를 검사해 capabilities 라벨 리스트를 만든다.

    반환 순서는 ``CAPABILITY_LABELS`` 의 정의 순서를 따른다 — 결정성을 위해.
    """
    content = record_in.content or {}
    labels: set[str] = set()

    # ---- 공통 구조적 신호 (모든 변종) -----------------------------------
    if _non_empty(content.get("sections")):
        labels.add("sections")
        # 섹션 안의 blocks 중 하나라도 비어있지 않으면 "blocks" 부여.
        for sec in content.get("sections") or []:
            if isinstance(sec, dict) and _non_empty(sec.get("blocks")):
                labels.add("blocks")
                break

    if _non_empty(content.get("tables")):
        labels.add("tables")
    if _non_empty(content.get("figures")):
        labels.add("figures")
    if _non_empty(content.get("attachments")):
        labels.add("attachments")

    # ---- 변종별 신호 ----------------------------------------------------
    dt = record_in.data_type
    if dt == "DATA":
        if _non_empty(content.get("headers")):
            labels.add("headers")
        if _non_empty(content.get("rows")):
            labels.add("rows")
    elif dt == "SIM":
        if _non_empty(content.get("inputs")):
            labels.add("inputs")
        if _non_empty(content.get("outputs")):
            labels.add("outputs")
    elif dt == "CAD":
        if _non_empty(content.get("components")):
            labels.add("components")
        if _non_empty(content.get("file_metadata")):
            labels.add("files")
    # LOG/FORM/OTHER/DOC: 위 공통 신호만 사용.

    # NOTE: "embeddings" 는 임베딩 잡이 별도 갱신하므로 여기선 부여하지 않는다.
    # NOTE: "samples" 는 현재 어떤 변종에서도 자동 추론하지 않는다.

    valid = set(CAPABILITY_LABELS)
    return [lbl for lbl in CAPABILITY_LABELS if lbl in labels and lbl in valid]


__all__ = ["compute_capabilities"]
