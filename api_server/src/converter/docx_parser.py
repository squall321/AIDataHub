"""Word(.docx) 본문을 순회하며 구조 요소를 추출한다.

python-docx는 단락(`<w:p>`)과 표(`<w:tbl>`)를 분리해서 노출하므로,
본문 등장 순서를 보존하기 위해 직접 XML을 순회한다.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass

from docx import Document
from docx.document import Document as DocumentType
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph

logger = logging.getLogger(__name__)

# 캡션 텍스트 패턴
CAPTION_PATTERN = re.compile(
    r"^(Figure|Fig\.?|그림|Table|Tbl\.?|표)\s*(\d+)\s*[:\.\-]\s*(.+)$",
    re.IGNORECASE,
)

# 헤딩 텍스트에서 섹션 번호 추출 (1./1.1/1.1.1 + 끝점 선택, 모두 허용)
SECTION_NUM_PATTERN = re.compile(r"^(\d+(?:\.\d+){0,2})\.?\s+(.+)$")

# 마커 패턴: [DOC_TYPE], [SUMMARY], [TAGS], [AGENT_SCOPE], [SOURCES]
MARKER_PATTERN = re.compile(
    r"^\s*\[(DOC_TYPE|SUMMARY|TAGS|AGENT_SCOPE|SOURCES)\]\s*(.*)$",
    re.DOTALL,
)


@dataclass
class ElementWrapper:
    """Word 본문 요소(단락 또는 표)를 등장 순서대로 추적."""

    kind: str  # 'paragraph' or 'table'
    paragraph: Paragraph | None = None
    table: DocxTable | None = None


def iter_block_items(document: DocumentType) -> Iterator[ElementWrapper]:
    """document body를 순회하며 단락/표를 등장 순서대로 yield."""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield ElementWrapper(kind="paragraph", paragraph=Paragraph(child, document))
        elif child.tag == qn("w:tbl"):
            yield ElementWrapper(kind="table", table=DocxTable(child, document))


def detect_heading_level(paragraph: Paragraph) -> int | None:
    """단락이 Heading 1/2/3인지 판정. base_style을 따라 추적."""
    style = paragraph.style
    visited: set[str] = set()
    while style is not None and style.style_id not in visited:
        visited.add(style.style_id or "")
        sid = style.style_id or ""
        if sid in {"Heading1", "Heading2", "Heading3"}:
            return int(sid[-1])
        style = style.base_style
    return None


def is_caption_paragraph(paragraph: Paragraph) -> bool:
    """단락이 캡션인지 판정 (스타일 또는 텍스트 패턴)."""
    style_id = (paragraph.style.style_id or "") if paragraph.style else ""
    if style_id == "Caption":
        return True
    text = paragraph.text.strip()
    if CAPTION_PATTERN.match(text):
        return True
    return False


def parse_caption(text: str) -> tuple[str, int, str] | None:
    """캡션 텍스트에서 (kind, number, full_caption) 추출.

    kind: 'figure' | 'table'
    number: 그림/표 번호
    full_caption: 정규화된 전체 캡션 (`Figure N: ...` 형태)
    """
    m = CAPTION_PATTERN.match(text.strip())
    if not m:
        return None
    raw_kind = m.group(1).lower()
    if raw_kind in {"figure", "fig", "fig.", "그림"}:
        kind = "figure"
        prefix = "Figure"
    else:
        kind = "table"
        prefix = "Table"
    number = int(m.group(2))
    description = m.group(3).strip()
    full = f"{prefix} {number}: {description}"
    return kind, number, full


def extract_section_id_and_title(heading_text: str) -> tuple[str | None, str]:
    """헤딩 텍스트에서 섹션 번호와 제목 분리."""
    m = SECTION_NUM_PATTERN.match(heading_text.strip())
    if m:
        return m.group(1), m.group(2).strip()
    return None, heading_text.strip()


def extract_marker(text: str) -> tuple[str, str] | None:
    """[DOC_TYPE] manual 같은 마커 추출."""
    m = MARKER_PATTERN.match(text)
    if not m:
        return None
    return m.group(1), m.group(2).strip()


def paragraph_text(paragraph: Paragraph) -> str:
    """단락 텍스트 추출 (run 결합)."""
    return "".join(run.text for run in paragraph.runs).strip()


def extract_table_data(tbl: DocxTable) -> tuple[list[str], list[list[str]]]:
    """표를 (headers, rows)로 변환. 병합된 셀은 같은 값을 반복."""
    rows_text: list[list[str]] = []
    for row in tbl.rows:
        row_data: list[str] = []
        for cell in row.cells:
            text = "\n".join(p.text for p in cell.paragraphs).strip()
            row_data.append(text)
        rows_text.append(row_data)

    if not rows_text:
        return [], []

    # 첫 행이 모두 같은 값이면 병합된 표 제목 → 두 번째 행을 헤더로
    first = rows_text[0]
    if len(set(first)) == 1 and len(rows_text) > 1:
        headers = rows_text[1]
        data_rows = rows_text[2:]
    else:
        headers = first
        data_rows = rows_text[1:]

    return headers, data_rows


def coerce_cell_value(value: str) -> str | int | float | None:
    """셀 값 타입 추론."""
    if not value:
        return None
    s = value.strip()
    if s == "":
        return None
    # 정수
    if re.fullmatch(r"-?\d+", s):
        try:
            return int(s)
        except ValueError:
            pass
    # 실수
    if re.fullmatch(r"-?\d+\.\d+", s):
        try:
            return float(s)
        except ValueError:
            pass
    return s


def has_inline_image(paragraph: Paragraph) -> bool:
    """단락 내부에 그림이 들어있는지 검사."""
    return paragraph._p.find(qn("w:r") + "/" + qn("w:drawing")) is not None or any(
        run._r.findall(qn("w:drawing")) for run in paragraph.runs
    )


# WordprocessingDrawing/Picture relation 추적용 XPath 네임스페이스 토큰.
_REL_NS = (
    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
)
_REL_NS_LINK = (
    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}link"
)


def extract_image_rids(paragraph: Paragraph) -> list[str]:
    """단락 내부의 그림(drawing) 들에 연결된 relationship id 목록을 반환한다.

    다음 두 위치를 모두 살핀다:

    - ``a:blip/@r:embed`` — 일반 인라인 이미지 (PNG/JPEG/GIF)
    - ``v:imagedata/@r:id`` — 일부 vml-fallback (예: WMF/EMF) 의 경우

    찾지 못하면 빈 리스트를 반환한다.
    """
    rids: list[str] = []
    p_el = paragraph._p
    # a:blip 검색 (네임스페이스 와일드카드)
    for blip in p_el.iter(
        "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
    ):
        rid = blip.get(_REL_NS) or blip.get(_REL_NS_LINK)
        if rid and rid not in rids:
            rids.append(rid)
    # v:imagedata fallback
    for imagedata in p_el.iter("{urn:schemas-microsoft-com:vml}imagedata"):
        rid = imagedata.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        if rid and rid not in rids:
            rids.append(rid)
    return rids


# 등폭 폰트 후보 (소문자 비교)
MONOSPACE_FONTS = frozenset(
    f.lower()
    for f in (
        "Consolas",
        "Courier",
        "Courier New",
        "Monaco",
        "Lucida Console",
        "Menlo",
        "DejaVu Sans Mono",
        "D2Coding",
        "맑은 고딕 mono",
        "나눔고딕코딩",
        "NanumGothicCoding",
        "Source Code Pro",
        "Fira Code",
        "JetBrains Mono",
    )
)

# 박스 그리기 문자 (ASCII 다이어그램에 자주 등장)
BOX_DRAWING_CHARS = frozenset(
    "┌┐└┘│─├┤┬┴┼"
    "╭╮╯╰"
    "═║╔╗╚╝╠╣╦╩╬"
    "▶◀▲▼"
    "↓↑→←"
)


def is_monospace_paragraph(paragraph: Paragraph) -> bool:
    """단락의 모든 run이 등폭 폰트인지 검사. 빈 run은 무시."""
    has_text = False
    for run in paragraph.runs:
        if not run.text.strip():
            continue
        has_text = True
        font_name = run.font.name
        if not font_name:
            return False
        if font_name.lower() not in MONOSPACE_FONTS:
            return False
    return has_text


def has_box_drawing(text: str) -> bool:
    """텍스트에 박스 그리기 문자가 포함되어 있는지 검사."""
    return any(c in BOX_DRAWING_CHARS for c in text)


def looks_like_code(paragraph: Paragraph) -> bool:
    """코드/등폭 단락으로 처리해야 하는지 종합 판정.

    - 등폭 폰트로 작성된 단락
    - 또는 박스 그리기 문자가 포함된 단락 (ASCII 다이어그램)
    - 또는 스타일이 'Code', 'Source Code Block' 등인 단락
    """
    style_id = (paragraph.style.style_id or "") if paragraph.style else ""
    if "Code" in style_id or "Source" in style_id or "Verbatim" in style_id:
        return True
    if has_box_drawing(paragraph.text):
        return True
    if is_monospace_paragraph(paragraph):
        return True
    return False


def list_marker(paragraph: Paragraph) -> str | None:
    """단락이 목록 항목이면 마커 문자열 반환. 그렇지 않으면 None.

    python-docx는 numPr만 노출하므로 정확한 마커 추출은 어려움.
    여기서는 numPr 존재 여부만 보고 "•" 또는 "1." 형태로 단순 표기.
    """
    pPr = paragraph._p.find(qn("w:pPr"))
    if pPr is None:
        return None
    numPr = pPr.find(qn("w:numPr"))
    if numPr is None:
        return None
    # 정확한 번호 추출은 numbering.xml 추적이 필요. 여기서는 단순 마커.
    return "•"


def open_document(docx_path: str) -> DocumentType:
    """안전하게 docx 파일 열기."""
    try:
        return Document(docx_path)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"docx 파일을 열 수 없습니다: {docx_path} ({e})") from e


# ---------------------------------------------------------------------------
# Attachment helpers (figure 일반화 — 모든 첨부 종류 지원)
# ---------------------------------------------------------------------------
# OOXML embedded-object / OLE / package relationship URIs. 추가 매핑이 필요하면
# 여기에 등록한다.
_OBJECT_REL_NS = (
    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
)


def extract_attachment_rids(p: Paragraph) -> list[tuple[str, str]]:
    """단락에서 모든 첨부(이미지/임베디드 OLE/패키지) 의 ``(rid, kind_hint)`` 를
    수집한다.

    ``extract_image_rids`` 와 달리 OLE / package 관계도 함께 본다 — 즉:

    - ``a:blip/@r:embed`` (인라인 이미지)
    - ``v:imagedata/@r:id`` (vml fallback)
    - ``o:OLEObject/@r:id`` (임베디드 object — Excel/PDF/CAD/Drawing 등)

    Returns:
        ``(rid, hint)`` 튜플 리스트. ``hint`` 는 ``"image"`` / ``"object"``
        / ``"link"`` 중 하나로, 후속 단계에서 정확한 ``kind`` 결정에 참고된다.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    p_el = p._p

    # 1) DrawingML blip — 일반 인라인 이미지
    for blip in p_el.iter(
        "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
    ):
        rid = blip.get(_REL_NS) or blip.get(_REL_NS_LINK)
        if rid and rid not in seen:
            seen.add(rid)
            out.append((rid, "image"))

    # 2) VML imagedata fallback (예: WMF/EMF)
    for imagedata in p_el.iter("{urn:schemas-microsoft-com:vml}imagedata"):
        rid = imagedata.get(_OBJECT_REL_NS)
        if rid and rid not in seen:
            seen.add(rid)
            out.append((rid, "image"))

    # 3) OLE object (Excel, embedded PDF, CAD viewer, …)
    for ole in p_el.iter(
        "{urn:schemas-microsoft-com:office:office}OLEObject"
    ):
        rid = ole.get(_OBJECT_REL_NS)
        if rid and rid not in seen:
            seen.add(rid)
            out.append((rid, "object"))

    return out


# 확장자 -> kind 매핑 (소문자 기준). 변환기 단계에서 가벼운 휴리스틱으로 사용.
_EXT_TO_KIND: dict[str, str] = {
    # figure
    "png": "figure", "jpg": "figure", "jpeg": "figure", "gif": "figure",
    "bmp": "figure", "wmf": "figure", "emf": "figure", "svg": "figure",
    "tif": "figure", "tiff": "figure", "webp": "figure",
    # document
    "pdf": "document", "doc": "document", "docx": "document",
    "hwp": "document", "hwpx": "document", "txt": "document", "rtf": "document",
    "odt": "document",
    # spreadsheet
    "xlsx": "spreadsheet", "xls": "spreadsheet", "xlsm": "spreadsheet",
    "csv": "spreadsheet", "tsv": "spreadsheet", "ods": "spreadsheet",
    # media
    "mp3": "media", "wav": "media", "ogg": "media", "flac": "media",
    "mp4": "media", "avi": "media", "mov": "media", "mkv": "media",
    "webm": "media", "m4a": "media",
    # archive
    "zip": "archive", "tar": "archive", "gz": "archive", "7z": "archive",
    "rar": "archive", "bz2": "archive", "xz": "archive",
    # cad (3D)
    "step": "cad", "stp": "cad", "iges": "cad", "igs": "cad",
    "catpart": "cad", "catproduct": "cad", "sldprt": "cad", "sldasm": "cad",
    "prt": "cad", "x_t": "cad", "x_b": "cad", "stl": "cad",
    # drawing (2D)
    "dwg": "drawing", "dxf": "drawing",
    # data
    "json": "data", "xml": "data", "yaml": "data", "yml": "data", "toml": "data",
}

_MIME_PREFIX_TO_KIND: dict[str, str] = {
    "image/": "figure",
    "audio/": "media",
    "video/": "media",
    "application/pdf": "document",
    "application/msword": "document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml": "document",
    "application/vnd.ms-excel": "spreadsheet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml": "spreadsheet",
    "application/zip": "archive",
    "application/x-7z-compressed": "archive",
    "application/x-tar": "archive",
    "application/gzip": "archive",
    "application/json": "data",
    "application/xml": "data",
    "text/xml": "data",
    "text/csv": "spreadsheet",
    "text/plain": "document",
}


def infer_attachment_kind(
    filename: str | None = None,
    mime: str | None = None,
) -> str:
    """파일명/MIME 으로 첨부 kind 결정. 9 종 중 하나를 반환 (실패 시 ``"other"``).

    ``api.schemas.attachment.infer_attachment_kind`` 의 변환기 측 거울로,
    같은 매핑을 사용한다. 두 모듈 어느 쪽에서나 호출 가능하다.
    """
    # 1) 확장자 우선
    if filename:
        name = str(filename).lower().strip()
        if "." in name:
            ext = name.rsplit(".", 1)[-1]
            kind = _EXT_TO_KIND.get(ext)
            if kind:
                return kind
    # 2) MIME 폴백
    if mime:
        m = mime.strip().lower()
        for key, kind in _MIME_PREFIX_TO_KIND.items():
            if m == key or m.startswith(key):
                return kind
    return "other"
