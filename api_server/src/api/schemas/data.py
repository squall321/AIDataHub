"""DATA 변종 콘텐츠 스키마 (표 형태 데이터)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# 분석/작도 힌트로 자주 쓰는 graph_type 어휘 (자유 문자열이지만 이 값을
# 권장 — 도구가 이걸 보고 적합한 분석을 자동 선택한다).
GRAPH_TYPES = ("stress_strain", "time_series", "scatter", "line", "bar", "histogram", "table")


class DataContent(BaseModel):
    """DATA variant 의 ``content`` 페이로드.

    ``graph_type`` 등 분석 힌트 필드 — 데이터 Description 에 "이건 이렇게
    분석/작도한다"를 담아 도구(예: stress_strain_plot)가 인식하게 한다.
    (DESKTOP_MCP_MIGRATION_PLAN.md v2 Phase 3)
    """

    model_config = ConfigDict(extra="allow")

    caption: str = ""
    headers: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    units: dict[str, str] | None = None
    notes: str = ""
    # 분석 힌트 (옵셔널). graph_type 권장 어휘는 GRAPH_TYPES.
    graph_type: str | None = None
    x_axis: str | None = None
    y_axis: str | None = None
    scale: str | None = None

    @field_validator("headers")
    @classmethod
    def _headers_strings(cls, v: list[Any]) -> list[str]:
        for h in v:
            if not isinstance(h, str):
                raise ValueError(f"headers must be str, got {type(h).__name__}")
        return v

    @model_validator(mode="after")
    def _row_widths(self) -> "DataContent":
        if not self.headers:
            return self
        width = len(self.headers)
        for i, row in enumerate(self.rows):
            if not isinstance(row, list):
                raise ValueError(f"rows[{i}] must be a list")
            if len(row) != width:
                raise ValueError(
                    f"rows[{i}] has {len(row)} cells, expected {width} (headers width)"
                )
        return self


__all__ = ["DataContent"]
