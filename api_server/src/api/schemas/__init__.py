"""Pydantic 스키마 패키지.

- ``common``: 공용 ``RecordIn`` / ``RecordOut`` / ``DataType``
- ``id_format``: 레코드 ID 파싱·검증 유틸
- ``document``: DOC 변종 콘텐츠 스키마 (Word→JSON)
- ``data``: DATA 변종 콘텐츠 스키마 (표 형태)
- ``sim``: SIM 변종 콘텐츠 스키마 (시뮬레이션)
- ``cad``: CAD 변종 콘텐츠 스키마 (CAD 메타데이터)

상위 단계(ingest, routes 등)에서는 본 모듈에서 직접 import 하면 된다.
"""
from .attachment import (
    ATTACHMENT_KINDS,
    AttachmentIn,
    AttachmentKind,
    AttachmentOut,
    CAPTION_MISSING_PLACEHOLDER,
    infer_attachment_kind,
)
from .cad import CADContent
from .common import (
    CAPABILITY_LABELS,
    CLASSIFICATIONS,
    DATA_TYPES,
    DERIVATIONS,
    STATUSES,
    Classification,
    DataType,
    Derivation,
    RecordIn,
    RecordOut,
    RecordSlim,
    Status,
)
from .data import DataContent
from .document import DocumentContent
from .id_format import (
    ID_PATTERN,
    LEGACY_ID_PATTERN,
    RecordID,
    format_id,
    is_legacy_id,
    normalize_id,
    parse_id,
)
from .sim import SimContent

__all__ = [
    "ATTACHMENT_KINDS",
    "AttachmentIn",
    "AttachmentKind",
    "AttachmentOut",
    "CADContent",
    "CAPABILITY_LABELS",
    "CAPTION_MISSING_PLACEHOLDER",
    "CLASSIFICATIONS",
    "Classification",
    "DATA_TYPES",
    "DERIVATIONS",
    "DataContent",
    "DataType",
    "Derivation",
    "DocumentContent",
    "ID_PATTERN",
    "LEGACY_ID_PATTERN",
    "RecordID",
    "RecordIn",
    "RecordOut",
    "RecordSlim",
    "STATUSES",
    "SimContent",
    "Status",
    "format_id",
    "infer_attachment_kind",
    "is_legacy_id",
    "normalize_id",
    "parse_id",
]
