# 전문가 삭제 API 가 키 · 권한 없이는 지우지 못하는지 보는 시험(HWAXPortal docs/request-1125 R-01 — P0)
"""DB 없이 돈다 — 세션은 비운 가짜, 권한은 실제 의존성(require_api_key → get_principal → resolve_principal)을 거친다."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import dependencies as deps
from api.db.base import get_session
from api.errors import register_exception_handlers
from api.routes import agents as agents_route


async def _no_session():
    yield None


def _client(monkeypatch, principal: deps.Principal | None = None, deleted: list | None = None) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(agents_route.router)
    app.dependency_overrides[get_session] = _no_session
    if principal is not None:
        app.dependency_overrides[deps.get_principal] = lambda: principal

    async def _fake_delete(session, agent_type, changed_by=None):
        if deleted is not None:
            deleted.append((agent_type, changed_by))
        return True
    monkeypatch.setattr(agents_route.agent_svc, "delete_agent", _fake_delete)
    return TestClient(app)


@pytest.mark.parametrize("auth_required", [False, True])
def test_키_없는_삭제는_401(monkeypatch, auth_required):
    monkeypatch.setattr(deps.settings, "auth_required", auth_required)
    deleted: list = []
    r = _client(monkeypatch, deleted=deleted).delete("/api/agents/he-calc-laminate")
    assert r.status_code == 401, r.text
    assert deleted == [], "키 없는 호출이 삭제까지 갔다"


def test_다른_전문가_스코프의_키는_403(monkeypatch):
    p = deps.Principal(key_id=7, name="other", agent_scopes=["he-other"], is_anonymous=False)
    deleted: list = []
    r = _client(monkeypatch, p, deleted).delete("/api/agents/he-calc-laminate")
    assert r.status_code == 403, r.text
    assert deleted == []


@pytest.mark.parametrize("scopes", [["*"], ["he-calc-laminate"]])
def test_관리자나_주인_키는_지운다(monkeypatch, scopes):
    p = deps.Principal(key_id=9, name="ops", agent_scopes=scopes, is_anonymous=False)
    deleted: list = []
    r = _client(monkeypatch, p, deleted).delete("/api/agents/he-calc-laminate", headers={"X-User-Id": "admin@x"})
    assert r.status_code == 204, r.text
    assert deleted == [("he-calc-laminate", "admin@x")]


def test_부트스트랩_키는_지운다(monkeypatch):
    p = deps.Principal(name="bootstrap", agent_scopes=["*"], is_anonymous=False, is_bootstrap=True)
    assert agents_route.may_delete_agent(p, "x")
