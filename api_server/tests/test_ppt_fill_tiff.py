# PPT 변환 — 그림으로 채운 도형의 그림을 그림 요소로 꺼내고 TIFF 그림은 PNG 로 바꿔 싣는지 시험(HWAXPortal docs/request-1125 IR-32)
from __future__ import annotations

import io
from pathlib import Path

import pytest

pptx = pytest.importorskip("pptx")
PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402
from pptx.enum.shapes import MSO_SHAPE  # noqa: E402
from pptx.oxml.ns import qn  # noqa: E402
from pptx.util import Inches  # noqa: E402

from ppt_converter.core import PptxConverter, PptxConverterOptions  # noqa: E402


def _img(fmt: str, color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(buf, format=fmt)
    return buf.getvalue()


def _opts(tmp_path: Path) -> PptxConverterOptions:
    return PptxConverterOptions(team="HE", group="CAE", year=2026, seq=1, output_dir=tmp_path / "out")


def _deck(tmp_path: Path) -> Path:
    prs = pptx.Presentation()
    layout = prs.slide_layouts[5]
    # 1쪽 — 그림으로 채운 둥근 사각형(안에 코드 글)
    s1 = prs.slides.add_slide(layout)
    s1.shapes.title.text = "코드 화면"
    box = s1.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(1), Inches(2), Inches(4), Inches(2))
    box.text_frame.text = "int main() { return 0; }"
    _, rid = s1.part.get_or_add_image_part(io.BytesIO(_img("PNG")))
    sp_pr = box._element.spPr
    for child in list(sp_pr):
        if child.tag in (qn("a:solidFill"), qn("a:noFill"), qn("a:gradFill")):
            sp_pr.remove(child)
    fill = sp_pr.makeelement(qn("a:blipFill"), {})
    blip = fill.makeelement(qn("a:blip"), {qn("r:embed"): rid})
    fill.append(blip)
    stretch = fill.makeelement(qn("a:stretch"), {})
    stretch.append(stretch.makeelement(qn("a:fillRect"), {}))
    fill.append(stretch)
    geom = sp_pr.find(qn("a:prstGeom"))
    geom.addnext(fill)
    # 2쪽 — TIFF 그림
    s2 = prs.slides.add_slide(layout)
    s2.shapes.title.text = "TIFF 사진"
    s2.shapes.add_picture(io.BytesIO(_img("TIFF", (10, 120, 10))), Inches(1), Inches(2), Inches(2), Inches(2))
    # 3쪽 — 그냥 도형(그림 채움 없음)은 그림이 아니다
    s3 = prs.slides.add_slide(layout)
    s3.shapes.title.text = "도형만"
    s3.shapes.add_shape(MSO_SHAPE.OVAL, Inches(1), Inches(2), Inches(2), Inches(2)).text_frame.text = "타원 글"
    path = tmp_path / "deck.pptx"
    prs.save(path)
    return path


def test_picture_filled_shape_becomes_figure_and_keeps_text(tmp_path):
    res = PptxConverter(_opts(tmp_path)).convert(_deck(tmp_path))
    secs = {s.title: s for s in res.sections}
    code = secs["코드 화면"]
    kinds = [b.type for b in code.blocks]
    assert "figure" in kinds, "그림으로 채운 도형이 그림 블록이 된다"
    assert any(b.type == "paragraph" and "int main()" in (b.text or "") for b in code.blocks), "도형 안의 글도 그대로"
    fig = next(f for f in res.figures if f.section_ref == code.id)
    assert fig.image_path and (tmp_path / "out" / fig.image_path).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert any(a.kind == "figure" and a.extra.get("figure_ref") == fig.id for a in res.attachments)
    # 그림 채움이 없는 도형은 그림이 아니다
    assert [b.type for b in secs["도형만"].blocks] == ["paragraph"]


def test_tiff_picture_is_stored_as_png(tmp_path):
    res = PptxConverter(_opts(tmp_path)).convert(_deck(tmp_path))
    sec = next(s for s in res.sections if s.title == "TIFF 사진")
    fig = next(f for f in res.figures if f.section_ref == sec.id)
    assert fig.image_path.endswith(".png"), fig.image_path
    with Image.open(tmp_path / "out" / fig.image_path) as im:
        assert im.format == "PNG" and im.size == (8, 6)
    att = next(a for a in res.attachments if a.extra.get("figure_ref") == fig.id)
    assert att.mime_type == "image/png" and att.file_name.endswith(".png")
    assert not [w for w in res.warnings if "TIFF" in w]


def test_broken_tiff_stays_tiff_with_warning(tmp_path):
    conv = PptxConverter(_opts(tmp_path))
    from converter.models import Figure

    blob, ext = conv._tiff_to_png(Figure(id="F", number=1, caption="c", section_ref="1"), b"II*\x00broken", "tiff")
    assert (blob, ext) == (b"II*\x00broken", "tiff")
    assert any("TIFF 를 PNG 로 바꾸지 못해" in w for w in conv.warnings)


# ── 요약 · agent_hints 는 쪽 제목으로(HWAXPortal docs/request-1125 IR-33) ─────────────────────
def test_summary_and_hints_come_from_slide_titles(tmp_path):
    prs = pptx.Presentation()
    for title, body in (("표지 제목", "회사 이름"), ("낙하 시험 결과", "모서리 낙하에서 힌지부 균열이 났다."), ("응력 지도", "최대 응력 320 MPa"),
                        ("낙하 시험 결과", "같은 제목의 둘째 쪽"), ("결론", "마지막 쪽 글 — 예전엔 이런 글 조각이 요약이 됐다")):
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = title
        s.placeholders[1].text_frame.text = body
    path = tmp_path / "t.pptx"
    prs.save(path)
    meta = PptxConverter(_opts(tmp_path)).convert(path).meta
    assert meta["summary"] == "낙하 시험 결과 · 응력 지도 · 결론", meta["summary"]
    assert "N/A" not in meta["agent_hints"] and "주요 토픽: 낙하 시험 결과, 응력 지도, 결론" in meta["agent_hints"]


def test_figure_reading_summaries_join_document_summary(tmp_path):
    from api.services import figure_reading as fr

    payload = {"meta": {"summary": "낙하 시험 결과 · 응력 지도"},
               "sections": [{"id": "1", "blocks": [{"type": "figure", "ref": "F1"}], "children": []}],
               "figures": [{"id": "F1", "number": 1, "caption": "Figure 1: a.png", "section_ref": "1", "image_path": "a.png"}]}
    (tmp_path / "a.png").write_bytes(_img("PNG"))

    import httpx

    def srv(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "종류: 지도\n요약: 최대 응력 320 MPa(힌지)"}}]})

    cfg = fr.VlmSettings(base_url="http://v/v1", model="m", key="", timeout_s=5, max_concurrency=1, max_figures=5, thinking_off=True)
    fr.apply(payload, tmp_path, cfg=cfg, client=httpx.Client(transport=httpx.MockTransport(srv)))
    assert payload["meta"]["summary"] == "낙하 시험 결과 · 응력 지도 / 그림 — 최대 응력 320 MPa(힌지)"
