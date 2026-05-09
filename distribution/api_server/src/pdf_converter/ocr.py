"""Optional OCR support for scanned PDFs.

Wraps :mod:`pytesseract` (which itself shells out to the system ``tesseract``
binary). Both are optional dependencies. If either is missing, ``ocr_pdf``
returns ``None`` and the caller falls back gracefully.

Detection heuristic:
    A page is considered a "scanned" page when its text-extraction returns
    an empty / near-empty string. ``ocr_pdf`` lets the caller decide which
    pages to OCR — typical usage is to OCR pages whose ``len(text) < 5``.

System dependencies (Windows):
    1. Install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki
       (defaults to ``C:\\Program Files\\Tesseract-OCR\\``).
    2. Add the install dir to ``PATH`` or set ``pytesseract.tesseract_cmd``.
    3. Install language packs (e.g. ``kor.traineddata``) if needed.

This module never raises on import failure — callers test for ``None``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def is_available() -> bool:
    """``pytesseract`` 와 ``pdf2image``/``Pillow`` 가 import 가능한지."""
    try:  # pragma: no cover — env-dependent
        import pytesseract  # noqa: F401
    except Exception:
        return False
    try:
        from PIL import Image  # noqa: F401
    except Exception:
        return False
    return True


def is_tesseract_binary_present() -> bool:
    """tesseract 시스템 바이너리가 실제로 실행 가능한지 확인."""
    try:  # pragma: no cover — env-dependent
        import pytesseract  # type: ignore[import-not-found]

        version = pytesseract.get_tesseract_version()
        return bool(version)
    except Exception:
        return False


def is_blank_page_text(text: str | None, *, min_chars: int = 5) -> bool:
    """페이지 텍스트가 OCR 후보일 만큼 비어있는지."""
    if text is None:
        return True
    return len(text.strip()) < int(min_chars)


def ocr_image(image: Any, *, lang: str = "eng") -> str:
    """단일 ``PIL.Image`` 객체에 대해 OCR 수행.

    Returns:
        추출된 텍스트 (줄바꿈 포함). 의존성/바이너리 누락 시 빈 문자열.
    """
    try:  # pragma: no cover — env-dependent
        import pytesseract  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("pytesseract not installed — OCR skipped")
        return ""
    try:
        return pytesseract.image_to_string(image, lang=lang) or ""
    except Exception as exc:  # noqa: BLE001  (env: missing binary, lang pack…)
        logger.warning("OCR failed: %s", exc)
        return ""


def ocr_pdf(
    pdf_path: str | Path,
    *,
    lang: str = "eng",
    dpi: int = 200,
    only_pages: set[int] | None = None,
) -> dict[int, str] | None:
    """PDF 의 (선택된) 페이지를 이미지로 렌더링한 뒤 OCR 수행.

    Args:
        pdf_path: 입력 PDF 경로.
        lang: tesseract 언어 코드 (예: ``"eng"``, ``"kor"``, ``"eng+kor"``).
        dpi: 페이지 렌더링 DPI (기본 200).
        only_pages: 1-based 페이지 번호 집합. ``None`` 이면 모든 페이지.

    Returns:
        ``{page_no: text}`` 매핑, 또는 의존성/바이너리가 없으면 ``None``.
    """
    if not is_available():
        logger.info("OCR dependencies missing — skipping")
        return None

    try:  # pragma: no cover — env-dependent
        from pdf2image import convert_from_path  # type: ignore[import-not-found]
    except ImportError:
        logger.info("pdf2image not installed — OCR skipping (install poppler+pdf2image)")
        return None

    try:
        images = convert_from_path(str(pdf_path), dpi=dpi)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf2image failed (poppler missing?): %s", exc)
        return None

    out: dict[int, str] = {}
    for idx, img in enumerate(images, start=1):
        if only_pages is not None and idx not in only_pages:
            continue
        out[idx] = ocr_image(img, lang=lang)
    return out


__all__ = [
    "is_available",
    "is_blank_page_text",
    "is_tesseract_binary_present",
    "ocr_image",
    "ocr_pdf",
]
