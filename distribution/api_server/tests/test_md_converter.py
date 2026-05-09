"""Markdown(.md) → DOC JSON 변환기 단위 테스트.

작은 in-memory MD 문자열로 핵심 동작을 검증한다.
"""
from __future__ import annotations

import textwrap

import pytest

from md_converter.core import MarkdownConverter, MarkdownConverterOptions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def opts(tmp_path):
    return MarkdownConverterOptions(
        division="HE",
        team="CAE",
        year=2026,
        seq=1,
        output_dir=tmp_path,
    )


def _convert(md: str, opts: MarkdownConverterOptions):
    """MD 텍스트를 변환해 (result, dict-payload) 반환."""
    converter = MarkdownConverter(opts)
    result = converter.convert_text(textwrap.dedent(md), source_file="sample.md")
    return result, result.to_dict()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_basic_headings(opts):
    md = """\
        # Title

        Top body.

        ## Sub

        Sub body.

        ### Sub-sub

        Deepest body.
    """
    result, payload = _convert(md, opts)

    assert result.meta["doc_id"] == "DOC-HE-CAE-2026-000001"
    assert result.meta["source_format"] == "md"
    # 최상위 sections 1개 (Title)
    assert len(result.sections) == 1
    title = result.sections[0]
    assert title.level == 1
    assert title.title == "Title"
    # title 의 children: Sub
    assert len(title.children) == 1
    sub = title.children[0]
    assert sub.level == 2
    assert sub.title == "Sub"
    # sub 의 children: Sub-sub
    assert len(sub.children) == 1
    sub_sub = sub.children[0]
    assert sub_sub.level == 3
    assert sub_sub.title == "Sub-sub"

    # toc 는 level 3 까지 모두 포함
    toc_titles = [t["title"] for t in payload["toc"]]
    assert toc_titles == ["Title", "Sub", "Sub-sub"]


def test_paragraph_with_inline_link(opts):
    md = """\
        # Doc

        See [the spec](https://example.com/spec) for details.
    """
    result, _ = _convert(md, opts)
    sec = result.sections[0]
    paragraphs = [b for b in sec.blocks if b.type == "paragraph"]
    assert len(paragraphs) == 1
    assert paragraphs[0].text == "See [the spec](https://example.com/spec) for details."


def test_code_fence_with_lang(opts):
    md = """\
        # Code

        ```python
        def hello():
            print("world")
        ```
    """
    result, _ = _convert(md, opts)
    sec = result.sections[0]
    codes = [b for b in sec.blocks if b.type == "code"]
    assert len(codes) == 1
    assert "def hello():" in codes[0].text
    assert codes[0].marker == "lang:python"


def test_table_extracted(opts):
    md = """\
        # Data

        | name | qty | unit |
        | ---- | --- | ---- |
        | bolt | 12  | ea   |
        | nut  | 24  | ea   |
    """
    result, payload = _convert(md, opts)
    assert len(result.tables) == 1
    tbl = result.tables[0]
    assert tbl.headers == ["name", "qty", "unit"]
    assert tbl.rows == [["bolt", "12", "ea"], ["nut", "24", "ea"]]
    # section 의 table_refs 에 등록됨
    sec = result.sections[0]
    assert tbl.id in sec.table_refs
    # blocks 흐름에 table ref 블록 존재
    table_blocks = [b for b in sec.blocks if b.type == "table"]
    assert len(table_blocks) == 1
    assert table_blocks[0].ref == tbl.id


def test_image_to_attachment_with_caption_from_alt_text(opts):
    md = """\
        # Figures

        ![브라켓 응력 분포](bracket.png)

        Some narrative text.

        ![](https://example.com/external.jpg)
    """
    result, _ = _convert(md, opts)
    assert len(result.figures) == 2
    assert len(result.attachments) == 2

    # 1번 그림: alt 가 캡션이 됨, 상대 경로 → file_path 로 보존
    fig1 = result.figures[0]
    att1 = result.attachments[0]
    assert fig1.caption == "브라켓 응력 분포"
    assert att1.kind == "figure"
    assert att1.caption == "브라켓 응력 분포"
    assert att1.file_path == "bracket.png"
    assert "url" not in att1.extra
    assert att1.extra.get("figure_ref") == fig1.id

    # 2번 그림: alt 없음 → 캡션 누락 경고, 절대 URL → extra.url 로
    fig2 = result.figures[1]
    att2 = result.attachments[1]
    assert "캡션 누락" in fig2.caption
    assert att2.extra.get("url") == "https://example.com/external.jpg"
    assert att2.file_path is None
    # 캡션 누락 경고가 들어 있어야 함
    assert any("alt" in w or "캡션 누락" in w for w in result.warnings)


def test_yaml_front_matter_to_meta(opts):
    md = """\
        ---
        title: KooRemapper IGA Guide
        summary: 본 가이드는 KooRemapper의 IGA 기능 사용법을 설명한다.
        tags: [IGA, NURBS, KooRemapper]
        agents: [iga-analyst, doc-curator]
        classification: internal
        status: draft
        ---

        # 1. 개요

        본문 시작.
    """
    result, _ = _convert(md, opts)
    meta = result.meta
    assert meta["title"] == "KooRemapper IGA Guide"
    assert meta["summary"].startswith("본 가이드는")
    assert meta["tags"] == ["IGA", "NURBS", "KooRemapper"]
    assert meta["agent_scope"] == ["iga-analyst", "doc-curator"]
    # 예약어 외 front matter 키는 front_matter_extra 로 보존
    extra = meta.get("front_matter_extra", {})
    assert extra.get("classification") == "internal"
    assert extra.get("status") == "draft"
    # 첫 헤딩이 "1. 개요" → section_id "1", title "개요"
    assert result.sections[0].id == "1"
    assert result.sections[0].title == "개요"


def test_blockquote_marker(opts):
    md = """\
        # Quotes

        > 인용된 한 문장.
    """
    result, _ = _convert(md, opts)
    blocks = result.sections[0].blocks
    paras = [b for b in blocks if b.type == "paragraph"]
    assert len(paras) == 1
    assert paras[0].marker == "> "
    assert paras[0].text == "인용된 한 문장."


def test_lists_ordered_and_unordered(opts):
    md = """\
        # Lists

        - apple
        - banana

        1. first
        2. second
    """
    result, _ = _convert(md, opts)
    blocks = result.sections[0].blocks
    items = [b for b in blocks if b.type == "list_item"]
    assert [b.marker for b in items] == ["•", "•", "1.", "2."]
    assert [b.text for b in items] == ["apple", "banana", "first", "second"]


def test_caption_pattern_replaces_missing_caption(opts):
    md = """\
        # Figs

        ![](image.png)

        Figure 1: 정확한 설명
    """
    result, _ = _convert(md, opts)
    fig = result.figures[0]
    assert "Figure 1" in fig.caption
    assert "정확한 설명" in fig.caption


def test_doc_id_format(opts):
    md = "# Hello\n\nbody.\n"
    result, _ = _convert(md, opts)
    # DOC-{div}-{team}-{year}-{seq:06d}
    assert result.meta["doc_id"] == "DOC-HE-CAE-2026-000001"
    fig_md = "# X\n\n![alt](a.png)\n"
    converter = MarkdownConverter(opts)
    r2 = converter.convert_text(fig_md, source_file="x.md")
    assert r2.figures[0].id == "DOC-HE-CAE-2026-000001-F001"
    assert r2.attachments[0].id == "DOC-HE-CAE-2026-000001-A001"
