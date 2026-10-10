# list_records 요약과 list_tags 총수 — 요약을 200자에서 말없이 자르던 것, total 이 자른 뒤 건수이던 것의 회귀 시험
"""DB 없이 돈다(aiosqlite 가 없는 venv 에서도 공식 시험에서 실행된다).

- list_records(MCP·AIDataHub 챗)의 요약은 ``record_query_svc.to_summary`` 가 만든다. 예전엔
  ``summary[:200]`` 이라 '…' 도 길이도 없이 끊겼다. 본문(content)을 빼는 것이 토큰 절약이고,
  요약 자체를 자를 까닭은 1백만 토큰 창에 없다.
- list_tags 의 ``total`` 은 잘라 낸 뒤의 ``len(items)`` 였다 — '전체' 처럼 보이는데 실은 반환
  건수라 더 있는지 알 수 없었다.
"""
from __future__ import annotations

import contextlib

import pytest


def test_to_summary_keeps_whole_summary():
    from api.db.models import Record
    from api.services.record_query_svc import to_summary

    long = "카드 요약 문장. " * 40 + "끝-ZQS"
    rec = Record(id="DOC-HE-CAE-2026-0000000999", data_type="DOC", team="HE", group="CAE",
                 year=2026, seq=999, title="t", summary=long, tags=["a"], agents=["x"],
                 content={"raw": "본문"})
    out = to_summary(rec)
    assert out["summary"] == long, f"요약이 {len(out['summary'])}자에서 잘렸다(원문 {len(long)}자)"
    assert "content" not in out          # 종전과 같다 — 본문은 여전히 빼고 준다


@pytest.mark.asyncio
async def test_list_tags_total_is_before_the_cut(monkeypatch):
    from api import mcp_runtime
    from api.routes import taxonomy

    bucket = {f"tag{i:03d}": {"count": 1000 - i, "data_types": {"DOC": 1}, "agents": set()}
              for i in range(30)}

    async def _fake_aggregate(_session):
        return bucket

    @contextlib.asynccontextmanager
    async def _no_db():
        yield None

    monkeypatch.setattr(taxonomy, "_aggregate_tags", _fake_aggregate)
    monkeypatch.setattr(mcp_runtime, "SessionLocal", _no_db)

    out = await mcp_runtime.list_tags(limit=5)
    assert len(out["items"]) == 5 and out["items"][0]["tag"] == "tag000"
    assert out["total"] == 30, f"total={out['total']} — 자른 뒤 건수다"
    assert out["count"] == 5

    big = await mcp_runtime.list_tags(limit=1000)
    assert big["count"] == 30 and big["total"] == 30
