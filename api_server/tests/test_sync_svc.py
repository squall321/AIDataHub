# 외부 소스 동기화(sync_svc) 시험 — 요약 폴백·상세 조회 실패·소스별 매핑 규칙
"""tests/ 에 동기화 변환(transform_record·detail_endpoint)을 다루는 시험이 없었다
(2026-10-15 요청서 K6-4). mx-whitepaper-analyst 카드 427장 중 320장이 원본부터 요약이 비어
제목 한 줄로만 보였다. 소스별 매핑 규칙은 config/sync_sources.yml 에서 읽는다.
"""
from __future__ import annotations

from pathlib import Path

_YML = Path(__file__).resolve().parents[2] / "config" / "sync_sources.yml"


def _yml_rules(name: str) -> dict:
    import yaml

    doc = yaml.safe_load(_YML.read_text(encoding="utf-8"))
    return next(s for s in doc["sources"] if s["name"] == name)["mapping_rules"]


_LONG = "첫 본문 " + "가" * 400
_MXWP_RAW = {  # MXWP DocumentJSON — 첫 절은 제목뿐이고 글은 subsections 에 있다
    "id": "u1", "title": "문서", "summary": "",
    "sections": [{"title": "개요", "blocks": [],
                  "subsections": [{"title": "하위", "blocks": [{"type": "paragraph",
                                                                 "text": _LONG}]}]}],
}


def test_summary_from_body_fills_an_empty_summary():
    from api.services.sync_svc import transform_record

    rules = {"title_field": "title", "summary_field": "summary", "sections_field": "sections",
             "summary_from_body": True}
    out = transform_record(dict(_MXWP_RAW), rules, trust_pii=True)
    assert out.get("summary") == _LONG[:300]


def test_summary_from_body_keeps_a_source_summary():
    from api.services.sync_svc import transform_record

    rules = {"title_field": "title", "summary_field": "summary", "sections_field": "sections",
             "summary_from_body": True}
    out = transform_record({**_MXWP_RAW, "summary": "원본 요약"}, rules, trust_pii=True)
    assert out.get("summary") == "원본 요약"


def test_no_fallback_without_the_rule():
    from api.services.sync_svc import transform_record

    rules = {"title_field": "title", "summary_field": "summary", "sections_field": "sections"}
    out = transform_record(dict(_MXWP_RAW), rules, trust_pii=True)
    assert "summary" not in out  # 다른 소스는 예전 그대로


def test_mxwp_rules_turn_on_the_summary_fallback():
    from api.services.sync_svc import transform_record

    rules = _yml_rules("mxwp")
    assert rules.get("summary_from_body") is True
    out = transform_record(dict(_MXWP_RAW), rules, trust_pii=False)
    assert out.get("summary") == _LONG[:300]
