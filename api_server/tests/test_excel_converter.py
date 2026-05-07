"""excel_converter 모듈 테스트.

신규 옵션 검증:
    - test_start_cell_offset             : --start-cell A5 로 표 좌상단 보정
    - test_skip_blank_rows               : --skip-blank-rows 로 빈 행 제거
    - test_irregular_detection_suggests_start_cell : 자동 탐지가 시작 셀 제안

기존 동작 회귀 검증:
    - test_basic_per_sheet_conversion
    - test_infer_units
    - test_merged_cells_replicated
"""
from __future__ import annotations

from pathlib import Path

import pytest

# openpyxl 미설치 환경 대비 module-level skip
openpyxl = pytest.importorskip("openpyxl")

from excel_converter import (  # noqa: E402  (after importorskip)
    XlsxConverter,
    XlsxConverterOptions,
    detect_irregular,
    parse_cell_address,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_xlsx(tmp_path: Path, sheets: dict[str, list[list]]) -> Path:
    """sheets dict({sheet_name: [[cell, ...], ...]}) → 임시 .xlsx 파일."""
    wb = openpyxl.Workbook()
    # 기본 시트 제거
    default = wb.active
    wb.remove(default)

    for name, grid in sheets.items():
        ws = wb.create_sheet(title=name)
        for r_idx, row in enumerate(grid, start=1):
            for c_idx, val in enumerate(row, start=1):
                ws.cell(row=r_idx, column=c_idx, value=val)

    out = tmp_path / "input.xlsx"
    wb.save(out)
    wb.close()
    return out


def _opts(tmp_path: Path, **overrides) -> XlsxConverterOptions:
    """기본 옵션 헬퍼."""
    base = dict(
        division="HE",
        team="CAE",
        year=2026,
        start_seq=1,
        output_dir=tmp_path / "out",
        mode="per_sheet",
    )
    base.update(overrides)
    return XlsxConverterOptions(**base)


# ---------------------------------------------------------------------------
# parse_cell_address
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "addr, expected",
    [
        ("A1", (1, 1)),
        ("A5", (5, 1)),
        ("B5", (5, 2)),
        ("C10", (10, 3)),
        ("AA1", (1, 27)),
        (" b 12 ", (12, 2)),  # whitespace tolerated
    ],
)
def test_parse_cell_address(addr, expected):
    assert parse_cell_address(addr) == expected


@pytest.mark.parametrize("bad", ["", "1A", "A0", "5"])
def test_parse_cell_address_invalid(bad):
    with pytest.raises(ValueError):
        parse_cell_address(bad)


# ---------------------------------------------------------------------------
# Basic conversion (regression)
# ---------------------------------------------------------------------------

def test_basic_per_sheet_conversion(tmp_path):
    xlsx = _make_xlsx(
        tmp_path,
        {
            "측정": [
                ["시간", "하중", "변형률"],
                [0.0, 0.0, 0.0],
                [0.1, 12.5, 0.02],
            ],
        },
    )

    sheets = XlsxConverter(_opts(tmp_path)).convert(xlsx)
    assert len(sheets) == 1
    s = sheets[0]
    assert s.headers == ["시간", "하중", "변형률"]
    assert s.rows == [[0.0, 0.0, 0.0], [0.1, 12.5, 0.02]]
    assert s.data_id == "DATA-HE-CAE-2026-000001"
    assert s.caption == "측정"


def test_infer_units(tmp_path):
    xlsx = _make_xlsx(
        tmp_path,
        {
            "데이터": [
                ["시간(s)", "하중(N)", "변형률(%)"],
                [0.1, 12.5, 0.02],
            ],
        },
    )

    sheets = XlsxConverter(_opts(tmp_path, infer_units=True)).convert(xlsx)
    s = sheets[0]
    assert s.headers == ["시간", "하중", "변형률"]
    assert s.units == ["s", "N", "%"]


def test_merged_cells_replicated(tmp_path):
    """병합 셀의 좌상단 값이 병합 영역 전체에 복제되는지."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "merge"
    ws["A1"] = "이름"
    ws["B1"] = "값"
    ws["A2"] = "그룹A"  # A2:A3 병합 예정
    ws["B2"] = 1
    ws["B3"] = 2
    ws.merge_cells("A2:A3")

    out = tmp_path / "merged.xlsx"
    wb.save(out)
    wb.close()

    sheets = XlsxConverter(_opts(tmp_path)).convert(out)
    s = sheets[0]
    assert s.headers == ["이름", "값"]
    assert s.rows == [["그룹A", 1], ["그룹A", 2]]


# ---------------------------------------------------------------------------
# --start-cell offset
# ---------------------------------------------------------------------------

def test_start_cell_offset(tmp_path):
    """표가 B5 부터 시작하는 시트 — --start-cell B5 로 정확히 추출."""
    grid = [
        ["보고서: 측정 결과", None, None, None],   # row 1: 제목
        [None, None, None, None],                   # row 2: 빈 줄
        ["작성자: tester", None, None, None],       # row 3: 메타
        [None, None, None, None],                   # row 4: 빈 줄
        [None, "시간", "하중", "변형률"],           # row 5: 헤더 (B5 시작)
        [None, 0.0, 0.0, 0.0],                      # row 6
        [None, 0.1, 12.5, 0.02],                    # row 7
        [None, 0.2, 25.0, 0.04],                    # row 8
    ]
    xlsx = _make_xlsx(tmp_path, {"raw": grid})

    sheets = XlsxConverter(
        _opts(tmp_path, start_cell="B5")
    ).convert(xlsx)
    s = sheets[0]
    assert s.headers == ["시간", "하중", "변형률"]
    assert s.rows == [
        [0.0, 0.0, 0.0],
        [0.1, 12.5, 0.02],
        [0.2, 25.0, 0.04],
    ]


def test_start_cell_with_infer_units(tmp_path):
    """--start-cell + --infer-units 조합."""
    grid = [
        ["제목 셀"],
        [None],
        ["시간(s)", "하중(N)"],   # row 3 = 헤더
        [0.1, 12.5],
        [0.2, 25.0],
    ]
    xlsx = _make_xlsx(tmp_path, {"raw": grid})

    sheets = XlsxConverter(
        _opts(tmp_path, start_cell="A3", infer_units=True)
    ).convert(xlsx)
    s = sheets[0]
    assert s.headers == ["시간", "하중"]
    assert s.units == ["s", "N"]
    assert s.rows == [[0.1, 12.5], [0.2, 25.0]]


# ---------------------------------------------------------------------------
# --skip-blank-rows
# ---------------------------------------------------------------------------

def test_skip_blank_rows(tmp_path):
    """데이터 사이의 완전 빈 행을 제거."""
    xlsx = _make_xlsx(
        tmp_path,
        {
            "data": [
                ["a", "b"],
                [1, 2],
                [None, None],   # 빈 행
                [3, 4],
                [None, None],   # 빈 행
                [5, 6],
            ],
        },
    )

    # skip 미사용
    sheets = XlsxConverter(_opts(tmp_path)).convert(xlsx)
    assert sheets[0].rows == [
        [1, 2],
        [None, None],
        [3, 4],
        [None, None],
        [5, 6],
    ]

    # skip_blank_rows 사용
    sheets = XlsxConverter(_opts(tmp_path, skip_blank_rows=True)).convert(xlsx)
    assert sheets[0].rows == [[1, 2], [3, 4], [5, 6]]


def test_skip_blank_rows_independent_of_skip_empty(tmp_path):
    """skip_blank_rows 만 켜도 빈 행이 제거된다."""
    xlsx = _make_xlsx(
        tmp_path,
        {
            "data": [
                ["a", "b"],
                [None, None],
                [1, 2],
            ],
        },
    )

    sheets = XlsxConverter(
        _opts(tmp_path, skip_blank_rows=True, skip_empty=False)
    ).convert(xlsx)
    assert sheets[0].rows == [[1, 2]]


# ---------------------------------------------------------------------------
# Irregular detection
# ---------------------------------------------------------------------------

def test_irregular_detection_suggests_start_cell(tmp_path):
    """detect_irregular 가 B5 같은 시작 셀을 제안한다."""
    xlsx = _make_xlsx(
        tmp_path,
        {
            "raw": [
                ["보고서 제목"],
                [None],
                ["작성자: x"],
                [None],
                [None, "시간", "하중", "변형률"],   # row 5, col B
                [None, 0.1, 12.5, 0.02],
                [None, 0.2, 25.0, 0.04],
            ],
        },
    )

    wb = openpyxl.load_workbook(xlsx)
    ws = wb["raw"]
    report = detect_irregular(ws)
    wb.close()

    assert report.is_irregular is True
    assert report.suggested_start_cell == "B5"
    assert any("header" in r.lower() for r in report.reasons)


def test_irregular_detection_clean_sheet(tmp_path):
    """A1 부터 헤더가 깔끔히 시작하면 비정상 아님."""
    xlsx = _make_xlsx(
        tmp_path,
        {
            "clean": [
                ["a", "b", "c"],
                [1, 2, 3],
                [4, 5, 6],
            ],
        },
    )

    wb = openpyxl.load_workbook(xlsx)
    ws = wb["clean"]
    report = detect_irregular(ws)
    wb.close()

    assert report.is_irregular is False
    assert report.suggested_start_cell is None


def test_irregular_detection_warning_in_warnings_array(tmp_path):
    """변환 시 자동 탐지 결과가 sheet.warnings 에 기록된다."""
    xlsx = _make_xlsx(
        tmp_path,
        {
            "raw": [
                ["제목"],
                [None],
                [None, "시간", "하중"],   # row 3, col B
                [None, 0.1, 12.5],
            ],
        },
    )

    sheets = XlsxConverter(_opts(tmp_path)).convert(xlsx)
    s = sheets[0]
    # start_cell 미지정 + 비정상 → warning 기록
    assert any("irregular" in w.lower() or "suggested" in w.lower() for w in s.warnings)


def test_irregular_detection_silenced_when_start_cell_given(tmp_path):
    """--start-cell 명시 시 자동 탐지 경고 미발생."""
    xlsx = _make_xlsx(
        tmp_path,
        {
            "raw": [
                ["제목"],
                [None, "시간", "하중"],   # B2 헤더
                [None, 0.1, 12.5],
            ],
        },
    )

    sheets = XlsxConverter(
        _opts(tmp_path, start_cell="B2")
    ).convert(xlsx)
    s = sheets[0]
    # irregular 자동 탐지는 비활성 — warnings 가 비었거나 다른 경고만.
    assert not any("looks irregular" in w for w in s.warnings)
    assert s.headers == ["시간", "하중"]
    assert s.rows == [[0.1, 12.5]]


# ---------------------------------------------------------------------------
# Header row warning
# ---------------------------------------------------------------------------

def test_warning_when_header_row_has_empty_cells(tmp_path):
    """헤더 행에 빈 셀이 있으면 경고 기록 + col_N 자동 채움."""
    xlsx = _make_xlsx(
        tmp_path,
        {
            "data": [
                ["a", None, "c"],
                [1, 2, 3],
            ],
        },
    )

    sheets = XlsxConverter(_opts(tmp_path)).convert(xlsx)
    s = sheets[0]
    assert s.headers == ["a", "col_2", "c"]
    assert any("empty cells" in w for w in s.warnings)


# ---------------------------------------------------------------------------
# 원칙 6 — _META / _GLOSSARY (데이터 의미 명시)
# ---------------------------------------------------------------------------

def test_meta_sheet_workbook_level(tmp_path):
    """_META 의 title/tags/agents 가 RecordIn 메타에 반영."""
    xlsx = _make_xlsx(
        tmp_path,
        {
            "_META": [
                ["key", "value"],
                ["title", "브라켓 하중 시험 결과 (2026-04)"],
                ["summary", "100개 시료에 대한 정적 하중 시험 결과."],
                ["tags", "시험,브라켓,하중,2026Q2"],
                ["agents", "material-reviewer,cae-reporter"],
                ["domain", "mechanical-test"],
                ["language", "ko"],
            ],
            "Sheet1": [
                ["시료ID", "최대하중"],
                ["S001", 1250.5],
                ["S002", 1305.0],
            ],
        },
    )

    sheets = XlsxConverter(_opts(tmp_path)).convert(xlsx)
    # _META 는 데이터로 변환되지 않는다.
    assert len(sheets) == 1
    s = sheets[0]
    assert s.source_sheet == "Sheet1"
    assert s.meta_overrides["title"] == "브라켓 하중 시험 결과 (2026-04)"
    assert s.meta_overrides["summary"].startswith("100개 시료")
    assert s.meta_overrides["tags"] == ["시험", "브라켓", "하중", "2026Q2"]
    assert s.meta_overrides["agents"] == ["material-reviewer", "cae-reporter"]
    assert s.meta_overrides["domain"] == "mechanical-test"
    assert s.meta_overrides["language"] == "ko"


def test_meta_sheet_per_sheet_context(tmp_path):
    """_META 의 sheet:Sheet1.method 가 해당 record context 에 반영."""
    xlsx = _make_xlsx(
        tmp_path,
        {
            "_META": [
                ["key", "value"],
                ["title", "측정 결과"],
                ["sheet:Sheet1.description", "시료별 최대 하중 측정"],
                ["sheet:Sheet1.method", "KS B 0814 표준 인장시험"],
                ["sheet:Sheet1.condition", "상온 23±2℃, 습도 50±5%RH"],
                ["sheet:Sheet1.equipment", "UTM-500, 50kN 로드셀"],
                ["sheet:Sheet1.operator", "박지수"],
                ["sheet:Sheet2.description", "응력-변형 곡선 핵심점"],
            ],
            "Sheet1": [
                ["시료ID", "최대하중"],
                ["S001", 1250.5],
            ],
            "Sheet2": [
                ["a", "b"],
                [1, 2],
            ],
        },
    )

    sheets = XlsxConverter(_opts(tmp_path)).convert(xlsx)
    by_name = {s.source_sheet: s for s in sheets}
    assert "Sheet1" in by_name and "Sheet2" in by_name

    s1 = by_name["Sheet1"]
    assert s1.context["description"] == "시료별 최대 하중 측정"
    assert s1.context["method"] == "KS B 0814 표준 인장시험"
    assert s1.context["condition"].startswith("상온")
    assert s1.context["equipment"] == "UTM-500, 50kN 로드셀"
    assert s1.context["operator"] == "박지수"

    s2 = by_name["Sheet2"]
    assert s2.context == {"description": "응력-변형 곡선 핵심점"}


def test_glossary_sheet_column_descriptions(tmp_path):
    """_GLOSSARY 의 description 이 매칭되어 column_descriptions 에 반영."""
    xlsx = _make_xlsx(
        tmp_path,
        {
            "_GLOSSARY": [
                ["column", "description", "unit", "dtype"],
                ["시료ID", "시료 고유 식별자", "-", "string"],
                ["무게", "시료 무게 (시험 직전 측정)", "g", "float"],
                ["최대하중", "시험 중 기록된 최대 하중", "N", "float"],
            ],
            "Sheet1": [
                ["시료ID", "무게", "최대하중"],
                ["S001", 12.34, 1250.5],
                ["S002", 12.40, 1305.0],
            ],
        },
    )

    sheets = XlsxConverter(_opts(tmp_path)).convert(xlsx)
    assert len(sheets) == 1
    s = sheets[0]
    assert s.column_descriptions == {
        "시료ID": "시료 고유 식별자",
        "무게": "시료 무게 (시험 직전 측정)",
        "최대하중": "시험 중 기록된 최대 하중",
    }
    # units_map 은 - 가 아닌 unit 만 보존.
    assert s.units_map == {"무게": "g", "최대하중": "N"}
    # dtype float 힌트로 모두 float 으로 변환되었는지.
    assert all(isinstance(row[1], float) for row in s.rows)
    assert all(isinstance(row[2], float) for row in s.rows)


def test_glossary_unit_overrides_inline(tmp_path):
    """_GLOSSARY 의 unit 이 헤더 인라인 단위와 충돌 시 _GLOSSARY 우선 + 경고."""
    xlsx = _make_xlsx(
        tmp_path,
        {
            "_GLOSSARY": [
                ["column", "description", "unit", "dtype"],
                ["하중", "측정 하중", "kN", ""],
            ],
            "Sheet1": [
                ["하중(N)"],   # 인라인 단위 N
                [1250.0],
                [1305.0],
            ],
        },
    )

    sheets = XlsxConverter(_opts(tmp_path, infer_units=True)).convert(xlsx)
    s = sheets[0]
    assert s.headers == ["하중"]
    # _GLOSSARY 의 kN 이 인라인 N 을 override.
    assert s.units_map.get("하중") == "kN"
    assert s.units == ["kN"]
    # 충돌 경고가 기록되어야 함.
    assert any(
        "kN" in w and "N" in w and ("override" in w.lower() or "overrides" in w.lower())
        for w in s.warnings
    )


def test_workbook_properties_fallback(tmp_path):
    """_META 가 없을 때 빌트인 properties (title/keywords) 가 폴백으로 사용됨."""
    wb = openpyxl.Workbook()
    wb.properties.title = "빌트인 제목"
    wb.properties.description = "빌트인 요약"
    wb.properties.keywords = "alpha, beta, gamma"
    wb.properties.creator = "빌트인 작성자"
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "x"
    ws["B1"] = "y"
    ws["A2"] = 1
    ws["B2"] = 2

    out = tmp_path / "props.xlsx"
    wb.save(out)
    wb.close()

    sheets = XlsxConverter(_opts(tmp_path)).convert(out)
    s = sheets[0]
    assert s.meta_overrides.get("title") == "빌트인 제목"
    assert s.meta_overrides.get("summary") == "빌트인 요약"
    assert s.meta_overrides.get("tags") == ["alpha", "beta", "gamma"]
    assert s.meta_overrides.get("author") == "빌트인 작성자"


def test_meta_sheet_overrides_workbook_properties(tmp_path):
    """_META 의 값이 빌트인 properties 보다 우선 (12장 우선순위)."""
    wb = openpyxl.Workbook()
    wb.properties.title = "빌트인 제목"
    wb.properties.keywords = "old1, old2"

    ws = wb.create_sheet("_META")
    ws["A1"] = "key"
    ws["B1"] = "value"
    ws["A2"] = "title"
    ws["B2"] = "_META 제목"
    ws["A3"] = "tags"
    ws["B3"] = "new1, new2, new3"

    data_ws = wb.active
    data_ws.title = "Sheet1"
    data_ws["A1"] = "x"
    data_ws["A2"] = 1

    out = tmp_path / "merged.xlsx"
    wb.save(out)
    wb.close()

    sheets = XlsxConverter(_opts(tmp_path)).convert(out)
    s = sheets[0]
    # _META 가 빌트인을 override.
    assert s.meta_overrides["title"] == "_META 제목"
    assert s.meta_overrides["tags"] == ["new1", "new2", "new3"]


def test_meta_glossary_sheets_excluded_from_data_output(tmp_path):
    """_META 와 _GLOSSARY 시트는 데이터 시트로 변환되지 않는다."""
    xlsx = _make_xlsx(
        tmp_path,
        {
            "_META": [["key", "value"], ["title", "T"]],
            "_GLOSSARY": [["column", "description", "unit", "dtype"], ["x", "X 컬럼", "-", "int"]],
            "Sheet1": [["x"], [1], [2]],
        },
    )

    sheets = XlsxConverter(_opts(tmp_path)).convert(xlsx)
    assert len(sheets) == 1
    assert sheets[0].source_sheet == "Sheet1"
