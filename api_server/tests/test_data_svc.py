# DATA 표 집계 순수 함수(data_svc.aggregate) 단위 테스트.
"""services/data_svc.aggregate 검증 — MCP data_aggregate / REST 가 공유하는 집계 수학.

순수 함수라 DB 불필요. op(avg/max/min/sum/count) · group_by · where · 에러 케이스.
"""
from __future__ import annotations

import pytest

from api.services import data_svc

_H = ["region", "value"]
_ROWS = [["A", 10], ["A", 20], ["B", 30], ["B", 50]]


def test_avg():
    r = data_svc.aggregate(headers=_H, units=None, rows=_ROWS, op="avg", column="value")
    assert r["result"] == 27.5
    assert r["rows_considered"] == 4


def test_max_min_sum():
    assert data_svc.aggregate(headers=_H, units=None, rows=_ROWS, op="max", column="value")["result"] == 50
    assert data_svc.aggregate(headers=_H, units=None, rows=_ROWS, op="min", column="value")["result"] == 10
    assert data_svc.aggregate(headers=_H, units=None, rows=_ROWS, op="sum", column="value")["result"] == 110


def test_count_no_column():
    r = data_svc.aggregate(headers=_H, units=None, rows=_ROWS, op="count")
    assert r["result"] == 4


def test_group_by():
    r = data_svc.aggregate(headers=_H, units=None, rows=_ROWS, op="max", column="value", group_by="region")
    got = {row["region"]: row["max_value"] for row in r["result"]}
    assert got == {"A": 20.0, "B": 50.0}
    assert r["groups"] == 2


def test_where_prefilter():
    r = data_svc.aggregate(headers=_H, units=None, rows=_ROWS, op="sum", column="value", where="region:A")
    assert r["result"] == 30  # 10+20 만
    assert r["rows_considered"] == 2


def test_string_numbers_coerced():
    rows = [["A", "10.5"], ["A", "20.5"]]  # 문자열 숫자
    r = data_svc.aggregate(headers=_H, units=None, rows=rows, op="avg", column="value")
    assert r["result"] == 15.5


def test_non_numeric_ignored():
    rows = [["A", "abc"], ["A", 10]]  # 비숫자 셀은 제외
    r = data_svc.aggregate(headers=_H, units=None, rows=rows, op="avg", column="value")
    assert r["result"] == 10.0


def test_unit_passthrough():
    r = data_svc.aggregate(headers=_H, units=[None, "MPa"], rows=_ROWS, op="avg", column="value")
    assert r["unit"] == "MPa"


def test_bad_op_raises():
    with pytest.raises(ValueError, match="op must be"):
        data_svc.aggregate(headers=_H, units=None, rows=_ROWS, op="median", column="value")


def test_missing_column_for_non_count_raises():
    with pytest.raises(ValueError, match="requires 'column'"):
        data_svc.aggregate(headers=_H, units=None, rows=_ROWS, op="avg")


def test_unknown_column_raises():
    with pytest.raises(ValueError, match="unknown column"):
        data_svc.aggregate(headers=_H, units=None, rows=_ROWS, op="avg", column="nope")


def test_unknown_group_by_raises():
    with pytest.raises(ValueError, match="unknown group_by"):
        data_svc.aggregate(headers=_H, units=None, rows=_ROWS, op="max", column="value", group_by="nope")


def test_bad_where_format_raises():
    with pytest.raises(ValueError, match="column:value"):
        data_svc.aggregate(headers=_H, units=None, rows=_ROWS, op="sum", column="value", where="noColon")


def test_empty_rows_returns_none():
    r = data_svc.aggregate(headers=_H, units=None, rows=[], op="avg", column="value")
    assert r["result"] is None
