"""Attachment Pydantic 스키마.

`record_attachments` 테이블 행을 표현한다. 모든 첨부는 다음 11 종류 중 하나의
``kind`` 를 가진다:

- ``figure``      이미지/다이어그램 (png, jpg, svg, wmf, emf, ...)
- ``document``    임베디드 문서 (pdf, doc, docx, hwp, txt, rtf)
- ``spreadsheet`` 표 형식 파일 (xlsx, xls, csv, tsv)
- ``media``       오디오/비디오 (mp3, wav, mp4, avi, mov, webm)
- ``archive``     번들 아카이브 (zip, tar, gz, 7z)
- ``cad``         CAD 형상 데이터 — MCAD 원본(parasolid x_t/x_b, catpart …),
                  형상 교환(step, iges), ECAD 원본(ODB++) (자세한 구분은
                  ``cad_cae_metadata_rules.md``)
- ``cae``         솔버 입출력 덱/결과 (LS-DYNA k/key/dynain, Abaqus inp/odb,
                  Nastran bdf/op2, OpenRadioss rad …)
- ``chart``       차트 placeholder (PPT chart shape; 데이터는 tables[] 로 추출)
- ``drawing``     2D 도면 (dwg, dxf)
- ``data``        구조화 데이터 (json, xml, yaml)
- ``other``       위에 해당하지 않는 모든 파일

``cad`` 와 ``cae`` 의 경계: 형상(geometry)을 담으면 cad, 솔버가 소비/생산하는
해석 덱·결과면 cae. ECAD ODB++ 아카이브(.tgz)는 확장자 추론으로는 archive 가
되므로 인제스트 시 ``kind="cad"`` 를 직접 지정한다. ECAD→JSON 파생본은 확장자
추론으로 data 가 되므로 마찬가지로 ``kind="cad"`` 직접 지정을 권장한다.

확장자 추론의 알려진 충돌: ``.key`` 는 Apple Keynote 와 겹치고(이 도메인에선
LS-DYNA 덱이 압도적이라 cae 우선), ``.odb`` 는 Abaqus 결과 기준(ECAD ODB++ 는
디렉토리/아카이브라 확장자 추론 대상 아님). 오분류 시 kind 직접 지정으로 교정.

캡션 (``caption``) 은 모든 kind 에 대해 **필수** 다. 변환/인제스트 단계에서
누락된 첨부는 ``"(캡션 누락 — 검수 필요)"`` placeholder 를 채워넣어야 한다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ATTACHMENT_KINDS: tuple[str, ...] = (
    "figure",
    "document",
    "spreadsheet",
    "media",
    "archive",
    "cad",
    "cae",
    "chart",
    "drawing",
    "data",
    "other",
)

AttachmentKind = Literal[
    "figure",
    "document",
    "spreadsheet",
    "media",
    "archive",
    "cad",
    "cae",
    "chart",
    "drawing",
    "data",
    "other",
]

# 확장자 -> kind 매핑. 소문자, 점 없이.
_KIND_BY_EXT: dict[str, str] = {
    # figure
    "png": "figure", "jpg": "figure", "jpeg": "figure", "gif": "figure",
    "bmp": "figure", "wmf": "figure", "emf": "figure", "svg": "figure",
    "tif": "figure", "tiff": "figure", "webp": "figure",
    # document
    "pdf": "document", "doc": "document", "docx": "document",
    "hwp": "document", "hwpx": "document", "txt": "document", "rtf": "document",
    "odt": "document",
    # spreadsheet
    "xlsx": "spreadsheet", "xls": "spreadsheet", "xlsm": "spreadsheet",
    "csv": "spreadsheet", "tsv": "spreadsheet", "ods": "spreadsheet",
    # media
    "mp3": "media", "wav": "media", "ogg": "media", "flac": "media",
    "mp4": "media", "avi": "media", "mov": "media", "mkv": "media",
    "webm": "media", "m4a": "media",
    # archive
    "zip": "archive", "tar": "archive", "gz": "archive", "tgz": "archive",
    "7z": "archive", "rar": "archive", "bz2": "archive", "xz": "archive",
    # cad (3D)
    "step": "cad", "stp": "cad", "iges": "cad", "igs": "cad",
    "catpart": "cad", "catproduct": "cad", "sldprt": "cad", "sldasm": "cad",
    "prt": "cad", "x_t": "cad", "x_b": "cad", "stl": "cad",
    # cae (솔버 입출력 덱/결과)
    "k": "cae", "key": "cae", "dyn": "cae", "dynain": "cae",   # LS-DYNA 입력
    "d3plot": "cae",                                            # LS-DYNA 결과
    "inp": "cae", "cdb": "cae", "odb": "cae",                   # Abaqus/ANSYS
    "rad": "cae",                                               # OpenRadioss
    "bdf": "cae", "nas": "cae", "fem": "cae", "op2": "cae",     # Nastran
    # drawing (2D)
    "dwg": "drawing", "dxf": "drawing",
    # data
    "json": "data", "xml": "data", "yaml": "data", "yml": "data", "toml": "data",
}

# MIME type -> kind 매핑 (확장자 매칭 실패 시 폴백).
_KIND_BY_MIME_PREFIX: dict[str, str] = {
    "image/": "figure",
    "audio/": "media",
    "video/": "media",
    "application/pdf": "document",
    "application/msword": "document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml": "document",
    "application/vnd.ms-excel": "spreadsheet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml": "spreadsheet",
    "application/zip": "archive",
    "application/x-7z-compressed": "archive",
    "application/x-tar": "archive",
    "application/gzip": "archive",
    "application/json": "data",
    "application/xml": "data",
    "text/xml": "data",
    "text/csv": "spreadsheet",
    "text/plain": "document",
}


# 확장자 없는 솔버 산출물 파일명 접두(LS-DYNA 관례: d3plot, d3plot01, binout0000 …).
_CAE_BASENAME_PREFIXES: tuple[str, ...] = (
    "d3plot", "d3dump", "binout", "dynain", "d3hsp",
    "rcforc", "nodout", "glstat", "messag",
)


def infer_kind_from_extension(filename: str | None) -> str | None:
    """파일명에서 확장자를 추출해 kind 를 추정한다. 매칭 실패 시 None."""
    if not filename:
        return None
    name = str(filename).lower().strip()
    if "." not in name:
        # LS-DYNA 산출물은 확장자 없이 관례 이름을 쓴다 (d3plot, dynain …).
        base = name.rsplit("/", 1)[-1]
        if base.startswith(_CAE_BASENAME_PREFIXES):
            return "cae"
        return None
    # 'a.tar.gz' 같은 복합 확장자 처리: 마지막 점만 본다.
    ext = name.rsplit(".", 1)[-1]
    return _KIND_BY_EXT.get(ext)


def infer_kind_from_mime(mime: str | None) -> str | None:
    """MIME type 에서 kind 를 추정한다. 매칭 실패 시 None."""
    if not mime:
        return None
    m = mime.strip().lower()
    # 정확 매칭 우선
    for key, kind in _KIND_BY_MIME_PREFIX.items():
        if m == key or m.startswith(key):
            return kind
    return None


def infer_attachment_kind(
    filename: str | None = None,
    mime: str | None = None,
) -> str:
    """확장자 + MIME 으로 attachment kind 결정. 항상 11 종 중 하나를 반환.

    매칭 실패 시 ``"other"`` 로 폴백한다. ``"chart"`` 는 확장자/MIME 으로
    추정되지 않고, PPT 변환기에서 chart shape 감지 시 직접 지정한다.
    """
    by_ext = infer_kind_from_extension(filename)
    if by_ext is not None:
        return by_ext
    by_mime = infer_kind_from_mime(mime)
    if by_mime is not None:
        return by_mime
    return "other"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
CAPTION_MISSING_PLACEHOLDER = "(캡션 누락 — 검수 필요)"


class AttachmentIn(BaseModel):
    """입력용 첨부 모델 (record 와 별도로 직접 생성하는 케이스).

    PK ``id`` 는 ``"{record_id}-A{nnn}"`` 또는 (호환) ``"{record_id}-F{nnn}"``.
    `caption` 은 필수다. 빈 문자열이면 placeholder 로 대체된다.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: str
    record_id: str
    number: int = Field(..., ge=1)
    kind: AttachmentKind = "other"
    caption: str
    file_name: str | None = None
    file_path: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    hash_sha256: str | None = None
    section_ref: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("caption", mode="before")
    @classmethod
    def _caption_required(cls, v: Any) -> str:
        # caption 이 None / 공백 만 있으면 placeholder 로 채운다.
        if v is None:
            return CAPTION_MISSING_PLACEHOLDER
        s = str(v).strip()
        if not s:
            return CAPTION_MISSING_PLACEHOLDER
        return s

    @field_validator("kind", mode="before")
    @classmethod
    def _kind_lower(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.lower()
        return v


class AttachmentOut(AttachmentIn):
    """DB 에서 읽어 반환되는 형태."""

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
        from_attributes=True,
    )

    created_at: datetime | None = None


__all__ = [
    "ATTACHMENT_KINDS",
    "AttachmentIn",
    "AttachmentKind",
    "AttachmentOut",
    "CAPTION_MISSING_PLACEHOLDER",
    "infer_attachment_kind",
    "infer_kind_from_extension",
    "infer_kind_from_mime",
]
