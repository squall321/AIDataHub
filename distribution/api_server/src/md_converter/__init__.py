"""Markdown(.md) → DOC JSON 변환기.

[json_schema_rules.md] v1.0 스키마 출력 (data_type=DOC, source_format=md).
[md_to_json_conversion_rules.md] 의 규칙을 따른다.

설계 핵심:
- markdown-it-py CommonMark 토큰 스트림을 한 번 순회 (single pass).
- h1-h6 → sections (h4-h6 은 level 3 본문 단락으로 강등).
- paragraph / code_block / fenced code / list_item / blockquote / table /
  image 토큰을 차례로 변환.
- YAML front matter (``---`` 블록) 가 있으면 meta 필드로 그대로 흡수한다.
- 표는 ``tables[]`` 와 ``sections[].table_refs[]`` 에 저장.
- 그림은 ``attachments[kind=figure]`` 로 등록 — 절대 URL 은 ``extra.url`` 에
  보존하고, 상대 경로는 그대로 ``file_path`` 로 유지한다.
"""
__version__ = "0.1.0"

from .core import MarkdownConverter, MarkdownConverterOptions, write_output

__all__ = [
    "MarkdownConverter",
    "MarkdownConverterOptions",
    "write_output",
]
