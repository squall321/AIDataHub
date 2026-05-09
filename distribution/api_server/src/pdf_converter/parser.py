"""PDF 파싱 보조 함수.

핵심 헬퍼:
- ``extract_pdf_metadata(reader)``     : pypdf /Info → meta dict (title/author/...)
- ``extract_outline(reader)``          : pypdf /Outlines → OutlineItem 리스트
- ``infer_headings_from_fontsize(plumber_doc)`` : 폰트 크기 기반 헤딩 후보
- ``extract_text_with_layout(page)``   : pdfplumber 페이지 → TextLine 리스트
- ``parse_pdf_date(s)``                : ``D:YYYYMMDDHHMMSS+09'00'`` → ISO 8601
- ``SECTION_NUM_PATTERN``              : ``1.2 제목`` 헤딩 패턴 (Word 와 동일)

PDF 는 본문 흐름이 페이지 좌표 기반이므로, 라인 단위로 모은 뒤 평탄화한다.
다단(2-column) 레이아웃은 pdfplumber 가 위→아래 우선으로 평탄화한다 —
완벽하지 않으나 베스트 노력 (한계는 경고로 보고).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 패턴 (Word/MD 변환기와 동일 — ``1.2.3 제목``)
# ---------------------------------------------------------------------------
SECTION_NUM_PATTERN = re.compile(r"^(\d+(?:\.\d+){0,4})\.?\s+(.+)$")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class OutlineItem:
    """PDF /Outlines (북마크) 항목.

    level 은 1-base (최상위 = 1).
    page 는 0-base 페이지 번호 (없으면 None).
    """

    title: str
    level: int
    page: Optional[int] = None


@dataclass
class TextLine:
    """페이지의 한 줄 — 텍스트 + 평균 폰트 크기 + y 좌표 + 페이지 번호."""

    text: str
    avg_font_size: float
    page_number: int       # 1-base
    y_top: float           # PDF top-left 기준 y (작을수록 위)
    is_bold: bool = False  # 베스트 노력


@dataclass
class HeadingCandidate:
    """헤딩 후보 (폰트 크기 또는 패턴 기반)."""

    text: str
    page_number: int
    level: int
    source: str  # "outline" | "pattern" | "fontsize"
    section_id: Optional[str] = None  # "1.2" 같은 자동 ID
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# /Info → meta dict
# ---------------------------------------------------------------------------

_PDF_DATE_RE = re.compile(
    r"^D:(?P<y>\d{4})(?P<m>\d{2})?(?P<d>\d{2})?"
    r"(?P<H>\d{2})?(?P<M>\d{2})?(?P<S>\d{2})?"
    r"(?P<tz>[+\-Z].*)?$"
)


def parse_pdf_date(s: str | None) -> Optional[str]:
    """PDF date string ``D:20260507142530+09'00'`` → ISO 8601 ``2026-05-07T14:25:30+09:00``.

    실패 시 None 반환. 입력이 None / 빈 문자열이어도 None.
    """
    if not s:
        return None
    s = str(s).strip()
    m = _PDF_DATE_RE.match(s)
    if not m:
        return None
    y = m.group("y")
    mo = m.group("m") or "01"
    d = m.group("d") or "01"
    H = m.group("H") or "00"
    M = m.group("M") or "00"
    S = m.group("S") or "00"
    tz_raw = m.group("tz") or ""
    # tz: ``+09'00'`` or ``Z``  →  ``+09:00`` / ``Z``
    tz = ""
    if tz_raw:
        if tz_raw.upper().startswith("Z"):
            tz = "Z"
        else:
            # ``+09'00'`` → ``+09:00``
            cleaned = tz_raw.replace("'", "").rstrip("'")
            if len(cleaned) == 3:  # "+09"
                tz = f"{cleaned}:00"
            elif len(cleaned) == 5:  # "+0900"
                tz = f"{cleaned[:3]}:{cleaned[3:]}"
            else:
                # already +HH:MM or unknown
                tz = cleaned
    return f"{y}-{mo}-{d}T{H}:{M}:{S}{tz}"


def extract_pdf_metadata(reader: Any) -> dict[str, Any]:
    """pypdf ``PdfReader.metadata`` (/Info) 를 dict 로 정규화.

    출력 키: title / author / subject / keywords / creator / producer
            / creation_date (ISO 8601) / modification_date (ISO 8601).
    누락된 항목은 빈 문자열 또는 None.
    """
    meta = getattr(reader, "metadata", None)
    out: dict[str, Any] = {
        "title": "",
        "author": "",
        "subject": "",
        "keywords": "",
        "creator": "",
        "producer": "",
        "creation_date": None,
        "modification_date": None,
    }
    if meta is None:
        return out

    def _g(*names: str) -> str:
        for n in names:
            v = None
            try:
                v = meta.get(n) if hasattr(meta, "get") else None
            except Exception:
                v = None
            if v is None:
                v = getattr(meta, n.lstrip("/").lower(), None)
            if v is not None:
                try:
                    return str(v)
                except Exception:
                    return ""
        return ""

    out["title"] = _g("/Title", "title")
    out["author"] = _g("/Author", "author")
    out["subject"] = _g("/Subject", "subject")
    out["keywords"] = _g("/Keywords", "keywords")
    out["creator"] = _g("/Creator", "creator")
    out["producer"] = _g("/Producer", "producer")
    out["creation_date"] = parse_pdf_date(_g("/CreationDate", "creation_date"))
    out["modification_date"] = parse_pdf_date(_g("/ModDate", "modification_date"))
    return out


# ---------------------------------------------------------------------------
# /Outlines → OutlineItem 리스트 (DFS, level 1-base)
# ---------------------------------------------------------------------------

def extract_outline(reader: Any) -> list[OutlineItem]:
    """pypdf ``reader.outline`` 를 평탄화해 OutlineItem 리스트 반환.

    pypdf 의 outline 은 중첩 리스트 (Destination 또는 sub-list) 형태이다.
    각 Destination 의 title 과 page index 를 추출한다.

    실패하거나 outline 이 없으면 빈 리스트 반환.
    """
    out: list[OutlineItem] = []
    try:
        ol = reader.outline
    except Exception as exc:
        logger.debug("outline 추출 실패: %s", exc)
        return out
    if not ol:
        return out

    def _resolve_page_index(item: Any) -> Optional[int]:
        try:
            idx = reader.get_destination_page_number(item)
            return int(idx) if idx is not None else None
        except Exception:
            return None

    def _walk(nodes: Any, level: int) -> None:
        if isinstance(nodes, list):
            for n in nodes:
                _walk(n, level)
            return
        # Destination-like
        title = ""
        try:
            title = str(getattr(nodes, "title", "") or "")
        except Exception:
            title = ""
        if not title:
            try:
                title = str(nodes["/Title"]) if "/Title" in nodes else ""
            except Exception:
                title = ""
        page_idx = _resolve_page_index(nodes)
        if title:
            out.append(OutlineItem(title=title.strip(), level=level, page=page_idx))

    # pypdf 는 [item, [child, child], item, ...] 의 nested 형태.
    def _walk_nested(seq: Any, level: int) -> None:
        if not isinstance(seq, list):
            _walk(seq, level)
            return
        for item in seq:
            if isinstance(item, list):
                _walk_nested(item, level + 1)
            else:
                _walk(item, level)

    _walk_nested(ol, 1)
    return out


# ---------------------------------------------------------------------------
# pdfplumber 페이지 → TextLine 리스트
# ---------------------------------------------------------------------------

def extract_text_with_layout(plumber_page: Any, page_number: int) -> list[TextLine]:
    """pdfplumber Page 의 char-level 정보를 라인 단위로 묶어 TextLine 리스트.

    같은 ``top`` 좌표(±1.0pt) 를 공유하는 char 들을 한 줄로 묶고,
    줄 안의 평균 글꼴 크기를 계산한다.
    """
    lines: list[TextLine] = []
    chars = []
    try:
        chars = plumber_page.chars or []
    except Exception:
        chars = []

    if not chars:
        # 텍스트가 전혀 없는 페이지 — 스캔 PDF 가능성
        return lines

    # 1) y 좌표(top) 기준으로 그룹핑 — 1pt 이하 차이는 같은 줄로
    chars_sorted = sorted(chars, key=lambda c: (round(c.get("top", 0.0), 0), c.get("x0", 0.0)))
    cur_top: Optional[float] = None
    cur_chars: list[dict[str, Any]] = []

    def _flush() -> None:
        if not cur_chars:
            return
        text = "".join(c.get("text", "") for c in cur_chars).strip()
        if not text:
            return
        sizes = [float(c.get("size", 0.0) or 0.0) for c in cur_chars]
        sizes = [s for s in sizes if s > 0]
        avg = sum(sizes) / len(sizes) if sizes else 0.0
        bold = any("Bold" in str(c.get("fontname", "")) for c in cur_chars)
        top = float(cur_chars[0].get("top", 0.0) or 0.0)
        lines.append(
            TextLine(
                text=text,
                avg_font_size=avg,
                page_number=page_number,
                y_top=top,
                is_bold=bold,
            )
        )

    for c in chars_sorted:
        top = round(float(c.get("top", 0.0) or 0.0), 0)
        if cur_top is None:
            cur_top = top
            cur_chars = [c]
            continue
        if abs(top - cur_top) <= 1.0:
            cur_chars.append(c)
        else:
            _flush()
            cur_top = top
            cur_chars = [c]
    _flush()

    return lines


# ---------------------------------------------------------------------------
# 폰트 크기 기반 헤딩 후보
# ---------------------------------------------------------------------------

def infer_headings_from_fontsize(
    all_lines: list[TextLine],
    *,
    threshold_ratio: float = 1.2,
) -> list[HeadingCandidate]:
    """전체 라인에서 폰트 크기가 본문 평균의 ``threshold_ratio`` 이상인 라인을 헤딩 후보로.

    레벨 결정:
    - 가장 큰 크기 → level 1
    - 그 다음 → level 2
    - 그 다음 → level 3
    - 그 외 → level 3 (collapse)
    """
    if not all_lines:
        return []
    sizes = [ln.avg_font_size for ln in all_lines if ln.avg_font_size > 0]
    if not sizes:
        return []
    body_avg = sum(sizes) / len(sizes)
    big_lines = [ln for ln in all_lines if ln.avg_font_size >= body_avg * threshold_ratio]
    if not big_lines:
        return []

    # 큰 글씨 사이즈를 정렬해 레벨 매핑
    distinct = sorted({round(ln.avg_font_size, 1) for ln in big_lines}, reverse=True)
    size_to_level: dict[float, int] = {}
    for idx, sz in enumerate(distinct[:3]):
        size_to_level[sz] = idx + 1
    # 4번째 이상은 모두 level 3 으로 collapse
    for sz in distinct[3:]:
        size_to_level[sz] = 3

    out: list[HeadingCandidate] = []
    for ln in big_lines:
        sz = round(ln.avg_font_size, 1)
        level = size_to_level.get(sz, 3)
        out.append(
            HeadingCandidate(
                text=ln.text,
                page_number=ln.page_number,
                level=level,
                source="fontsize",
                extra={"font_size": ln.avg_font_size, "body_avg": body_avg},
            )
        )
    return out


# ---------------------------------------------------------------------------
# 패턴 기반 헤딩 후보 (``1.2 제목``)
# ---------------------------------------------------------------------------

def infer_headings_from_pattern(all_lines: list[TextLine]) -> list[HeadingCandidate]:
    """``^\\d+(\\.\\d+){0,2}\\s+제목$`` 패턴이면 헤딩 후보로.

    레벨은 점 개수 + 1:
        ``1 개요`` → level 1
        ``1.2 작동`` → level 2
        ``1.2.3 ...`` → level 3
        그보다 깊으면 level 3 (collapse).
    """
    out: list[HeadingCandidate] = []
    for ln in all_lines:
        m = SECTION_NUM_PATTERN.match(ln.text.strip())
        if not m:
            continue
        sec_id = m.group(1)
        title = m.group(2).strip()
        if not title:
            continue
        # title 이 너무 길면 본문 단락일 가능성 — 80자 이하만 헤딩 후보
        if len(title) > 80:
            continue
        depth = sec_id.count(".") + 1
        level = min(depth, 3)
        out.append(
            HeadingCandidate(
                text=ln.text.strip(),
                page_number=ln.page_number,
                level=level,
                source="pattern",
                section_id=sec_id,
                extra={"title": title},
            )
        )
    return out


# ---------------------------------------------------------------------------
# 첨부 종류 추정 (확장자)
# ---------------------------------------------------------------------------

_ATTACHMENT_KIND_BY_EXT: dict[str, str] = {
    "png": "figure", "jpg": "figure", "jpeg": "figure", "gif": "figure",
    "bmp": "figure", "tif": "figure", "tiff": "figure", "webp": "figure",
}


def infer_attachment_kind_from_ext(ext: str) -> str:
    e = (ext or "").lower().lstrip(".")
    return _ATTACHMENT_KIND_BY_EXT.get(e, "other")


__all__ = [
    "SECTION_NUM_PATTERN",
    "OutlineItem",
    "TextLine",
    "HeadingCandidate",
    "parse_pdf_date",
    "extract_pdf_metadata",
    "extract_outline",
    "extract_text_with_layout",
    "infer_headings_from_fontsize",
    "infer_headings_from_pattern",
    "infer_attachment_kind_from_ext",
]
