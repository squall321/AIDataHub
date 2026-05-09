"""차트 데이터 추출 — python-pptx ``chart`` API 활용.

각 차트 도형의 ``chart.plots[*].series[*]`` 를 읽어 ``Table`` 형태로 변환한다.

추출 결과 ``Table``:
    - headers = ["category", "<series1.name>", "<series2.name>", ...]
    - rows    = [[cat_label, val1, val2, ...], ...]

지원 차트 종류:
    - 막대 (Bar / Column)
    - 라인
    - 파이 / 도넛 (categories = label, single series)
    - Scatter (XY) — categories 자리에 X 값을 넣는다.

지원되지 않는 차트 종류 (예: 3D, Surface) 는 ``None`` 을 반환한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ChartTable:
    """추출된 차트 데이터 테이블 (변환기 ``Table`` 모델로 매핑)."""

    title: str = ""
    headers: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    chart_type: str = ""

    def is_empty(self) -> bool:
        return not self.headers and not self.rows


def extract_chart_table(shape: Any) -> ChartTable | None:
    """python-pptx 차트 도형에서 ChartTable 을 추출.

    Args:
        shape: ``has_chart`` 가 True 인 GraphicFrame.

    Returns:
        ``ChartTable`` 또는 추출 실패 시 ``None``.
    """
    chart = getattr(shape, "chart", None)
    if chart is None:
        return None

    # 차트 종류 (문자열).
    chart_type = ""
    try:
        ct = getattr(chart, "chart_type", None)
        if ct is not None:
            chart_type = getattr(ct, "name", "") or str(ct)
    except Exception:  # noqa: BLE001
        pass

    # 차트 제목 (옵션).
    title = ""
    try:
        if getattr(chart, "has_title", False):
            tf = getattr(chart.chart_title, "text_frame", None)
            if tf is not None:
                title = (tf.text or "").strip()
    except Exception:  # noqa: BLE001
        pass

    # plots / series 수집.
    series_data: list[tuple[str, list[Any]]] = []
    categories: list[Any] = []

    try:
        plots = list(chart.plots)
    except Exception:  # noqa: BLE001
        return None

    if not plots:
        return None

    # 첫 plot 의 categories 사용 (대부분 1 plot).
    try:
        first_plot = plots[0]
        cats = getattr(first_plot, "categories", None)
        if cats is not None:
            categories = [c for c in cats]
    except Exception:  # noqa: BLE001
        categories = []

    # 모든 plot 의 series 수집.
    for plot in plots:
        try:
            for s in plot.series:
                name = ""
                try:
                    name = (s.name or "").strip()
                except Exception:  # noqa: BLE001
                    pass
                values: list[Any] = []
                try:
                    values = list(s.values or [])
                except Exception:  # noqa: BLE001
                    values = []
                # XY (scatter) 인 경우 x_values 가 있을 수 있다.
                x_values: list[Any] = []
                try:
                    x_values = list(getattr(s, "xy_values", []) or [])
                except Exception:  # noqa: BLE001
                    pass
                if x_values and not categories:
                    # scatter: x 값으로 categories 대체.
                    categories = list(x_values)
                series_data.append((name or f"series{len(series_data) + 1}", values))
        except Exception:  # noqa: BLE001
            continue

    if not series_data:
        return None

    # categories 가 비어있을 수도 있음 (단순 단일 series).
    n_rows = max((len(vs) for _, vs in series_data), default=0)
    if not categories:
        categories = [f"item{i + 1}" for i in range(n_rows)]
    if len(categories) < n_rows:
        # 부족한 카테고리는 인덱스로 채움.
        categories = list(categories) + [
            f"item{i + 1}" for i in range(len(categories), n_rows)
        ]

    headers = ["category"] + [name for name, _ in series_data]
    rows: list[list[Any]] = []
    for i in range(n_rows):
        row: list[Any] = [categories[i] if i < len(categories) else f"item{i + 1}"]
        for _, vs in series_data:
            row.append(vs[i] if i < len(vs) else None)
        rows.append(row)

    return ChartTable(
        title=title,
        headers=headers,
        rows=rows,
        chart_type=chart_type,
    )


__all__ = ["ChartTable", "extract_chart_table"]
