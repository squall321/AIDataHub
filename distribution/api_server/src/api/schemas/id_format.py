"""레코드 ID 파싱·검증 유틸리티.

공식 ID 포맷:
    {DATA_TYPE}-{TEAM}-{GROUP}-{YEAR}-{SEQ:06d}
    예: DOC-HE-CAE-2026-0000000001

레거시 ID 포맷 (DATA_TYPE 접두사 누락):
    {TEAM}-{GROUP}-{YEAR}-{SEQ:06d}
    예: HE-CAE-2026-0000000001
    → 파싱 시 ``data_type`` 인수 또는 기본값 ``"DOC"``로 보강한다.

규칙:
    - ``DATA_TYPE``: ``DOC | DATA | SIM | CAD | LOG | FORM | OTHER``
    - ``TEAM``: 2~4자 대문자 ASCII (HE, DA, MX, VD …)
    - ``GROUP``    : 2~5자 대문자 ASCII (CAE, MFG, QA, DEV, PLM …)
    - ``YEAR``    : 4자리 (2020~2099)
    - ``SEQ``     : 6자리 zero-pad (000001~999999)

본 모듈은 데이터베이스/스키마 양쪽에서 import 되므로 외부 의존성을 최소화한다.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
DATA_TYPES: tuple[str, ...] = (
    "DOC",
    "DATA",
    "SIM",
    "CAD",
    "LOG",
    "FORM",
    "OTHER",
)

DataType = Literal["DOC", "DATA", "SIM", "CAD", "LOG", "FORM", "OTHER"]

# DATA_TYPE 알터네이션. 길이 내림차순으로 정렬해 부분 일치를 방지한다.
_DATA_TYPE_ALT = "|".join(sorted(DATA_TYPES, key=len, reverse=True))

# 정식 ID
ID_PATTERN = re.compile(
    rf"^(?P<data_type>{_DATA_TYPE_ALT})"
    r"-(?P<team>[A-Z]{2,4})"
    r"-(?P<group>[A-Z]{2,5})"
    r"-(?P<year>20[2-9][0-9])"
    r"-(?P<seq>\d{6})$"
)

# 레거시 ID (data_type 누락)
LEGACY_ID_PATTERN = re.compile(
    r"^(?P<team>[A-Z]{2,4})"
    r"-(?P<group>[A-Z]{2,5})"
    r"-(?P<year>20[2-9][0-9])"
    r"-(?P<seq>\d{6})$"
)


# ---------------------------------------------------------------------------
# 함수 API
# ---------------------------------------------------------------------------
def parse_id(id: str, default_data_type: str = "DOC") -> dict:
    """ID 문자열을 구성요소 dict 로 분해한다.

    Args:
        id: 레코드 ID 문자열.
        default_data_type: 레거시 ID 일 때 사용할 ``data_type`` 기본값.

    Returns:
        ``{"data_type": str, "team": str, "group": str, "year": int, "seq": int}``

    Raises:
        ValueError: 정식·레거시 어느 패턴과도 일치하지 않을 때.
    """
    if not isinstance(id, str) or not id:
        raise ValueError("id must be a non-empty string")

    m = ID_PATTERN.match(id)
    if m:
        return {
            "data_type": m.group("data_type"),
            "team": m.group("team"),
            "group": m.group("group"),
            "year": int(m.group("year")),
            "seq": int(m.group("seq")),
        }

    legacy = LEGACY_ID_PATTERN.match(id)
    if legacy:
        if default_data_type not in DATA_TYPES:
            raise ValueError(
                f"default_data_type {default_data_type!r} is not a valid DataType"
            )
        return {
            "data_type": default_data_type,
            "team": legacy.group("team"),
            "group": legacy.group("group"),
            "year": int(legacy.group("year")),
            "seq": int(legacy.group("seq")),
        }

    raise ValueError(
        f"Invalid record id {id!r}: expected "
        "'{DATA_TYPE}-{TEAM}-{GROUP}-{YYYY}-{NNNNNN}' "
        "or legacy '{TEAM}-{GROUP}-{YYYY}-{NNNNNN}'"
    )


def is_legacy_id(id: str) -> bool:
    """주어진 ID가 레거시 포맷(접두사 누락)이면 True."""
    return ID_PATTERN.match(id) is None and LEGACY_ID_PATTERN.match(id) is not None


def format_id(
    data_type: str,
    team: str,
    group: str,
    year: int,
    seq: int,
) -> str:
    """구성요소를 정식 ID 문자열로 합성한다.

    검증을 거친 뒤 zero-padded SEQ 로 합성한다.
    """
    if data_type not in DATA_TYPES:
        raise ValueError(f"data_type must be one of {DATA_TYPES}, got {data_type!r}")
    if not re.fullmatch(r"[A-Z]{2,4}", team):
        raise ValueError(f"team must be 2-4 uppercase ASCII letters, got {team!r}")
    if not re.fullmatch(r"[A-Z]{2,5}", group):
        raise ValueError(f"group must be 2-5 uppercase ASCII letters, got {group!r}")
    if not (2020 <= int(year) <= 2099):
        raise ValueError(f"year must be in 2020..2099, got {year!r}")
    if not (1 <= int(seq) <= 999_999):
        raise ValueError(f"seq must be in 1..999999, got {seq!r}")

    return f"{data_type}-{team}-{group}-{int(year)}-{int(seq):06d}"


def normalize_id(id: str, default_data_type: str = "DOC") -> str:
    """레거시 ID 면 ``DATA_TYPE`` 접두사를 붙여 정식 ID 로 정규화한다.

    이미 정식 ID 면 그대로 반환한다.
    """
    parts = parse_id(id, default_data_type=default_data_type)
    return format_id(
        data_type=parts["data_type"],
        team=parts["team"],
        group=parts["group"],
        year=parts["year"],
        seq=parts["seq"],
    )


# ---------------------------------------------------------------------------
# Pydantic Model
# ---------------------------------------------------------------------------
class RecordID(BaseModel):
    """ID 구성요소를 모델로 표현한다.

    ``RecordID.from_string()`` 으로 문자열에서 인스턴스를 만들거나, 컴포넌트 별 검증을
    Pydantic 에 위임할 수 있다.
    """

    data_type: DataType
    team: str = Field(..., min_length=2, max_length=4)
    group: str = Field(..., min_length=2, max_length=5)
    year: int = Field(..., ge=2020, le=2099)
    seq: int = Field(..., ge=1, le=999_999)

    @field_validator("team")
    @classmethod
    def _div_upper(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Z]{2,4}", v):
            raise ValueError("team must be 2-4 uppercase ASCII letters")
        return v

    @field_validator("group")
    @classmethod
    def _team_upper(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Z]{2,5}", v):
            raise ValueError("group must be 2-5 uppercase ASCII letters")
        return v

    @classmethod
    def from_string(cls, id: str, default_data_type: str = "DOC") -> "RecordID":
        return cls(**parse_id(id, default_data_type=default_data_type))

    def to_string(self) -> str:
        return format_id(
            data_type=self.data_type,
            team=self.team,
            group=self.group,
            year=self.year,
            seq=self.seq,
        )


__all__ = [
    "DATA_TYPES",
    "DataType",
    "ID_PATTERN",
    "LEGACY_ID_PATTERN",
    "RecordID",
    "format_id",
    "is_legacy_id",
    "normalize_id",
    "parse_id",
]
