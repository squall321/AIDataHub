# DRM(NASCA) 문서를 변환기 앞에서 알아보고 DRM_PROTECTED 로 따로 알리는지 시험(10-30 요청 AD-03).
"""변환 앞 DRM 머리 판정.

- 머리 표는 HWAX 포털 ``docAttach.ts`` 의 ``isEncryptedOffice`` 와 같아야 한다
  (PK · OLE · NASCA · %PDF).
- NASCA 머리 → ``convert_file`` 이 ``DrmProtectedError``(422 · DRM_PROTECTED), REST 도 같은 봉투,
  MCP ``run_convert`` 는 ``code: drm_protected`` 와 할 일. 임시 폴더는 만들지 않는다.
- HWAX 포털은 MCP 결과가 ``status: error`` 면 ``error`` 칸만 사용자 화면에 낸다
  (포털 ``backend/app/agent/upload.py`` mcp_call → ``app/procedures/judge.py``
  short_error — 첫 줄을 220자에서 자른다). 그래서 ``error`` 칸이 한 줄 · 220자 안의
  '무엇이 + 할 일' 이어야 한다. 할 일은 형식을 따른다 — PC 도우미는 Excel · md 를
  열지 않는다(포털 ``hwax-doc-extract.ps1``).
- 정상 docx · pptx 는 그대로 변환되고, 깨진 파일은 종전 일반 오류(convert_failed) 그대로다.

DB 는 쓰지 않는다 — ``run_convert`` 의 인증 세션은 빈 가짜로 바꾼다
(키 없음 · 인증 끔 → DB 를 안 본다).
"""
from __future__ import annotations

import contextlib
import io
import tempfile
from pathlib import Path

import pytest

from api.services import mcp_write_svc as mw
from api.services.converter_dispatch import (
    ConvertRequest,
    DrmProtectedError,
    SourceFormat,
    _encrypted_head,
    convert_file,
)

# 포털 docAttach.ts DRM_HEAD 와 같은 바이트(10-25 PC 시험 파일의 머리). 판정은 이 앞 9바이트만 본다.
NASCA = bytes([0x3C, 0x23, 0x23, 0x20, 0x4E, 0x41, 0x53, 0x43, 0x41])
OLE = bytes([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1])
DRM_FILE = NASCA + bytes(range(256)) * 4
OLD_GENERIC = "파일이 손상됐거나 변환기가 처리 못하는 구조일 수 있습니다."
PORTAL_ERROR_MAX = 220  # 포털 judge.short_error 가 자르는 길이


def _make_pptx_bytes() -> bytes:
    pptx = pytest.importorskip("pptx")
    prs = pptx.Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "낙하 시험 결과"
    s.placeholders[1].text_frame.text = "모서리 낙하에서 힌지부 균열."
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _make_docx_bytes() -> bytes:
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph("개요", style="Heading 1")
    doc.add_paragraph("변환 시험용 본문입니다.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ── 머리 표 — 포털과 같은 판정 ─────────────────────────────────────
@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"PK\x03\x04" + b"\x00" * 12, None),      # zip — 정상 OOXML
        (OLE + b"\x00" * 8, "OLE"),                 # OLE 복합문서 — 암호화 또는 구형
        (DRM_FILE, "NASCA"),                        # 사내 DRM 머리
        (b"%PDF-1.7\n" + b"\x00" * 8, None),        # PDF — 평문
        (b"# hello\n", None),                       # 모르는 머리 — 변환기에 맡긴다
        (NASCA[:5], None),                          # 머리가 덜 왔다
        (b"<#", None),                              # 4바이트 미만 — 모른다
    ],
    ids=["zip", "ole", "nasca", "pdf", "text", "nasca-cut", "under-4"],
)
def test_encrypted_head_matches_portal_table(tmp_path, head, expected):
    p = tmp_path / "f.bin"
    p.write_bytes(head)
    assert _encrypted_head(p) == expected


@pytest.mark.parametrize(("name", "head"), [("ole", OLE[:4]), ("nasca", NASCA)])
def test_encrypted_head_needs_every_prefix_byte(tmp_path, name, head):
    # 포털은 OLE 4바이트 · NASCA 9바이트를 다 본다 — 한 바이트만 달라도 암호화가 아니다
    # (판정을 앞 몇 바이트로 줄이면 여기서 걸린다)
    p = tmp_path / "f.bin"
    for i in range(len(head)):
        b = bytearray(head + b"\x00" * 16)
        b[i] ^= 0xFF
        p.write_bytes(bytes(b))
        assert _encrypted_head(p) is None, f"{name} 머리 {i}번째 바이트가 달라도 암호화로 봤다"


# ── convert_file · REST ───────────────────────────────────────────
def test_convert_file_raises_drm_protected_before_temp_dir(tmp_path):
    src = tmp_path / "drm.pptx"
    src.write_bytes(DRM_FILE)
    out = tmp_path / "out"
    req = ConvertRequest(team="HE", group="CAE", year=2026, output_dir=out)
    with pytest.raises(DrmProtectedError) as ei:
        convert_file(src, SourceFormat.PPTX, req)
    exc = ei.value
    assert exc.code == "DRM_PROTECTED" and exc.status_code == 422
    assert exc.details == {"header": "NASCA"}
    assert "DRM(NASCA)" in exc.message and "PC 도우미" in exc.message
    assert "손상" not in exc.message
    assert not out.exists(), "변환기 앞에서 거르면 출력 폴더를 만들지 않는다"


@pytest.mark.asyncio
async def test_convert_route_drm_protected_envelope(test_client, tmp_path, monkeypatch):
    from api.config import settings

    monkeypatch.setattr(settings, "upload_temp_dir", tmp_path / "uploads", raising=False)
    files = {"file": ("drm.pptx", DRM_FILE, "application/octet-stream")}
    form = {"team": "HE", "group": "CAE", "year": "2026", "seq": "1"}
    resp = await test_client.post("/api/convert/", files=files, data=form)
    assert resp.status_code == 422, resp.text
    err = resp.json()["error"]
    assert err["code"] == "DRM_PROTECTED"
    assert err["details"] == {"header": "NASCA"}
    assert "DRM(NASCA)" in err["message"]


# ── MCP run_convert ───────────────────────────────────────────────
@pytest.fixture()
def inbox(tmp_path, monkeypatch) -> Path:
    """inbox · 변환 임시 폴더를 tmp_path 아래로, 인증 세션은 DB 없는 가짜로."""
    from api.config import settings

    box = tmp_path / "inbox"
    box.mkdir()
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setenv("AIDH_CONVERT_INBOX", str(box))
    # run_convert 는 output_dir 없이 mkdtemp 를 쓴다 — /tmp 에 남기지 않게 tmp_path 아래로
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setattr(settings, "auth_required", False, raising=False)

    @contextlib.asynccontextmanager
    async def _no_db():
        yield None

    monkeypatch.setattr(mw, "SessionLocal", _no_db)
    return box


@pytest.mark.asyncio
async def test_run_convert_reports_drm_protected(inbox):
    (inbox / "설계검토.pptx").write_bytes(DRM_FILE)
    out = await mw.run_convert(file="설계검토.pptx")
    assert out["status"] == "error"
    assert out["code"] == "drm_protected"
    assert out["recoverable"] is False
    # 포털 화면에 닿는 것은 error 칸뿐이다 — 무엇이 · 할 일이 거기 있어야 한다
    err = out["error"]
    assert "DRM(NASCA)" in err and "'PC 의 PowerPoint 로 열어 가져오기'(PC 도우미)" in err
    assert "\n" not in err and len(err) <= PORTAL_ERROR_MAX
    assert out["suggestion"] == err
    assert "손상" not in err
    assert str(inbox) not in str(out), "서버 inbox 경로를 알림에 싣지 않는다"


@pytest.mark.asyncio
async def test_run_convert_ole_head_is_drm_protected(inbox):
    (inbox / "old.docx").write_bytes(OLE + b"\x00" * 512)
    out = await mw.run_convert(file="old.docx")
    assert out["code"] == "drm_protected"
    assert "구형 형식" in out["error"]
    assert "다시 저장" in out["error"], "암호만 걸렸거나 구형이면 다시 저장하는 쉬운 길을 말한다"


@pytest.mark.parametrize("head", [NASCA, OLE], ids=["nasca", "ole"])
@pytest.mark.parametrize(
    ("ext", "todo"),
    [
        ("pptx", "'PC 의 PowerPoint 로 열어 가져오기'(PC 도우미)"),
        ("docx", "'PC 의 Word 로 열어 가져오기'(PC 도우미)"),
        ("pdf", "'PC 의 Word 로 열어 가져오기'(PC 도우미)"),   # 포털 단추도 PDF 는 Word 다
        ("xlsx", "복사해 챗에 붙여 넣으세요"),                   # PC 도우미는 Excel 을 열지 않는다
        ("md", "복사해 챗에 붙여 넣으세요"),
    ],
    ids=["pptx", "docx", "pdf", "xlsx", "md"],
)
@pytest.mark.asyncio
async def test_drm_todo_follows_format(inbox, ext, todo, head):
    name = f"원본.{ext}"
    (inbox / name).write_bytes(head + bytes(range(256)) * 4)
    out = await mw.run_convert(file=name)
    err = out["error"]
    assert out["code"] == "drm_protected"
    assert todo in err
    if ext in ("xlsx", "md"):
        assert "PC 도우미" not in err, "PC 도우미가 열지 않는 형식에 그 길을 권하지 않는다"
    assert ("다시 저장" in err) == (head == OLE)
    assert "\n" not in err and len(err) <= PORTAL_ERROR_MAX, "포털이 첫 줄 220자에서 자른다"


@pytest.mark.parametrize(
    ("name", "make"), [("demo.pptx", _make_pptx_bytes), ("demo.docx", _make_docx_bytes)]
)
@pytest.mark.asyncio
async def test_run_convert_plain_office_still_converts(inbox, name, make):
    (inbox / name).write_bytes(make())
    out = await mw.run_convert(file=name)
    assert out["status"] == "converted", out
    assert out["record"].get("sections")


@pytest.mark.asyncio
async def test_run_convert_broken_file_keeps_generic_error(inbox):
    (inbox / "broken.pptx").write_bytes(b"this is not a zip at all" + b"\x00" * 64)
    out = await mw.run_convert(file="broken.pptx")
    assert out["code"] == "convert_failed"
    assert out["suggestion"] == OLD_GENERIC
