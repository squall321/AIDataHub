"""CAD 변종 콘텐츠 스키마 (MCAD/ECAD/도면 메타데이터)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CADContent(BaseModel):
    """CAD variant 의 ``content`` 페이로드."""

    model_config = ConfigDict(extra="allow")

    cad_type: Literal["MCAD", "ECAD", "DRAWING"]
    file_format: str  # CATPart, STEP, ODB++, dxf …
    file_metadata: dict[str, Any] = Field(default_factory=dict)
    components: list[dict[str, Any]] = Field(default_factory=list)


__all__ = ["CADContent"]
