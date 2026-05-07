"""Ingestion 패키지.

- ``normalizer``: 변종(DOC/DATA/SIM/CAD/…) JSON → 통합 ``RecordIn`` 변환
- ``loader``: 파일/디렉터리에서 JSON 을 읽어 정규화·검증
- ``db_writer``: 정규화된 ``RecordIn`` 을 DB 에 영속화
- ``cli``: ``python -m api.ingest`` CLI 진입점
"""
from .normalizer import (
    canonical_json,
    compute_content_hash,
    detect_variant,
    normalize,
)

__all__ = [
    "canonical_json",
    "compute_content_hash",
    "detect_variant",
    "normalize",
]
