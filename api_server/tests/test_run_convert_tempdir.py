# MCP run_convert 가 변환기의 추출 폴더(그림 · 첨부)를 payload 를 만든 뒤 지우는지 시험(HWAXPortal docs/request-1125 AD-02)
"""'이 대화에서 분석' 은 '어디에도 저장하지 않았습니다' 라고 말한다 — 변환기가 꺼낸 그림이 박스 /tmp 에
ai_data_convert_* 로 기한 없이 남으면 그 말이 거짓이 된다. REST 변환(routes/convert)은 이미 지운다.

DB 는 쓰지 않는다 — 인증 세션은 빈 가짜(키 없음 · 인증 끔)."""
from __future__ import annotations

import contextlib
import io
import tempfile
from pathlib import Path

import pytest

from api.services import mcp_write_svc as mw

# 1×1 PNG
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def _pptx_with_picture() -> bytes:
    pptx = pytest.importorskip("pptx")
    from pptx.util import Inches

    prs = pptx.Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "응력 지도"
    s.shapes.add_picture(io.BytesIO(_PNG), Inches(1), Inches(1), Inches(2), Inches(2))
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


@pytest.fixture()
def inbox(tmp_path, monkeypatch) -> tuple[Path, Path]:
    from api.config import settings

    box = tmp_path / "inbox"
    box.mkdir()
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setenv("AIDH_CONVERT_INBOX", str(box))
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))     # 임시 폴더를 tmp_path 아래로 — 남은 것을 센다
    monkeypatch.setattr(settings, "auth_required", False, raising=False)

    @contextlib.asynccontextmanager
    async def _no_db():
        yield None

    monkeypatch.setattr(mw, "SessionLocal", _no_db)
    return box, scratch


@pytest.mark.asyncio
async def test_run_convert_leaves_no_temp_folder(inbox):
    box, scratch = inbox
    (box / "deck.pptx").write_bytes(_pptx_with_picture())
    out = await mw.run_convert(file="deck.pptx")
    assert out["status"] == "converted", out
    assert out["record"]["figures"], "그림이 꺼내졌어야 이 시험이 뜻이 있다"
    assert list(scratch.iterdir()) == [], "변환 뒤 임시 폴더가 남지 않는다"


@pytest.mark.asyncio
async def test_run_convert_cleans_up_on_failure(inbox):
    box, scratch = inbox
    (box / "broken.pptx").write_bytes(b"this is not a zip at all" + b"\x00" * 64)
    out = await mw.run_convert(file="broken.pptx")
    assert out["code"] == "convert_failed"
    assert list(scratch.iterdir()) == [], "변환이 실패해도 임시 폴더가 남지 않는다"
