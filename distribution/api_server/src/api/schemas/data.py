"""DATA 변종 콘텐츠 스키마 (표 형태 데이터)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DataContent(BaseModel):
    """DATA variant 의 ``content`` 페이로드."""

    model_config = ConfigDict(extra="allow")

    caption: str = ""
    headers: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    units: dict[str, str] | None = None
    notes: str = ""

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
