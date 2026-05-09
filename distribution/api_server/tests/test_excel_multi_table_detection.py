"""S8. Excel multi-table detection (heuristic)."""
from __future__ import annotations

import io
from pathlib import Path

import pytest


def test_detect_two_blocks_in_rows() -> None:
    from excel_converter.detect_multi import detect_multi_tables

    rows = [
        ["A", "B"],
        [1, 2],
        [3, 4],
        [None, None],   # blank separator
        ["X", "Y", "Z"],
        [10, 20, 30],
        [11, 21, 31],
    ]
    rep = detect_multi_tables(rows)
    assert rep.has_multi_tables is True
    assert len(rep.blocks) == 2
    b1, b2 = rep.blocks
    assert b1.headers == ["A", "B"]
    assert b2.headers == ["X", "Y", "Z"]
    assert b1.num_rows == 2
    assert b2.num_rows == 2


def test_single_block_returns_no_multi() -> None:
    from excel_converter.detect_multi import detect_multi_tables

    rows = [
        ["k", "v"],
        [1, 2],
        [3, 4],
    ]
    rep = detect_multi_tables(rows)
    assert rep.has_multi_tables is False
    assert len(rep.blocks) == 1


def test_only_blank_returns_empty() -> None:
    from excel_converter.detect_multi import detect_multi_tables

    rows = [[None, None], [None, None]]
    rep = detect_multi_tables(rows)
    assert rep.has_multi_tables is False
    assert rep.blocks == []


def test_detect_in_worksheet(tmp_path) -> None:
    """openpyxl Worksheet 에서 동작."""
    openpyxl = pytest.importorskip("openpyxl")

    from excel_converter.detect_multi import detect_in_worksheet

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "data"
    ws["A1"] = "h1"
    ws["B1"] = "h2"
    ws["A2"] = 1
    ws["B2"] = 2
    # blank row 3
    ws["A4"] = "j1"
    ws["B4"] = "j2"
    ws["C4"] = "j3"
    ws["A5"] = 9
    ws["B5"] = 8
    ws["C5"] = 7

    rep = detect_in_worksheet(ws)
    assert rep.has_multi_tables is True
    assert len(rep.blocks) == 2


def test_min_block_rows_filter() -> None:
    """``min_block_rows=2`` 이면 단일 행 블록은 무시된다."""
    from excel_converter.detect_multi import detect_multi_tables

    rows = [
        ["solo"],
        [None],
        ["A", "B"],
        [1, 2],
    ]
    rep = detect_multi_tables(rows, min_block_rows=2)
    # solo 블록은 데이터 행이 없으므로(헤더만 있음) 1행 → 제외.
    assert len(rep.blocks) == 1
