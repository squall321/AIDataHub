# 인앱 스케줄러 회차의 페이지 한도 — 기본 500 · AIDH_SYNC_MAX_PAGES 손잡이 · 스케줄러 호출이 그 값을 넘기는지
"""왜 — 한도(run_sync 기본 50)에 걸린 회차는 커서를 저장하지 않아 다음 회차도 처음부터 돈다. 그래서 페이지가 한도보다 많은 원천은
끝쪽이 영영 안 받아졌다(2026-11-23 dev — materialtwin 57쪽, 51 ~ 57쪽의 재료 카드가 옛 본문 그대로, 로그 'hit max_pages=50' 회차마다)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from api.services import sync_svc

SRC = Path(sync_svc.__file__).resolve().parents[1]


def test_기본은_500_이고_수동_창구_상한과_같다(monkeypatch):
    monkeypatch.delenv("AIDH_SYNC_MAX_PAGES", raising=False)
    assert sync_svc.scheduled_max_pages() == 500
    route = (SRC / "routes/sync.py").read_text(encoding="utf-8")
    assert "max_pages: int = Query(50, ge=1, le=500)" in route, "수동 창구 상한이 바뀌면 스케줄러 기본도 같이 본다"


@pytest.mark.parametrize("raw, want", [("120", 120), ("1", 1), ("5000", 5000), (" 80 ", 80)])
def test_손잡이를_적으면_그_값이다(monkeypatch, raw, want):
    monkeypatch.setenv("AIDH_SYNC_MAX_PAGES", raw)
    assert sync_svc.scheduled_max_pages() == want


@pytest.mark.parametrize("raw", ["0", "-3", "5001", "abc", "1e3", "50.5"])
def test_꼴이_틀린_값은_기본값이다(monkeypatch, caplog, raw):
    monkeypatch.setenv("AIDH_SYNC_MAX_PAGES", raw)
    assert sync_svc.scheduled_max_pages() == 500
    assert "AIDH_SYNC_MAX_PAGES" in caplog.text


def test_스케줄러가_그_한도를_넘긴다():
    """main._run_due_syncs 의 run_sync 호출이 max_pages=sync_svc.scheduled_max_pages() 를 넘긴다 — 빠지면 기본 50 으로 돌아간다."""
    tree = ast.parse((SRC / "main.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run_due_syncs")
    calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "run_sync"]
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert "max_pages" in kw, "스케줄러 호출에 max_pages 가 없다 — run_sync 기본 50 으로 돈다"
    v = kw["max_pages"]
    assert isinstance(v, ast.Call) and getattr(v.func, "attr", "") == "scheduled_max_pages"
