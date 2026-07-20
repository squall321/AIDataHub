"""markdown-it-py 토큰 워커 보조 함수.

핵심 헬퍼:
- ``walk_tokens(tokens)``         : 깊이 우선 순회로 (token, depth) 를 yield
- ``inline_to_text(token)``       : inline 토큰을 평문(+하이퍼링크) 로 펼침
- ``extract_section_id_from_heading``: ``# 1.2 작동원리`` → ("1.2", "작동원리")
- ``infer_attachment_kind_from_url``: 확장자 기반 첨부 종류 추정
- ``is_absolute_url``             : http(s)/ftp/data 등 절대 URL 판별
- ``parse_yaml_front_matter``     : YAML 블록을 meta dict 로
"""
from __future__ import annotations

import re
from typing import Any, Iterator, Optional

# ---------------------------------------------------------------------------
# 헤딩 텍스트 → (section_id, title)
# ---------------------------------------------------------------------------

# "1", "1.", "1.2", "1.2.3" 같은 점 구분 번호 + (선택적 마침표) + 공백 + 제목.
SECTION_NUM_PATTERN = re.compile(r"^(\d+(?:\.\d+){0,4})\.?\s+(.*)$")


def extract_section_id_from_heading(text: str) -> tuple[Optional[str], str]:
    """``# 1.2 작동원리`` → ("1.2", "작동원리").

    매칭되지 않으면 (None, text.strip()) 반환.
    """
    if not text:
        return None, ""
    s = text.strip()
    m = SECTION_NUM_PATTERN.match(s)
    if m:
        return m.group(1), m.group(2).strip()
    return None, s


# ---------------------------------------------------------------------------
# 토큰 깊이 우선 워커
# ---------------------------------------------------------------------------

def walk_tokens(tokens: list[Any]) -> Iterator[tuple[Any, int]]:
    """markdown-it 토큰 트리(children 포함)를 (token, depth) 로 깊이우선 순회."""
    def _walk(ts: list[Any], depth: int) -> Iterator[tuple[Any, int]]:
        for t in ts:
            yield t, depth
            children = getattr(t, "children", None)
            if children:
                yield from _walk(children, depth + 1)

    yield from _walk(tokens, 0)


# ---------------------------------------------------------------------------
# inline 토큰 → 평문 (+ 마크다운 하이퍼링크 보존)
# ---------------------------------------------------------------------------

def inline_to_text(inline_token: Any) -> str:
    """inline 토큰의 children 을 평문으로 합성.

    - 일반 텍스트(``text``) 는 그대로
    - 코드(``code_inline``) 는 ```...``` 로 감싸기
    - 링크(``link_open``/``link_close``) 는 ``[text](url)`` 형식 보존
    - 이미지(``image``) 는 인라인 텍스트에는 포함하지 않음 (별도 attachment 로 처리됨)
    - 줄바꿈(``softbreak``/``hardbreak``) 은 공백/개행
    """
    if inline_token is None or getattr(inline_token, "children", None) is None:
        # plain leaf — children 이 None 인 경우는 fallback
        return getattr(inline_token, "content", "") or ""

    out: list[str] = []
    link_stack: list[str] = []   # 링크 url 스택

    # 단순 구현: 평탄하게 처리. md 의 link 는 nest 가 거의 없으므로 OK.
    inside_link_text: list[str] | None = None
    pending_link_url: str = ""

    for child in inline_token.children:
        ttype = child.type
        if ttype == "text":
            piece = child.content or ""
            if inside_link_text is not None:
                inside_link_text.append(piece)
            else:
                out.append(piece)
        elif ttype == "code_inline":
            piece = f"`{child.content or ''}`"
            if inside_link_text is not None:
                inside_link_text.append(piece)
            else:
                out.append(piece)
        elif ttype == "softbreak":
            (inside_link_text if inside_link_text is not None else out).append(" ")
        elif ttype == "hardbreak":
            (inside_link_text if inside_link_text is not None else out).append("\n")
        elif ttype == "link_open":
            href = ""
            for k, v in (child.attrs or {}).items() if isinstance(child.attrs, dict) else (child.attrs or []):
                if k == "href":
                    href = v
                    break
            pending_link_url = href or ""
            inside_link_text = []
            link_stack.append(pending_link_url)
        elif ttype == "link_close":
            url = link_stack.pop() if link_stack else pending_link_url
            text_inner = "".join(inside_link_text or [])
            out.append(f"[{text_inner}]({url})")
            inside_link_text = None
            pending_link_url = ""
        elif ttype == "image":
            # image 는 attachment 로 별도 처리됨. 인라인 텍스트엔 alt 만 표시.
            alt = ""
            if child.children:
                alt = "".join((c.content or "") for c in child.children if c.type == "text")
            (inside_link_text if inside_link_text is not None else out).append(alt)
        elif ttype in ("em_open", "em_close", "strong_open", "strong_close",
                       "s_open", "s_close"):
            # 인라인 서식은 무시 (스키마상 평문)
            continue
        elif ttype == "html_inline":
            # 인라인 HTML 은 평문 그대로 보존
            (inside_link_text if inside_link_text is not None else out).append(child.content or "")
        else:
            # 알 수 없는 타입: content 가 있으면 그대로
            content = getattr(child, "content", "") or ""
            if content:
                (inside_link_text if inside_link_text is not None else out).append(content)

    return "".join(out)


# ---------------------------------------------------------------------------
# 첨부 종류 추정
# ---------------------------------------------------------------------------

_ATTACHMENT_KIND_BY_EXT: dict[str, str] = {
    # figure
    "png": "figure", "jpg": "figure", "jpeg": "figure", "gif": "figure",
    "bmp": "figure", "tif": "figure", "tiff": "figure", "webp": "figure",
    "svg": "figure", "emf": "figure", "wmf": "figure",
    # document
    "pdf": "document", "doc": "document", "docx": "document",
    "rtf": "document", "txt": "document", "odt": "document",
    "hwp": "document", "hwpx": "document",
    # spreadsheet
    "xls": "spreadsheet", "xlsx": "spreadsheet", "csv": "spreadsheet",
    "ods": "spreadsheet", "tsv": "spreadsheet",
    # slide
    "ppt": "slide", "pptx": "slide", "odp": "slide",
    # media
    "mp4": "media", "mov": "media", "avi": "media", "mkv": "media",
    "mp3": "media", "wav": "media", "flac": "media",
    # archive
    "zip": "archive", "tar": "archive", "gz": "archive", "tgz": "archive",
    "7z": "archive", "rar": "archive",
    # cad
    "catpart": "cad", "catproduct": "cad", "step": "cad", "stp": "cad",
    "iges": "cad", "igs": "cad", "sldprt": "cad", "sldasm": "cad",
    "prt": "cad", "stl": "cad", "x_t": "cad", "x_b": "cad",
    # cae (솔버 입출력 덱/결과) — schemas.attachment 와 동일 매핑 유지
    # (k/inp/cdb/odb 는 과거 "data" 였으나 cae kind 신설로 이관)
    "k": "cae", "key": "cae", "dyn": "cae", "dynain": "cae", "d3plot": "cae",
    "inp": "cae", "cdb": "cae", "odb": "cae", "rad": "cae",
    "bdf": "cae", "nas": "cae", "fem": "cae", "op2": "cae",
    # drawing
    "dxf": "drawing", "dwg": "drawing",
    # data
    "json": "data", "yaml": "data", "yml": "data", "xml": "data",
}


def infer_attachment_kind_from_url(url: str) -> str:
    """URL/파일 경로의 확장자로 attachment kind 추정.

    매칭 실패 시 ``"other"`` 반환.
    """
    if not url:
        return "other"
    # querystring/fragment 제거
    cleaned = url.split("?", 1)[0].split("#", 1)[0]
    if "." not in cleaned:
        return "other"
    ext = cleaned.rsplit(".", 1)[-1].lower().strip()
    if not ext or len(ext) > 10:
        return "other"
    return _ATTACHMENT_KIND_BY_EXT.get(ext, "other")


# ---------------------------------------------------------------------------
# 절대 URL 판별
# ---------------------------------------------------------------------------

_ABSOLUTE_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def is_absolute_url(url: str) -> bool:
    """``http://``, ``https://``, ``ftp://``, ``data:`` 등 스킴 포함이면 True."""
    if not url:
        return False
    if url.startswith("//"):
        return True   # 프로토콜 상대 URL
    return bool(_ABSOLUTE_URL_RE.match(url))


# ---------------------------------------------------------------------------
# YAML front matter 파싱 (의존성 없는 최소 구현)
# ---------------------------------------------------------------------------

def parse_yaml_front_matter(yaml_text: str) -> dict[str, Any]:
    """간단한 YAML 서브셋 파서.

    지원:
    - ``key: value``
    - ``key: [a, b, c]``  (인라인 리스트)
    - 다음 줄들이 ``- item`` 인 경우 리스트로 취급
    - 따옴표('"', "'") 제거

    PyYAML 의존을 피하기 위해 직접 구현. 복잡한 YAML 은 지원하지 않음.
    """
    out: dict[str, Any] = {}
    if not yaml_text:
        return out

    lines = yaml_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        if ":" not in stripped:
            i += 1
            continue

        key, _, raw_val = stripped.partition(":")
        key = key.strip()
        raw_val = raw_val.strip()

        if not key:
            i += 1
            continue

        if raw_val:
            out[key] = _parse_yaml_scalar(raw_val)
            i += 1
            continue

        # value 가 비어있다면 다음 줄들이 ``- item`` 형태인지 확인
        items: list[Any] = []
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            nxt_stripped = nxt.strip()
            if not nxt_stripped:
                j += 1
                continue
            if nxt.startswith(" ") and nxt_stripped.startswith("- "):
                items.append(_parse_yaml_scalar(nxt_stripped[2:].strip()))
                j += 1
                continue
            break
        if items:
            out[key] = items
            i = j
        else:
            out[key] = None
            i += 1

    return out


def _parse_yaml_scalar(s: str) -> Any:
    """YAML 스칼라 한 줄을 Python 값으로."""
    if not s:
        return ""
    # 인라인 리스트
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_yaml_scalar(p.strip()) for p in _split_inline_list(inner)]
    # 따옴표 제거
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    # 불리언/널
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", ""):
        return None
    # 숫자
    try:
        if "." not in s and "e" not in low:
            return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _split_inline_list(s: str) -> list[str]:
    """``a, b, "c, d"`` → ['a', 'b', '"c, d"'] (따옴표 안 콤마 보호)."""
    parts: list[str] = []
    buf: list[str] = []
    in_q: str | None = None
    for ch in s:
        if in_q:
            buf.append(ch)
            if ch == in_q:
                in_q = None
            continue
        if ch in ('"', "'"):
            in_q = ch
            buf.append(ch)
            continue
        if ch == ",":
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


# ---------------------------------------------------------------------------
# Caption 패턴
# ---------------------------------------------------------------------------

CAPTION_PATTERN = re.compile(
    r"^(Figure|Fig\.?|그림|Table|Tbl\.?|표)\s*(\d+)\s*[:\.\-]\s*(.+)$",
    re.IGNORECASE,
)


def parse_figure_caption(text: str) -> Optional[str]:
    """``Figure 1: 설명`` 패턴이면 전체 캡션 문자열 반환, 아니면 None."""
    if not text:
        return None
    m = CAPTION_PATTERN.match(text.strip())
    if not m:
        return None
    return text.strip()


__all__ = [
    "SECTION_NUM_PATTERN",
    "CAPTION_PATTERN",
    "extract_section_id_from_heading",
    "walk_tokens",
    "inline_to_text",
    "infer_attachment_kind_from_url",
    "is_absolute_url",
    "parse_yaml_front_matter",
    "parse_figure_caption",
]
