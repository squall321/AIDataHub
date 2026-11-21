# 박스 명령 sync-sources(정기 동기화 원천을 보고 · 끄고 · 켠다)가 이름을 먼저 확인하고 그 박스의 행만 바꾸는지
"""정기 동기화 원천(sync_sources)은 박스마다 따로 있는 행이다 — 조각 동기화가 나르지 않는다. dev 는 원천 앱에서 직접 당기고,
cae00 은 내부 포트 가드에 막혀 실패만 쌓였다(HWAXPortal docs/leftovers-1121 L21-6 ④ · L21-9). 그래서 끄는 일은 이행이 아니라 박스에서
도는 명령이다 — 이행으로 끄면 원천이 사는 dev 까지 꺼진다.

여기서 고정하는 것.
  · list 는 읽기만 하고 비밀(api_key) · 주소(base_url)를 내지 않는다.
  · disable · enable 은 이름을 **먼저 다 확인**한다 — 하나라도 없으면 아무것도 바꾸지 않는다(종료 2).
  · 이미 그 상태면 그대로다(멱등). 바꾼 것과 그대로인 것을 갈라 말한다.
"""
import asyncio
import json

import pytest

pytest.importorskip("aiosqlite")

from api.db import base as db_base  # noqa: E402
from api.db.models import SyncSource  # noqa: E402
from api.scripts import sync_sources as cli  # noqa: E402


class _Engine:
    async def dispose(self):
        return None


async def _seed(maker):
    async with maker() as s:
        for name, on, cron, st, err in (("signalforge-gs", True, "*/30 * * * *", "blocked", "base_url unsafe: non-standard port blocked: 18000"),
                                        ("mxwp", True, "*/30 * * * *", "error", "HTTPStatusError: 401"),
                                        ("materialtwin", False, "0 */6 * * *", "ok", None)):
            s.add(SyncSource(name=name, base_url=f"http://10.0.0.9:8800/{name}", api_key="sk-secret-do-not-print",
                             list_endpoint="/list", mapping_rules={}, schedule_cron=cron, enabled=on,
                             last_status=st, last_error=err))
        await s.commit()


async def _state(maker) -> dict:
    from sqlalchemy import select

    async with maker() as s:
        return {r.name: r.enabled for r in (await s.execute(select(SyncSource))).scalars()}


@pytest.fixture()
def box(monkeypatch, test_session_maker):
    asyncio.run(_seed(test_session_maker))
    monkeypatch.setattr(db_base, "SessionLocal", test_session_maker)
    monkeypatch.setattr(db_base, "engine", _Engine())
    return test_session_maker


def test_list_는_읽기만_하고_비밀과_주소를_내지_않는다(box, capsys):
    assert cli.main(["list"]) == 0
    raw = capsys.readouterr().out
    # 값(비밀 · 주소)도 칸 이름도 없다. 오류 글의 'base_url unsafe: …' 는 사유이지 주소가 아니다.
    assert "sk-secret" not in raw and "10.0.0.9" not in raw and '"api_key"' not in raw and '"base_url"' not in raw
    rows = {r["name"]: r for r in json.loads(raw)["sources"]}
    assert set(rows) == {"signalforge-gs", "mxwp", "materialtwin"}
    assert rows["signalforge-gs"] == {"name": "signalforge-gs", "enabled": True, "schedule_cron": "*/30 * * * *",
                                      "last_status": "blocked", "last_sync_at": None,
                                      "last_error": "base_url unsafe: non-standard port blocked: 18000"}
    assert rows["materialtwin"]["enabled"] is False
    assert asyncio.run(_state(box)) == {"signalforge-gs": True, "mxwp": True, "materialtwin": False}


def test_disable_는_고른_것만_끄고_이미_꺼진_것은_그대로라고_말한다(box, capsys):
    assert cli.main(["disable", "mxwp", "materialtwin"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"action": "disable", "changed": ["mxwp"], "unchanged": ["materialtwin"]}
    assert asyncio.run(_state(box)) == {"signalforge-gs": True, "mxwp": False, "materialtwin": False}
    assert cli.main(["disable", "mxwp"]) == 0, "두 번 불러도 같다"
    assert json.loads(capsys.readouterr().out) == {"action": "disable", "changed": [], "unchanged": ["mxwp"]}


def test_enable_로_되돌린다(box, capsys):
    assert cli.main(["enable", "materialtwin"]) == 0
    assert json.loads(capsys.readouterr().out) == {"action": "enable", "changed": ["materialtwin"], "unchanged": []}
    assert asyncio.run(_state(box))["materialtwin"] is True


def test_없는_이름이_섞이면_아무것도_바꾸지_않는다(box, capsys):
    """오타 하나 때문에 일부만 꺼지면 '껐다' 고 믿은 원천이 계속 돈다."""
    assert cli.main(["disable", "mxwp", "signalforge_gs"]) == 2
    cap = capsys.readouterr()
    assert "signalforge_gs" in cap.err and "signalforge-gs" in cap.err, "있는 이름을 함께 알려 준다"
    assert asyncio.run(_state(box)) == {"signalforge-gs": True, "mxwp": True, "materialtwin": False}


def test_이름_없이_부르면_인자_오류다(box):
    with pytest.raises(SystemExit) as e:
        cli.main(["disable"])
    assert e.value.code == 2
