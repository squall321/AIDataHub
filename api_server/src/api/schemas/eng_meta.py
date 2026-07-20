# CAD/CAE(SIM) 공용 엔지니어링 메타 — 과제·개발단계 리비전·설계안·DOE·BOM 연계 스키마
"""CAD/SIM 변종이 공유하는 엔지니어링 메타데이터 스키마.

스마트폰류 하드웨어 개발 흐름의 세 축을 구조화한다.

1. **개발단계 리비전** (``DevRevision``) — pre/dv/pv/pra/mp 단계 + 차수.
   ``code`` (dv1, dvr, pra …) 는 매칭 질의용, ``seq`` 는 범위 질의용
   ("pv1 이후 전부" = ``seq >= 310``). phase/round 에서 자동 유도된다.
2. **설계안 vs DOE** — ``design_variation`` 은 설계자가 고른 대안(A안/B안,
   소수), ``DoeRef`` 는 인자 조합으로 자동 생성된 케이스(대량). 두 축을
   섞으면 설계안 비교 질의에 DOE 케이스가 쏟아지므로 필드를 분리한다.
   DOE 캠페인/케이스의 레코드 계층은 ``RecordIn.depth`` (0=campaign,
   1=case) + ``parent_record_id`` 로 표현하고, 본 메타는 슬라이스 질의용.
3. **BOM 연계** (``BomLink``) — ECAD→JSON / MCAD→STEP 파생 포맷에서
   추출한 BOM 코드로 PLM/ERP 를 바로 조회하기 위한 레코드 수준 요약.
   부품 단위 상세는 ``CADContent.components[]`` 의 ``bom_code`` 관례 사용.

단계 코드 ↔ seq 표 (범위 질의 기준값).

======  =====  =====  ====
code    phase  round  seq
======  =====  =====  ====
pre     pre    —      100
dv1     dv     1      210
dv2     dv     2      220
dv3     dv     3      230
dvr     dv     r      290
pv1     pv     1      310
pv2     pv     2      320
pv3     pv     3      330
pvr     pv     r      390
pra     pra    —      400
mp      mp     —      500
======  =====  =====  ====
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Phase = Literal["pre", "dv", "pv", "pra", "mp"]
Round = Literal["1", "2", "3", "r"]

_PHASE_BASE: dict[str, int] = {"pre": 100, "dv": 200, "pv": 300, "pra": 400, "mp": 500}
_ROUND_OFFSET: dict[str, int] = {"1": 10, "2": 20, "3": 30, "r": 90}
_ROUNDED_PHASES = ("dv", "pv")   # 차수 필수 단계. pre/pra/mp 는 차수 없음.


class DevRevision(BaseModel):
    """개발단계 리비전. ``phase`` (+ ``round``) 만 주면 code/seq 는 자동 유도.

    통제어휘: dv/pv 는 round 필수(dv1…dvr), pre/pra/mp 는 round 금지 —
    표 밖 조합(dv, pre1, mpr …)은 검증 단계에서 거부된다.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    phase: Phase
    round: Round | None = None
    code: str = ""   # 정규화 합성코드 (dv1, dvr, pra …) — 비우면 자동
    seq: int = 0     # 순서수치 — 비우면(0) 자동

    @field_validator("round", mode="before")
    @classmethod
    def _round_str(cls, v: Any) -> Any:
        # JSON 숫자(1)로 와도 문서 표기("1")로 수용한다.
        if isinstance(v, int) and not isinstance(v, bool):
            return str(v)
        return v

    @model_validator(mode="after")
    def _derive(self) -> "DevRevision":
        if self.phase in _ROUNDED_PHASES and self.round is None:
            raise ValueError(f"phase {self.phase!r} 는 round 필수 (1/2/3/r)")
        if self.phase not in _ROUNDED_PHASES and self.round is not None:
            raise ValueError(f"phase {self.phase!r} 는 round 를 갖지 않는다")
        derived_code = self.phase + (self.round or "")
        if not self.code:
            self.code = derived_code
        elif self.code != derived_code:
            raise ValueError(
                f"code {self.code!r} != phase+round {derived_code!r} — "
                "code 는 생략하거나 일치시켜야 한다"
            )
        derived_seq = _PHASE_BASE[self.phase] + (_ROUND_OFFSET[self.round] if self.round else 0)
        if self.seq == 0:
            self.seq = derived_seq
        elif self.seq != derived_seq:
            raise ValueError(f"seq {self.seq} != 유도값 {derived_seq} — seq 는 생략 권장")
        return self


class DoeRef(BaseModel):
    """DOE 캠페인/케이스 참조. 캠페인 레코드는 case 없이 study 만 기입."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    study: str                      # DOE 캠페인 ID (예: "cms_L3")
    case: str | None = None         # 케이스 식별자 (예: "p4") — 캠페인 레코드는 None
    factors: dict[str, Any] = Field(default_factory=dict)   # 인자값 (예: {"gap": 0.3})


class BomLink(BaseModel):
    """BOM 연계 요약 — 이 데이터에서 조회 가능한 BOM 코드 목록."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    system: str | None = None       # PLM/ERP 시스템명 (사내 표준명)
    codes: list[str] = Field(default_factory=list)   # BOM 코드 목록
    coverage: Literal["full", "partial", "none"] | None = None  # 코드 추출 완전성


class EngMeta(BaseModel):
    """CAD/SIM 공용 엔지니어링 메타 블록.

    ``project`` 는 ``RecordIn.project`` 와 동일 값을 병기한다(변종 내 자기완결).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project: str | None = None              # 과제코드 (record.project 와 동일 값)
    dev_revision: DevRevision | None = None
    design_variation: str | None = None     # 설계안 (A안/B안 등 — 과제 내 통제어휘)
    doe: DoeRef | None = None
    model_revision: str | None = None       # 같은 단계 내 모델 재작업 rev (v1, v2 …)


__all__ = ["BomLink", "DevRevision", "DoeRef", "EngMeta", "Phase", "Round"]
