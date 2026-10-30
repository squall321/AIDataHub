"""파일 확장자 기반 통합 변환 디스패처.

지원 포맷:
    - ``.docx``           → ``converter`` (Word)
    - ``.xlsx``           → ``excel_converter``
    - ``.pptx``           → ``ppt_converter``
    - ``.md`` / ``.markdown`` → ``md_converter``
    - ``.pdf``            → ``pdf_converter`` (Agent 26 — 현재는 선택)

각 변환기는 서로 다른 ``ConverterOptions`` shape 을 갖지만, 모두 ``ConversionResult``
또는 그에 준하는 출력을 만들고 ``to_dict()`` 또는 dict-like payload 를 노출한다.

PDF 변환기는 아직 미설치 상태일 수 있으므로 함수 단위 ``ImportError`` 가드를 둔다.
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..errors import APIError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 도메인 에러
# ---------------------------------------------------------------------------
class UnsupportedFormatError(APIError):
    """지원하지 않는 확장자."""

    status_code = 415
    code = "UNSUPPORTED_FORMAT"


class PdfNotAvailableError(APIError):
    """PDF 변환기가 설치되어 있지 않다."""

    status_code = 501
    code = "PDF_NOT_AVAILABLE"


class ConversionFailedError(APIError):
    """변환 단계에서 예외가 발생."""

    status_code = 500
    code = "CONVERSION_FAILED"


class DrmProtectedError(APIError):
    """DRM 으로 암호화된(또는 암호 · 구형 형식) 문서 — 서버가 열 수 없다."""

    status_code = 422
    code = "DRM_PROTECTED"


# ---------------------------------------------------------------------------
# 포맷 enum / 매핑
# ---------------------------------------------------------------------------
class SourceFormat(str, Enum):
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"
    MD = "md"
    PDF = "pdf"


EXTENSION_MAP: dict[str, SourceFormat] = {
    ".docx": SourceFormat.DOCX,
    ".xlsx": SourceFormat.XLSX,
    ".pptx": SourceFormat.PPTX,
    ".md": SourceFormat.MD,
    ".markdown": SourceFormat.MD,
    ".pdf": SourceFormat.PDF,
}


def detect_format(filename: str) -> SourceFormat:
    """파일명에서 확장자를 보고 ``SourceFormat`` 을 결정한다.

    Raises:
        UnsupportedFormatError: 매핑에 없는 확장자.
    """
    if not filename:
        raise UnsupportedFormatError("파일명이 비어 있습니다")
    ext = Path(filename).suffix.lower()
    if ext not in EXTENSION_MAP:
        raise UnsupportedFormatError(
            f"확장자 {ext or '(없음)'} 미지원",
            details={
                "extension": ext,
                "supported": sorted(EXTENSION_MAP.keys()),
            },
        )
    return EXTENSION_MAP[ext]


# ---------------------------------------------------------------------------
# 변환 요청 컨텍스트
# ---------------------------------------------------------------------------
@dataclass
class ConvertRequest:
    """라우터에서 디스패처로 전달되는 변환 매개변수."""

    team: str
    group: str
    year: int
    seq: int = 1
    tags: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    classification: str = "internal"
    domain: str | None = None
    output_dir: Path | None = None  # None 이면 임시 폴더 사용

    def resolved_output_dir(self) -> Path:
        if self.output_dir is None:
            return Path(tempfile.mkdtemp(prefix="ai_data_convert_"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir


# ---------------------------------------------------------------------------
# Excel 결합 헬퍼
# ---------------------------------------------------------------------------
def _excel_sheets_to_dict(sheets: list[Any], options: Any) -> dict[str, Any]:
    """``XlsxConverter.convert()`` 결과(시트 리스트)를 단일 dict 로 합친다.

    - 단일 시트면 그 시트의 ``to_payload()`` 를 그대로 반환한다.
    - 여러 시트면 ``{"data_type": "DATA_BUNDLE", "sheets": [...]}`` 형태로 묶는다.
    """
    if not sheets:
        return {
            "schema_version": "data.v1",
            "data_type": "DATA",
            "sheets": [],
            "warnings": ["변환된 시트가 없습니다"],
        }
    if len(sheets) == 1:
        payload = sheets[0].to_payload(options)
        # normalize 가 인식할 수 있도록 id 필드 보강.
        payload.setdefault("id", payload.get("data_id"))
        return payload
    bundle: dict[str, Any] = {
        "schema_version": "data.v1",
        "data_type": "DATA_BUNDLE",
        "id": sheets[0].data_id,
        "title": getattr(sheets[0], "caption", "") or sheets[0].data_id,
        "sheets": [s.to_payload(options) for s in sheets],
    }
    return bundle


# ---------------------------------------------------------------------------
# DRM 머리 판정 — 변환기에 넣기 전에 앞 바이트로 암호화 문서를 가른다
# ---------------------------------------------------------------------------
# 표는 HWAX 포털 ``frontend/src/components/chat/docAttach.ts`` 의 ``DRM_HEAD`` ·
# ``isEncryptedOffice`` 와 같다(10-27 요청 AD-03 · 포털 HP-14). 언어가 달라 코드를
# 나눠 쓰지 못하므로 표를 같게 둔다.
#   50 4B                        "PK"        → zip(정상 OOXML) = 평문
#   D0 CF 11 E0                  OLE 복합문서 → 암호화(또는 구형 .ppt · .doc · .xls)
#   3C 23 23 20 4E 41 53 43 41   "<## NASCA" → 사내 DRM 머리 = 암호화
#                                              (10-25 PC 시험 파일 앞 34바이트)
#   25 50 44 46                  "%PDF"      → 평문
# 평문과 모르는 머리는 변환기에 맡긴다 — 깨진 파일은 종전대로 변환 실패로 간다.
_OLE_HEAD = bytes([0xD0, 0xCF, 0x11, 0xE0])
_DRM_HEAD = bytes([0x3C, 0x23, 0x23, 0x20, 0x4E, 0x41, 0x53, 0x43, 0x41])

_DRM_WHY = {
    "NASCA": "사내 DRM(NASCA)으로 암호화된 문서라 서버가 열 수 없습니다.",
    "OLE": "암호화(DRM · 암호)돼 있거나 구형 형식(.ppt · .doc · .xls)이라 서버가 열 수 없습니다.",
}
# 할 일 — PPT · Word · PDF 는 포털이 같은 파일에 띄우는 안내(DocExtractHint ·
# docs/chat-ppt B)와 같은 길이고 단추 이름도 포털 그대로다(PDF 도 Word).
# PC 도우미는 Excel · md 를 열지 않는다(포털 hwax-doc-extract.ps1).
_PC_APP = {SourceFormat.PPTX: "PowerPoint", SourceFormat.DOCX: "Word", SourceFormat.PDF: "Word"}
_DRM_HINT_PC = (
    "HWAX 포털 챗에 붙이면 뜨는 "
    "'PC 의 {app} 로 열어 가져오기'(PC 도우미)로 글을 뽑아 올리세요."
)
_DRM_HINT_OTHER = "PC 에서 파일을 열어 필요한 내용을 복사해 챗에 붙여 넣으세요."
# OLE 는 암호만 건 새 형식이거나 확장자만 바꾼 구형일 수 있다 — 다시 저장하면 서버가 읽는다.
_OLE_RESAVE = (
    "암호만 걸렸거나 구형이면 암호를 풀고 "
    "새 형식(.pptx · .docx · .xlsx)으로 다시 저장해 올려도 됩니다."
)


def _drm_message(head: str, fmt: SourceFormat) -> str:
    """무엇이 + 할 일을 한 줄로. HWAX 포털은 MCP 결과의 error 칸 첫 줄을 220자까지만 화면에 낸다."""
    app = _PC_APP.get(fmt)
    parts = [_DRM_WHY[head], _DRM_HINT_PC.format(app=app) if app else _DRM_HINT_OTHER]
    if head == "OLE":
        parts.append(_OLE_RESAVE)
    return " ".join(parts)


def _encrypted_head(file_path: Path) -> str | None:
    """앞 바이트로 본 암호화 판정 — ``"NASCA"`` · ``"OLE"``, 평문이거나 모르면 ``None``."""
    with file_path.open("rb") as fh:
        b = fh.read(16)
    if b.startswith(_OLE_HEAD):
        return "OLE"
    if b.startswith(_DRM_HEAD):
        return "NASCA"
    return None


# ---------------------------------------------------------------------------
# 메인 디스패처
# ---------------------------------------------------------------------------
def convert_file(
    file_path: Path,
    fmt: SourceFormat,
    req: ConvertRequest,
) -> dict[str, Any]:
    """``fmt`` 에 맞는 변환기로 ``file_path`` 를 처리하고 dict payload 를 돌려준다."""
    file_path = Path(file_path)
    if not file_path.exists():
        raise ConversionFailedError(
            f"입력 파일이 존재하지 않습니다: {file_path}",
            details={"path": str(file_path)},
        )

    # 암호화 문서는 변환기에 넣지 않는다 — 넣으면 '손상' 으로만 답한다(AD-03).
    # 임시 폴더를 만들기 전에 거른다.
    head = _encrypted_head(file_path)
    if head is not None:
        raise DrmProtectedError(_drm_message(head, fmt), details={"header": head})

    output_dir = req.resolved_output_dir()

    try:
        if fmt == SourceFormat.DOCX:
            from converter.core import Converter, ConverterOptions

            opts = ConverterOptions(
                team=req.team,
                group=req.group,
                year=req.year,
                seq=req.seq,
                output_dir=output_dir,
            )
            result = Converter(opts).convert(str(file_path))
            payload = result.to_dict()
            # normalize 가 ID 를 잘 잡도록 meta.doc_id → data_type prefix 보강.
            return _augment_doc_payload(payload, "DOC", req)

        if fmt == SourceFormat.XLSX:
            from excel_converter.core import XlsxConverter, XlsxConverterOptions

            opts = XlsxConverterOptions(
                team=req.team,
                group=req.group,
                year=req.year,
                start_seq=req.seq,
                output_dir=output_dir,
            )
            sheets = XlsxConverter(opts).convert(file_path)
            payload = _excel_sheets_to_dict(sheets, opts)
            return _augment_data_payload(payload, req)

        if fmt == SourceFormat.PPTX:
            from ppt_converter.core import PptxConverter, PptxConverterOptions

            opts = PptxConverterOptions(
                team=req.team,
                group=req.group,
                year=req.year,
                seq=req.seq,
                output_dir=output_dir,
                tags=list(req.tags),
                agents=list(req.agents),
            )
            result = PptxConverter(opts).convert(file_path)
            payload = result.to_dict()
            return _augment_doc_payload(payload, "DOC", req)

        if fmt == SourceFormat.MD:
            from md_converter.core import MarkdownConverter, MarkdownConverterOptions

            opts = MarkdownConverterOptions(
                team=req.team,
                group=req.group,
                year=req.year,
                seq=req.seq,
                output_dir=output_dir,
                tags=list(req.tags),
                agents=list(req.agents),
            )
            result = MarkdownConverter(opts).convert(file_path)
            payload = result.to_dict()
            return _augment_doc_payload(payload, "DOC", req)

        if fmt == SourceFormat.PDF:
            try:
                from pdf_converter.core import (  # type: ignore[import-not-found]
                    PdfConverter,
                    PdfConverterOptions,
                )
            except ImportError as exc:
                raise PdfNotAvailableError(
                    "PDF 변환기가 설치되어 있지 않습니다",
                    details={"hint": "Agent 26 의 pdf_converter 모듈이 필요합니다"},
                ) from exc

            opts = PdfConverterOptions(  # type: ignore[call-arg]
                team=req.team,
                group=req.group,
                year=req.year,
                seq=req.seq,
                output_dir=output_dir,
            )
            result = PdfConverter(opts).convert(str(file_path))  # type: ignore[call-arg]
            # pdf_converter 도 ConversionResult.to_dict() 규약을 따른다고 가정.
            if hasattr(result, "to_dict"):
                payload = result.to_dict()
            elif isinstance(result, dict):
                payload = result
            else:
                raise ConversionFailedError(
                    "PDF 변환기 결과 형식을 인식할 수 없습니다",
                    details={"type": type(result).__name__},
                )
            return _augment_doc_payload(payload, "DOC", req)

        raise UnsupportedFormatError(f"포맷 {fmt} 디스패치 미구현")

    except APIError:
        raise
    except FileNotFoundError as exc:
        raise ConversionFailedError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover — 안전망
        logger.exception("conversion failed for %s", file_path)
        raise ConversionFailedError(
            f"{fmt.value} 변환 실패: {exc}",
            details={"type": type(exc).__name__},
        ) from exc


# ---------------------------------------------------------------------------
# Payload 보강 — normalize() 가 잘 받아들이도록 메타 채우기
# ---------------------------------------------------------------------------
def _augment_doc_payload(
    payload: dict[str, Any],
    data_type: str,
    req: ConvertRequest,
) -> dict[str, Any]:
    """DOC 계열 payload 에 ``id`` / ``data_type`` / 폼 메타를 보강한다.

    converter / ppt / md 변환기들은 ``meta.doc_id`` 에 정식 ID 를 넣는다.
    normalize 는 그것을 읽어 RecordIn.id 로 사용한다. 여기서는 라우터 폼 입력
    (tags / agents / classification / domain) 만 합쳐 넣는다.
    """
    if not isinstance(payload, dict):
        return payload  # 안전망

    payload.setdefault("data_type", data_type)

    # tags / agents / classification / domain 머지 — 입력이 우선.
    if req.tags:
        existing_tags = list(payload.get("tags") or [])
        merged = existing_tags + [t for t in req.tags if t not in existing_tags]
        payload["tags"] = merged
    if req.agents:
        existing_agents = list(payload.get("agents") or [])
        merged_agents = existing_agents + [
            a for a in req.agents if a not in existing_agents
        ]
        payload["agents"] = merged_agents
    if req.classification:
        payload.setdefault("classification", req.classification)
    if req.domain:
        payload.setdefault("domain", req.domain)

    # meta.doc_id 를 top-level id 로 승격 (없을 때만).
    meta = payload.get("meta")
    if isinstance(meta, dict):
        if "id" not in payload and meta.get("doc_id"):
            payload["id"] = meta["doc_id"]
    return payload


def _augment_data_payload(
    payload: dict[str, Any],
    req: ConvertRequest,
) -> dict[str, Any]:
    """DATA 계열 payload 에 폼 메타를 보강한다."""
    if not isinstance(payload, dict):
        return payload

    payload.setdefault("data_type", payload.get("data_type") or "DATA")

    if req.tags:
        existing_tags = list(payload.get("tags") or [])
        merged = existing_tags + [t for t in req.tags if t not in existing_tags]
        payload["tags"] = merged
    if req.agents:
        existing_agents = list(payload.get("agents") or [])
        merged_agents = existing_agents + [
            a for a in req.agents if a not in existing_agents
        ]
        payload["agents"] = merged_agents
    if req.classification:
        payload.setdefault("classification", req.classification)
    if req.domain:
        payload.setdefault("domain", req.domain)

    # data_id → id 보강.
    if "id" not in payload and payload.get("data_id"):
        payload["id"] = payload["data_id"]
    return payload


__all__ = [
    "ConversionFailedError",
    "ConvertRequest",
    "DrmProtectedError",
    "EXTENSION_MAP",
    "PdfNotAvailableError",
    "SourceFormat",
    "UnsupportedFormatError",
    "convert_file",
    "detect_format",
]
