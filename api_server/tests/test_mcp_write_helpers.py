# mcp_write_svc 순수 헬퍼(_scan_missing / _suggest / _classify_error) 단위 테스트.
"""데이터 주입 되묻기·제안·에러봉투 로직 검증 (DB 불필요).

_scan_missing: 부족 필수 필드를 한 번에 수집.
_suggest: title/data_type 제안 (confidence 동반).
_classify_error: _import_one 에러 문자열 → 표준 코드.
"""
from __future__ import annotations

from api.services import mcp_write_svc as mw


# ── _scan_missing ─────────────────────────────────────────────────
def test_scan_missing_all_when_bare_table():
    # id 없으면 auto_seq 채번에 data_type/team/group/year 전부 필요 + title.
    rec = {"content": {"headers": ["a"], "rows": [[1]]}}
    missing = mw._scan_missing(rec)
    assert set(missing) == {"title", "data_type", "team", "group", "year"}


def test_scan_missing_only_title_when_meta_and_year_present():
    rec = {"data_type": "DATA", "team": "HE", "group": "CAE", "year": 2026, "content": {}}
    assert mw._scan_missing(rec) == ["title"]


def test_scan_missing_none_when_id_present():
    # id 있으면 data_type/team/group/year 는 id 에서 파싱 → title 만 본다
    rec = {"id": "DATA-HE-CAE-2026-0000000001", "title": "t", "content": {}}
    assert mw._scan_missing(rec) == []


def test_scan_missing_empty_when_complete():
    rec = {"title": "t", "data_type": "DATA", "team": "HE", "group": "CAE", "year": 2026, "content": {}}
    assert mw._scan_missing(rec) == []


# ── _suggest ──────────────────────────────────────────────────────
def test_suggest_title_from_caption_high():
    rec = {"content": {"caption": "SUS304 인장", "headers": ["strain"], "rows": []}}
    s = mw._suggest(rec)
    assert s["title"]["suggested"] == "SUS304 인장"
    assert s["title"]["confidence"] == "high"


def test_suggest_title_from_headers_low():
    rec = {"content": {"headers": ["strain", "stress"], "rows": []}}
    s = mw._suggest(rec)
    assert s["title"]["confidence"] == "low"  # 헤더 기반은 낮은 신뢰
    assert "strain" in s["title"]["suggested"]


def test_suggest_skips_garbage_unnamed_headers():
    rec = {"content": {"headers": ["Unnamed: 0", "Unnamed: 1"], "rows": []}}
    s = mw._suggest(rec)
    assert "title" not in s  # 쓰레기 헤더면 title 제안 안 함


def test_suggest_data_type_from_structure():
    assert mw._suggest({"content": {"headers": ["a"], "rows": [[1]]}})["data_type"]["suggested"] == "DATA"
    assert mw._suggest({"content": {"sections": [{}]}})["data_type"]["suggested"] == "DOC"


# ── _classify_error ───────────────────────────────────────────────
def test_classify_missing_title():
    e = mw._classify_error({"error": "title is required"})
    assert e["code"] == "missing_title" and e["recoverable"] is True


def test_classify_auto_seq_fields():
    e = mw._classify_error({"error": "auto_seq needs 'team' (along with data_type/team/group/year)"})
    assert e["code"] == "missing_id_fields"


def test_classify_team_not_found_unrecoverable():
    e = mw._classify_error({"error": "team 'ZZ' is not registered or inactive"})
    assert e["code"] == "team_not_found" and e["recoverable"] is False


def test_classify_duplicate():
    e = mw._classify_error({"error": "integrity error: duplicate key"})
    assert e["code"] == "duplicate"


def test_classify_unknown_falls_back():
    e = mw._classify_error({"error": "something weird"})
    assert e["code"] == "import_error" and e["recoverable"] is False
