"""변환 중간 모델 (Word → 중간표현 → JSON).

설계 원칙:
- Section.blocks 가 본문 등장 순서를 보존한다.
- 각 block은 paragraph / code / table / figure / list_item 등 타입을 가진다.
- 표·그림은 blocks에서는 ref만 가지고 실제 데이터는 최상위 tables/figures에 저장.
- 이렇게 하면 AI가 blocks를 읽으면서 원본 Word의 흐름을 그대로 재구성 가능.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Block:
    """본문 블록 (단락/코드/표 참조/그림 참조/목록 항목).

    type:
        - paragraph: 일반 단락
        - code: 코드/등폭 글꼴 블록 (LS-DYNA 카드, YAML, ASCII 다이어그램 등)
        - table: 표 위치 표시 (ref로 표 데이터 가리킴)
        - figure: 그림 위치 표시 (ref로 그림 데이터 가리킴)
        - list_item: 목록 항목 (text + marker)
    """

    type: str
    text: str | None = None
    ref: str | None = None
    marker: str | None = None  # list_item 용 (예: "•", "1.")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        if self.text is not None:
            d["text"] = self.text
        if self.ref is not None:
            d["ref"] = self.ref
        if self.marker is not None:
            d["marker"] = self.marker
        return d


@dataclass
class Section:
    id: str
    level: int
    title: str
    blocks: list[Block] = field(default_factory=list)
    figure_refs: list[str] = field(default_factory=list)
    table_refs: list[str] = field(default_factory=list)
    children: list[Section] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level,
            "title": self.title,
            "blocks": [b.to_dict() for b in self.blocks],
            "figure_refs": self.figure_refs,
            "table_refs": self.table_refs,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class Figure:
    id: str
    number: int
    caption: str
    section_ref: str
    image_path: str | None = None
    source_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "number": self.number,
            "caption": self.caption,
            "section_ref": self.section_ref,
        }
        if self.image_path:
            d["image_path"] = self.image_path
        if self.source_ref:
            d["source_ref"] = self.source_ref
        return d


@dataclass
class Table:
    id: str
    number: int
    caption: str
    section_ref: str
    headers: list[str]
    rows: list[list[Any]]
    source_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "number": self.number,
            "caption": self.caption,
            "section_ref": self.section_ref,
            "headers": self.headers,
            "rows": self.rows,
        }
        if self.source_ref:
            d["source_ref"] = self.source_ref
        return d


@dataclass
class Source:
    id: str
    type: str
    format: str
    file_name: str
    file_path: str
    modified: str | None = None
    size_bytes: int | None = None
    hash_sha256: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "format": self.format,
            "file_name": self.file_name,
            "file_path": self.file_path,
        }
        if self.modified:
            d["modified"] = self.modified
        if self.size_bytes is not None:
            d["size_bytes"] = self.size_bytes
        if self.hash_sha256:
            d["hash_sha256"] = self.hash_sha256
        if self.description:
            d["description"] = self.description
        return d


@dataclass
class Attachment:
    """일반화된 첨부 (figure / document / spreadsheet / media / archive
    / cad / chart / drawing / data / other).

    캡션은 모든 kind 에 대해 필수다. 누락 시 ``"(캡션 누락 — 검수 필요)"``
    placeholder 가 채워지고 변환기가 ``warnings`` 에 경고를 남긴다.

    ``file_path`` 는 정적 마운트 ``/attachments`` 직하 상대 경로이며,
    cross-platform 이식성을 위해 항상 POSIX-style (forward slashes) 로
    저장한다. 내부적으로는 ``pathlib.Path`` 를 사용해 OS 분리자를
    추상화한다.
    """

    id: str
    number: int
    kind: str
    caption: str
    section_ref: str
    file_name: str | None = None
    file_path: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    hash_sha256: str | None = None
    source_ref: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "number": self.number,
            "kind": self.kind,
            "caption": self.caption,
            "section_ref": self.section_ref,
        }
        if self.file_name:
            d["file_name"] = self.file_name
        if self.file_path:
            d["file_path"] = self.file_path
        if self.mime_type:
            d["mime_type"] = self.mime_type
        if self.size_bytes is not None:
            d["size_bytes"] = self.size_bytes
        if self.hash_sha256:
            d["hash_sha256"] = self.hash_sha256
        if self.source_ref:
            d["source_ref"] = self.source_ref
        if self.extra:
            d["extra"] = dict(self.extra)
        return d


@dataclass
class ConversionResult:
    schema_version: str
    meta: dict[str, Any]
    sections: list[Section]
    figures: list[Figure]
    tables: list[Table]
    sources: list[Source]
    attachments: list[Attachment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def build_toc(self) -> list[dict[str, Any]]:
        toc: list[dict[str, Any]] = []

        def walk(nodes: list[Section]) -> None:
            for n in nodes:
                if n.level <= 3:
                    toc.append({"id": n.id, "level": n.level, "title": n.title})
                walk(n.children)

        walk(self.sections)
        return toc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "meta": self.meta,
            "toc": self.build_toc(),
            "sections": [s.to_dict() for s in self.sections],
            "figures": [f.to_dict() for f in self.figures],
            "tables": [t.to_dict() for t in self.tables],
            "sources": [s.to_dict() for s in self.sources],
            "attachments": [a.to_dict() for a in self.attachments],
        }
