"""S6. PDF OCR module tests.

The actual OCR path requires the ``pytesseract`` Python package + a
``tesseract`` system binary + ``pdf2image`` + ``poppler``. We exercise only
the safe fallbacks here: the helpers that detect availability and the
:func:`is_blank_page_text` heuristic. Real OCR is skipped when dependencies
are missing.
"""
from __future__ import annotations

import pytest


def test_is_blank_page_text_basic():
    from pdf_converter.ocr import is_blank_page_text

    assert is_blank_page_text(None) is True
    assert is_blank_page_text("") is True
    assert is_blank_page_text("   \n  ") is True
    assert is_blank_page_text("a") is True   # < min_chars (5)
    assert is_blank_page_text("abcdef") is False


def test_is_available_returns_bool():
    from pdf_converter.ocr import is_available

    # 의존성 유무와 무관하게 True/False 만 반환해야 한다 (예외 없음).
    val = is_available()
    assert isinstance(val, bool)


def test_ocr_pdf_returns_none_if_unavailable(tmp_path):
    """의존성 없을 때 ``ocr_pdf`` 가 ``None`` 을 반환하는지."""
    from pdf_converter import ocr as ocr_mod

    if ocr_mod.is_available():
        pytest.skip("OCR deps installed; cannot validate fallback path")

    p = tmp_path / "fake.pdf"
    p.write_bytes(b"%PDF-1.4\n%fake\n")
    assert ocr_mod.ocr_pdf(p) is None
