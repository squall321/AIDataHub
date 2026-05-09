"""SIM 변종 콘텐츠 스키마 (CAE/시뮬레이션 결과)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SimContent(BaseModel):
    """SIM variant 의 ``content`` 페이로드."""

    model_config = ConfigDict(extra="allow")

    solver: str  # 예: "LS-DYNA", "Abaqus", "OpenFOAM"
    solver_version: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    runtime: dict[str, Any] | None = None  # cpu_time, memory, status …


__all__ = ["SimContent"]
