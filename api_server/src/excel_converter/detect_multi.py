"""Excel 시트의 다중 표(multi-table) 후보 탐지.

휴리스틱:
    1. 시트의 모든 행을 순회.
    2. 비-빈 행이 1개 이상 있는 ``contiguous block`` 으로 묶는다.
       (빈 행 = 모든 셀이 None / 공백).
    3. 각 블록을 후보 표로 본다 — 첫 행이 헤더, 나머지가 데이터로 가정.
    4. 블록이 2개 이상이면 ``has_multi_tables=True``.

이 모듈은 ``XlsxConverter`` 본체에 영향을 주지 않으며, ``--detect-multi-tables``
플래그로 opt-in 활성화한다. CLI 는 결과를 경고 + 대체 변환 단위로 사용한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TableBlock:
    """탐지된 후보 표 블록."""

    start_row: int  # 1-based
    end_row: int    # 1-based, inclusive
    start_col: int  # 1-based
    end_col: int    # 1-based, inclusive
    headers: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)

    @property
    def num_rows(self) -> int:
        return len(self.rows)


@dataclass
class MultiTableReport:
    """``detect_multi_tables`` 결과."""

    has_multi_tables: bool
    blocks: list[TableBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _is_row_empty(row: list[Any]) -> bool:
    for v in row:
        if v is None:
            continue
        if isinstance(v, str) and v.strip() == "":
            continue
        return False
    return True


def _trim_row_columns(rows: list[list[Any]]) -> tuple[list[list[Any]], int, int]:
    """블록 내 모든 행에서 좌·우의 빈 컬럼을 제거.

    Returns:
        (trimmed_rows, first_col_offset_1based, last_col_offset_1based)
    """
    if not rows:
        return rows, 1, 1

    # 가장 넓은 행을 기준으로.
    max_w = max(len(r) for r in rows)
    # 좌측: 첫 비-빈 열 찾기.
    left = max_w
    right = 0
    for r in rows:
        for i, v in enumerate(r):
            if v is None:
                continue
            if isinstance(v, str) and v.strip() == "":
                continue
            if i < left:
                left = i
            if i > right:
                right = i
    if left > right:
        return rows, 1, max_w

    trimmed = [r[left : right + 1] for r in rows]
    # (1-based)
    return trimmed, left + 1, right + 1


def detect_multi_tables(
    rows: list[list[Any]],
    *,
    min_block_rows: int = 2,
) -> MultiTableReport:
    """``rows`` (시트 전체 행 매트릭스) 에서 contiguous block 을 추출.

    Args:
        rows: 시트의 모든 행 매트릭스 (1-based 의미상 ``rows[0]`` = row 1).
        min_block_rows: 블록으로 인정할 최소 행 수 (헤더 + 데이터 1+).

    Returns:
        ``MultiTableReport``.
    """
    blocks: list[TableBlock] = []
    warnings: list[str] = []

    cur_start: int | None = None
    cur_buffer: list[list[Any]] = []

    def _flush(start: int, end: int, buf: list[list[Any]]) -> None:
        if not buf:
            return
        if len(buf) < int(min_block_rows):
            return
        trimmed, scol, ecol = _trim_row_columns(buf)
        # 첫 행은 헤더로.
        header_row = trimmed[0]
        headers = [
            ("" if v is None else str(v).strip())
            for v in header_row
        ]
        data_rows = [list(r) for r in trimmed[1:]]
        blocks.append(
            TableBlock(
                start_row=start,
                end_row=end,
                start_col=scol,
                end_col=ecol,
                headers=headers,
                rows=data_rows,
            )
        )

    for i, row in enumerate(rows, start=1):
        if _is_row_empty(row):
            if cur_start is not None:
                _flush(cur_start, i - 1, cur_buffer)
                cur_start = None
                cur_buffer = []
            continue
        if cur_start is None:
            cur_start = i
            cur_buffer = []
        cur_buffer.append(list(row))

    if cur_start is not None:
        _flush(cur_start, cur_start + len(cur_buffer) - 1, cur_buffer)

    has_multi = len(blocks) >= 2
    if has_multi:
        warnings.append(
            f"시트에 {len(blocks)}개의 표 블록이 발견되었습니다 — "
            "각 블록을 별도 레코드로 변환하는 것을 권장합니다."
        )

    return MultiTableReport(
        has_multi_tables=has_multi,
        blocks=blocks,
        warnings=warnings,
    )


def detect_in_worksheet(ws: Any, *, min_block_rows: int = 2) -> MultiTableReport:
    """openpyxl Worksheet 에서 직접 multi-table 탐지.

    얇은 헬퍼 — 모든 행을 ``ws.iter_rows`` 로 읽어 ``detect_multi_tables`` 에
    위임한다.
    """
    rows: list[list[Any]] = []
    for row in ws.iter_rows(values_only=True):
        rows.append(list(row))
    return detect_multi_tables(rows, min_block_rows=min_block_rows)


__all__ = [
    "MultiTableReport",
    "TableBlock",
    "detect_in_worksheet",
    "detect_multi_tables",
]
