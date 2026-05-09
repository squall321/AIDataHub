"""HTML(.html/.htm) → DOC JSON 변환기.

[json_schema_rules.md] v1.0 스키마 출력 (data_type=DOC, source_format=html).
[html_to_json_conversion_rules.md] 의 규칙을 따른다.

설계 핵심:
- ``lxml.html`` 로 트리 파싱 후 body 하위를 순서대로 순회 (single pass).
- h1-h3 → sections, h4-h6 → 본문 단락 (level 3 content) — md_converter 와 동일.
- p / ul / ol / pre / code / blockquote / table / img 를 차례로 변환.
- ``<head>`` 의 ``<title>`` 와 ``<meta name=...>`` 가 있으면 meta 필드로 흡수한다.
- 표는 ``tables[]`` 와 ``sections[].table_refs[]`` 에 저장.
- 그림은 ``attachments[kind=figure]`` 로 등록 — 절대 URL 은 ``extra.url``,
  상대 경로는 ``file_path`` 로 보존한다.
"""
__version__ = "0.1.0"

from .core import HtmlConverter, HtmlConverterOptions, write_output

__all__ = [
    "HtmlConverter",
    "HtmlConverterOptions",
    "write_output",
]
