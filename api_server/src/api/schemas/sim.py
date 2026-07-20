"""SIM 변종 콘텐츠 스키마 (CAE/시뮬레이션 결과).

한 SIM 레코드 = 한 해석 작업(job/run)이며, 솔버 입출력 파일은 첨부
(attachment, ``kind="cae"``)로 담는다. 첨부 ``extra`` 관례.

- ``solver``        "LS-DYNA" | "Abaqus" | "Nastran" | "OpenRadioss" …
- ``format``        "keyword" | "inp" | "bulk" | "binary-result" …
- ``role``          "input" | "output" | "intermediate" (dynain 등)
- ``unit_system``   "mm-t-s" 등 — 덱 파일 안에 명시가 없으므로 메타 필수
- ``model_summary`` {nodes, elements, parts} — 인제스트 시 자동 추출 권장

``inputs`` / ``outputs`` dict 관례 (자유형이지만 다음 키를 권장).

- inputs:  ``op``(전처리 op 명), ``config``(op 파라미터), ``model``(입력 첨부 id)
- outputs: ``result_files``(출력 첨부 id 목록), ``report``(op/해석 리포트 JSON)

DOE 캠페인/케이스 계층은 레코드의 ``depth`` (0=campaign, 1=case) +
``parent_record_id`` 로, 슬라이스 축은 ``eng_meta.doe`` 로 표현한다.
상세 컨벤션은 리포 루트 ``cad_cae_metadata_rules.md`` 참조.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .eng_meta import BomLink, EngMeta


class SimContent(BaseModel):
    """SIM variant 의 ``content`` 페이로드."""

    model_config = ConfigDict(extra="allow")

    solver: str  # 예: "LS-DYNA", "Abaqus", "OpenFOAM"
    solver_version: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    runtime: dict[str, Any] | None = None  # cpu_time, memory, status …

    # ---- 엔지니어링 메타 / BOM 연계 --------------------------------------
    eng_meta: EngMeta | None = None   # 과제·개발단계 리비전·설계안·DOE
    bom: BomLink | None = None        # 모델 내 부품 ↔ BOM 코드 요약


__all__ = ["SimContent"]
