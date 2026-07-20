"""CAD 변종 콘텐츠 스키마 (MCAD/ECAD/도면 메타데이터).

한 CAD 레코드 = 한 설계 데이터셋(보드 리비전, 부품/어셈블리)이며, 원본과
파생 포맷 파일들을 첨부(attachment, ``kind="cad"``)로 함께 담는다.

- **MCAD** — 원본 Parasolid(x_t/x_b)·CATPart 등, 파생 STEP(AI 분석용)
- **ECAD** — 원본 ODB++, 파생 ECAD-JSON 고유 포맷(AI 분석용)
- 파생 포맷(STEP/ECAD-JSON)은 BOM 코드 조회의 진입점이다 — 부품 단위는
  ``components[]`` 의 ``bom_code``, 레코드 수준 요약은 ``bom`` (BomLink).

``components[]`` 항목 관례 (dict 자유형이지만 다음 키를 권장).

- ``name``      부품/피처 이름
- ``bom_code``  BOM 코드 (PLM/ERP 조회 키)
- ``refdes``    참조 지정자 (ECAD: R101, C55 …)
- ``qty``       수량
- ``material``  재질 (MCAD)

상세 컨벤션은 리포 루트 ``cad_cae_metadata_rules.md`` 참조.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .eng_meta import BomLink, EngMeta


class CADContent(BaseModel):
    """CAD variant 의 ``content`` 페이로드."""

    model_config = ConfigDict(extra="allow")

    cad_type: Literal["MCAD", "ECAD", "DRAWING"]
    file_format: str  # 원본(네이티브) 포맷: Parasolid, CATPart, ODB++, dxf …
    file_metadata: dict[str, Any] = Field(default_factory=dict)
    components: list[dict[str, Any]] = Field(default_factory=list)

    # ---- 파생 포맷 / 엔지니어링 메타 / BOM 연계 --------------------------
    derived_formats: list[str] = Field(default_factory=list)
    """파생 포맷 목록 (예: MCAD ``["STEP"]``, ECAD ``["ecad-json"]``).

    파생 파일 자체는 첨부로 담고, 첨부 ``extra`` 에
    ``{"format_role": "derived", "format": "STEP"}`` 를 기입한다
    (원본 첨부는 ``"format_role": "native"``).
    """

    eng_meta: EngMeta | None = None   # 과제·개발단계 리비전·설계안·DOE
    bom: BomLink | None = None        # 레코드 수준 BOM 코드 요약


__all__ = ["CADContent"]
