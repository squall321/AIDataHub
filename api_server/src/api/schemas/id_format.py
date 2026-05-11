"""레코드 ID 파싱·검증 유틸리티.

공식 ID 포맷 (v3 — 2026-05-11~):
    {DATA_TYPE}-{TEAM}-{GROUP}-{YEAR}-{SEQ:010d}
    예: DOC-HE-CAE-2026-0000000001

레거시 ID 포맷:
    - v2 (8-digit seq): DOC-HE-CAE-2026-00000001 — 파싱 OK
    - v1 (6-digit seq): DOC-HE-CAE-2026-000001  — 파싱 OK
    - v0 (DATA_TYPE 접두사 누락): HE-CAE-2026-... — ``data_type`` 인수 보강 후 파싱

규칙:
    - ``DATA_TYPE``: ``DOC | DATA | SIM | CAD | LOG | FORM | OTHER``
    - ``TEAM``: 2~4자 대문자 ASCII (HE, DA, MX, VD …)
    - ``GROUP``    : 2~5자 대문자 ASCII (CAE, MFG, QA, DEV, PLM …)
    - ``YEAR``    : 4자리 (2020~2099)
    - ``SEQ``     : **6~12자리** zero-pad (regex 는 6+ 자리 허용 — 미래
      BIGINT 마이그레이션 까지 확장 여지). 신규 생성은 10자리
      = 1..2,147,483,647 (INTEGER 컬럼 한계, 약 21억).
      더 필요하면 ``records.seq`` 를 BIGINT 로 마이그레이션 후 SEQ_MAX 상향.

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

# 신규 zero-pad 폭: 10자리 (display). 실제 INTEGER 한계 = 2,147,483,647.
# 정규식은 6~12자리 허용 (legacy 6/8자리 호환 + 미래 BIGINT 확장 여지).
SEQ_PAD_WIDTH = 10
SEQ_MAX = 2_147_483_647   # INT32 max (PG INTEGER 컬럼 한계). BIGINT 이주 시 상향.

# 정식 ID — seq 는 6~12자리 허용
ID_PATTERN = re.compile(
    rf"^(?P<data_type>{_DATA_TYPE_ALT})"
    r"-(?P<team>[A-Z]{2,4})"
    r"-(?P<group>[A-Z]{2,5})"
    r"-(?P<year>20[2-9][0-9])"
    r"-(?P<seq>\d{6,12})$"
)

# 레거시 ID (data_type 누락, v0)
LEGACY_ID_PATTERN = re.compile(
    r"^(?P<team>[A-Z]{2,4})"
    r"-(?P<group>[A-Z]{2,5})"
    r"-(?P<year>20[2-9][0-9])"
    r"-(?P<seq>\d{6,12})$"
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
        "'{DATA_TYPE}-{TEAM}-{GROUP}-{YYYY}-{NNNNNNNNNN}' (10-digit seq) "
        "or legacy '{TEAM}-{GROUP}-{YYYY}-{6-12digit}' (backward-compat)"
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
    if not (1 <= int(seq) <= SEQ_MAX):
        raise ValueError(f"seq must be in 1..{SEQ_MAX}, got {seq!r}")

    return f"{data_type}-{team}-{group}-{int(year)}-{int(seq):0{SEQ_PAD_WIDTH}d}"


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
    seq: int = Field(..., ge=1, le=SEQ_MAX)

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
