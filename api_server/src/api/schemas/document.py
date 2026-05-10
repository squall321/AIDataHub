"""DOC 변종 콘텐츠 스키마 (Word→JSON 변환 산출물).

``json_schema_rules.md`` v1.0/v1.1 에 대응. 최상위 키:
``meta`` / ``toc`` / ``sections`` / ``figures`` (back-compat) / ``tables`` /
``sources`` / ``attachments`` (preferred).

마이그레이션 경로:
    - 신규 변환 산출물은 ``attachments[]`` 에 모든 종류 (figure / document /
      spreadsheet / media / archive / cad / drawing / data / other) 를 담는다.
    - 기존 (legacy) 산출물은 ``figures[]`` 만 가질 수 있다 — 인제스트
      단계에서 ``figures[]`` 항목을 ``kind="figure"`` 인 attachment 로 자동
      변환한다.
    - 두 키가 동시에 있어도 허용 — 인제스트는 두 리스트를 합집합으로 처리.

``figures[i]`` / ``attachments[i]`` 는 모두 자유 형식 dict 로 받는다.
권장 키:

- ``id`` (str), ``number`` (int), ``caption`` (str), ``section_ref`` (str)
- ``kind`` (str)         — attachments[] 만. 10 종 중 하나.
- ``file_path`` (str)    — 정적 마운트 ``/attachments`` 직하 상대 경로.
- ``image_path`` (str)   — figures[] 의 legacy 키. attachments[] 에서는
                            ``file_path`` 로 통일된다.
- ``mime_type`` (str)    — 선택.
- ``size_bytes`` (int)   — 선택.
- ``hash_sha256`` (str)  — 선택.
- ``source_ref`` (str)   — sources[] 의 id 참조 (선택).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocumentContent(BaseModel):
    """DOC variant 의 ``content`` 필드 페이로드."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=False)

    schema_version: str = "1.0"
    meta: dict[str, Any] = Field(default_factory=dict)
    toc: list[dict[str, Any]] = Field(default_factory=list)
    sections: list[dict[str, Any]] = Field(default_factory=list)
    figures: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("sections")
    @classmethod
    def _sections_have_id_title(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for idx, sec in enumerate(v):
            if not isinstance(sec, dict):
                raise ValueError(f"sections[{idx}] must be a dict")
            # id 와 title 은 필수 (level 은 누락시 1 로 보강 가능하므로 검증 안 함)
            if "id" not in sec:
                raise ValueError(f"sections[{idx}] missing 'id'")
            if "title" not in sec:
                raise ValueError(f"sections[{idx}] missing 'title'")
        return v


__all__ = ["DocumentContent"]
