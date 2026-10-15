# 외부 소스 동기화(sync_svc) 시험 — 요약 폴백·상세 조회 실패·소스별 매핑 규칙
"""tests/ 에 동기화 변환(transform_record·detail_endpoint)을 다루는 시험이 없었다
(2026-10-15 요청서 K6-4). mx-whitepaper-analyst 카드 427장 중 320장이 원본부터 요약이 비어
제목 한 줄로만 보였다. 소스별 매핑 규칙은 config/sync_sources.yml 에서 읽는다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

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


# ---------------------------------------------------------------------------
# 상세 조회 실패 — 목록 응답(메타뿐)으로 변환해 있던 본문을 빈 「본문」 한 절로 덮던 것.
# ---------------------------------------------------------------------------
_RID = "DOC-MX-WP-2026-0000000109"
_SRC = "mxwp-t"
_RULES = {"id_field": "id", "title_field": "title", "summary_field": "summary",
          "sections_field": "sections", "data_type": "DOC", "team": "MX", "group": "WP",
          "doc_type": "whitepaper", "agents": ["mx-whitepaper-analyst"]}
_OLD_BODY = {"sections": [{"section_id": "1", "level": 1, "title": "개요",
                           "content_text": "원래 본문"}]}


async def _seed_mxwp(session) -> int:
    from api.db.models import Agent, ExternalIdMap, OrgTeam, Record, SyncSource

    session.add(OrgTeam(code="MX", name="MX"))
    session.add(Agent(agent_type="mx-whitepaper-analyst", name="MX 백서", description="백서",
                      common_tags=[], data_types=["DOC"]))
    session.add(Record(id=_RID, data_type="DOC", team="MX", group="WP", year=2026, seq=109,
                       title="문서", summary="", tags=[], agents=["mx-whitepaper-analyst"],
                       content=_OLD_BODY))
    await session.flush()
    session.add(ExternalIdMap(source=_SRC, external_id="u1", record_id=_RID))
    src = SyncSource(name=_SRC, base_url="http://127.0.0.1:8800",
                     list_endpoint="/api/v1/documents", detail_endpoint="/api/v1/documents/{slug}",
                     cursor_param="offset", since_param="_unsupported_updated_since",
                     limit_param="limit", page_size=100, max_rps=1000.0, retry_max=0,
                     retry_backoff_sec=0.0, trust_pii_masked=False, enabled=True,
                     mapping_rules=_RULES)
    session.add(src)
    await session.commit()
    return src.id


def _serve(monkeypatch, detail):
    """목록은 메타 한 건, 상세는 detail(요청) 이 정한다 — 실제 네트워크는 쓰지 않는다."""
    import httpx

    from api.services import sync_svc

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/documents":
            return httpx.Response(200, json={"data": [{"id": "u1", "slug": "s1", "title": "문서",
                                                       "summary": ""}], "meta": {}})
        return detail(req)

    real = httpx.AsyncClient
    monkeypatch.setattr(sync_svc.httpx, "AsyncClient",
                        lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setenv("AIDH_SYNC_ALLOW_INTERNAL", "true")


def _boom(req):
    import httpx

    raise httpx.ConnectError("detail down", request=req)


@pytest.mark.asyncio
@pytest.mark.parametrize("detail", [lambda req: __import__("httpx").Response(500), _boom],
                         ids=["status-500", "connect-error"])
async def test_detail_failure_does_not_overwrite_the_body(test_session, monkeypatch, detail):
    from sqlalchemy import select

    from api.db.models import Record, SyncRun
    from api.services.sync_svc import run_sync

    sid = await _seed_mxwp(test_session)
    _serve(monkeypatch, detail)
    out = await run_sync(test_session, sid)
    assert out["failed"] == 1 and out["status"] == "partial", out
    assert out["imported"] == 0 and out["updated"] == 0
    rec = (await test_session.execute(select(Record).where(Record.id == _RID))).scalar_one()
    assert rec.content == _OLD_BODY  # 있던 본문 그대로 — 빈 「본문」 한 절로 덮지 않는다
    run = (await test_session.execute(select(SyncRun).where(SyncRun.source_id == sid))).scalar_one()
    assert run.status == "partial"
    assert len(run.dead_letter) == 1 and "detail fetch" in run.dead_letter[0]["error"]


@pytest.mark.asyncio
async def test_detail_success_still_updates_the_body(test_session, monkeypatch):
    import httpx
    from sqlalchemy import select

    from api.db.models import Record
    from api.services.sync_svc import run_sync

    new = [{"title": "개요", "blocks": [{"type": "paragraph", "text": "새 본문"}]}]
    sid = await _seed_mxwp(test_session)
    _serve(monkeypatch, lambda req: httpx.Response(200, json={"data": {
        "id": "u1", "title": "문서", "summary": "", "content": {"sections": new}}}))
    out = await run_sync(test_session, sid)
    assert out["failed"] == 0 and out["updated"] == 1, out
    rec = (await test_session.execute(select(Record).where(Record.id == _RID))).scalar_one()
    assert rec.content["sections"] == new
