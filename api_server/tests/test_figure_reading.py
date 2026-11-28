# 변환의 그림 판독 시험 — 박스 설정이 있을 때만 그림마다 VLM 판독을 캡션 · 본문 블록에 싣는지(HWAXPortal docs/request-1125 IR-31)
"""가짜 VLM(httpx.MockTransport)으로 본다 — 실제 서버 · 네트워크에 닿지 않는다. 확인하는 것은
- 꺼짐(기본) — 설정이 없으면 결과가 글자 그대로다.
- 켜짐 — 파일 이름 캡션이 판독 요약이 되고, 그 그림 블록 바로 뒤에 판독 전문 글 블록이 서고, 첨부 캡션도 같다. 사람이 단 캡션은 뒤에 요약.
- 같은 그림은 한 번만 · 상한을 넘는 그림은 건너뜀 · 서버 오류는 그 그림만 · 그림을 못 받는 서버는 나머지를 건너뜀 · 키는 결과에 없음.
- MCP run_convert 길에서 실제 pptx 가 판독 블록을 얻고 그 글이 절 본문(_blocks_to_text)에 든다.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
from pathlib import Path

import httpx
import pytest

from api.services import figure_reading as fr

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

_READ = ("종류: 그래프\n요약: 막대 4개 — B 가 82 로 가장 높다\n① 그림 종류: 막대 그래프\n② 축 이름: x 시료, y 강도(MPa)\n"
         "③ 범례: 없음\n④ 수치: A 40, B 82, C 55, D 61\n⑤ 그림 안의 글자: t1_bar")


def _png(color=(10, 20, 30), size=(6, 4)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _cfg(**kw) -> fr.VlmSettings:
    base = dict(base_url="http://vlm.test/v1", model="vlm-x", key="SECRET-KEY", timeout_s=10, max_concurrency=2,
                max_figures=40, thinking_off=True)
    base.update(kw)
    return fr.VlmSettings(**base)


class _Server:
    def __init__(self, reply=_READ, status=200, body_text=""):
        self.calls, self.reply, self.status, self.body_text = [], reply, status, body_text

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer SECRET-KEY"
        if self.status != 200:
            return httpx.Response(self.status, text=self.body_text)
        return httpx.Response(200, json={"choices": [{"message": {"content": self.reply}}]})


def _payload(tmp_path: Path, figs: list[tuple[str, str, bytes | None]]) -> dict:
    """figs = [(그림 id, 캡션, 바이트)] — 모두 절 '1' 의 그림 블록."""
    out = tmp_path / "out"
    (out / "DOC").mkdir(parents=True, exist_ok=True)
    figures, blocks, atts = [], [{"type": "paragraph", "text": "앞 글"}], []
    for n, (fid, cap, blob) in enumerate(figs, 1):
        f = {"id": fid, "number": n, "caption": cap, "section_ref": "1"}
        if blob is not None:
            (out / "DOC" / f"F{n:03d}.png").write_bytes(blob)
            f["image_path"] = f"DOC/F{n:03d}.png"
        figures.append(f)
        blocks.append({"type": "figure", "ref": fid})
        atts.append({"id": f"A{n}", "kind": "figure", "caption": cap, "section_ref": "1", "extra": {"figure_ref": fid}})
    return {"meta": {"title": "덱"}, "sections": [{"id": "1", "title": "쪽", "blocks": blocks, "children": []}],
            "figures": figures, "attachments": atts}


def test_off_by_default_leaves_payload_unchanged(tmp_path, monkeypatch):
    for k in ("AIDH_VLM_BASE_URL", "AIDH_VLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert fr.settings() is None
    p = _payload(tmp_path, [("F1", "Figure 1: t1_bar.png", _png())])
    before = json.dumps(p, sort_keys=True)
    assert json.dumps(fr.apply(p, tmp_path / "out"), sort_keys=True) == before


def test_reading_goes_to_caption_and_body_block(tmp_path):
    srv = _Server()
    p = _payload(tmp_path, [("F1", "Figure 1: t1_bar.png", _png()), ("F2", "Figure 2: 응력 지도(하중 10 N)", _png((1, 2, 3)))])
    fr.apply(p, tmp_path / "out", cfg=_cfg(), client=httpx.Client(transport=httpx.MockTransport(srv)))
    f1, f2 = p["figures"]
    assert f1["caption"] == "Figure 1: 막대 4개 — B 가 82 로 가장 높다", "파일 이름 캡션은 판독 요약으로"
    assert f2["caption"] == "Figure 2: 응력 지도(하중 10 N) — 막대 4개 — B 가 82 로 가장 높다", "사람이 단 캡션은 뒤에 요약"
    assert p["attachments"][0]["caption"] == f1["caption"]
    blocks = p["sections"][0]["blocks"]
    assert [b["type"] for b in blocks] == ["paragraph", "figure", "paragraph", "figure", "paragraph"]
    text = blocks[2]["text"]
    assert text.startswith("[그림 판독 — Figure 1] ") and "A 40, B 82" in text
    assert "종류: 그래프" not in text and "요약:" not in text and "범례: 없음" not in text, "머리 · 없음 줄은 뺀다"
    assert f1["reading"] == {"summary": "막대 4개 — B 가 82 로 가장 높다", "model": "vlm-x", "prompt_version": fr.PROMPT_VERSION}
    st = p["meta"]["figure_reading"]
    assert (st["figures"], st["read"], st["failed"], st["skipped"]) == (2, 2, 0, 0)
    # 요청 꼴 — 그림은 data URL · 생각 끔 · 같은 지시문
    body = srv.calls[0]
    assert body["chat_template_kwargs"] == {"enable_thinking": False} and body["temperature"] == 0
    assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "요약: " in body["messages"][1]["content"][0]["text"]
    assert "SECRET-KEY" not in json.dumps(p, ensure_ascii=False)
    # 판독 글이 절 본문이 된다(검색이 찾는 글)
    from api.ingest.db_writer import _blocks_to_text

    assert "B 82" in _blocks_to_text(blocks)


def test_same_image_once_cap_and_missing_file(tmp_path):
    srv = _Server()
    img = _png()
    p = _payload(tmp_path, [("F1", "Figure 1: a.png", img), ("F2", "Figure 2: b.png", img), ("F3", "Figure 3: c.png", _png((9, 9, 9))),
                            ("F4", "Figure 4: d.png", None)])
    fr.apply(p, tmp_path / "out", cfg=_cfg(max_figures=1), client=httpx.Client(transport=httpx.MockTransport(srv)))
    assert len(srv.calls) == 1, "같은 그림은 한 번 · 상한 1"
    st = p["meta"]["figure_reading"]
    assert (st["read"], st["skipped"], st["reasons"]) == (2, 2, {"cap": 1, "no_file": 1})
    assert p["figures"][2]["caption"] == "Figure 3: c.png" and "reading" not in p["figures"][2]


@pytest.mark.parametrize(("status", "text", "kind"), [(503, "busy", "http_5xx"), (400, "model does not support image input", "no_vision"),
                                                      (400, "bad", "http_4xx")])
def test_server_errors_leave_figure_unread(tmp_path, status, text, kind):
    srv = _Server(status=status, body_text=text)
    p = _payload(tmp_path, [("F1", "Figure 1: a.png", _png()), ("F2", "Figure 2: b.png", _png((5, 5, 5)))])
    fr.apply(p, tmp_path / "out", cfg=_cfg(max_concurrency=1), client=httpx.Client(transport=httpx.MockTransport(srv)))
    st = p["meta"]["figure_reading"]
    assert st["read"] == 0 and st["failed"] == 2 and st["reasons"] == {kind: 2}
    assert [b["type"] for b in p["sections"][0]["blocks"]] == ["paragraph", "figure", "figure"]
    if kind == "no_vision":
        assert len(srv.calls) == 1, "그림을 못 받는 서버면 나머지를 보내지 않는다"


def test_unreadable_format_is_reported_not_sent(tmp_path):
    srv = _Server()
    p = _payload(tmp_path, [("F1", "Figure 1: a.emf", b"\x01\x00\x00\x00 not an image")])
    fr.apply(p, tmp_path / "out", cfg=_cfg(), client=httpx.Client(transport=httpx.MockTransport(srv)))
    assert srv.calls == [] and p["meta"]["figure_reading"]["reasons"] == {"format": 1}


def test_big_image_is_shrunk_before_sending(tmp_path):
    srv = _Server()
    p = _payload(tmp_path, [("F1", "Figure 1: big.png", _png(size=(4000, 100)))])
    fr.apply(p, tmp_path / "out", cfg=_cfg(), client=httpx.Client(transport=httpx.MockTransport(srv)))
    import base64

    url = srv.calls[0]["messages"][1]["content"][1]["image_url"]["url"]
    with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as im:
        assert max(im.size) == fr.LONG_SIDE


# ── MCP run_convert 길 ─────────────────────────────────────────────
@pytest.fixture()
def inbox(tmp_path, monkeypatch):
    from api.config import settings
    from api.services import mcp_write_svc as mw

    box = tmp_path / "inbox"
    box.mkdir()
    monkeypatch.setenv("AIDH_CONVERT_INBOX", str(box))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(settings, "auth_required", False, raising=False)

    @contextlib.asynccontextmanager
    async def _no_db():
        yield None

    monkeypatch.setattr(mw, "SessionLocal", _no_db)
    return box


@pytest.mark.asyncio
async def test_run_convert_reads_figures_when_configured(inbox, monkeypatch):
    pptx = pytest.importorskip("pptx")
    from pptx.util import Inches

    from api.services import mcp_write_svc as mw

    prs = pptx.Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "막대 그래프"
    s.shapes.add_picture(io.BytesIO(_png()), Inches(1), Inches(1), Inches(2), Inches(2))
    prs.save(inbox / "deck.pptx")

    srv = _Server()
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: real(transport=httpx.MockTransport(srv)))
    for k, v in (("AIDH_VLM_BASE_URL", "http://vlm.test/v1"), ("AIDH_VLM_MODEL", "vlm-x"), ("AIDH_VLM_API_KEY", "SECRET-KEY")):
        monkeypatch.setenv(k, v)
    out = await mw.run_convert(file="deck.pptx")
    assert out["status"] == "converted", out
    rec = out["record"]
    assert rec["figures"][0]["caption"] == "Figure 1: 막대 4개 — B 가 82 로 가장 높다"
    assert rec["meta"]["figure_reading"]["read"] == 1
    texts = [b.get("text", "") for sec in rec["sections"] for b in sec["blocks"]]
    assert any(t.startswith("[그림 판독 — Figure 1]") for t in texts)
    # 끄면 예전 그대로
    monkeypatch.delenv("AIDH_VLM_BASE_URL")
    plain = await mw.run_convert(file="deck.pptx")
    assert "figure_reading" not in plain["record"]["meta"] and len(srv.calls) == 1
