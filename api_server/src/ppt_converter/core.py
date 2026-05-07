"""PPT(.pptx) → JSON 변환 핵심 로직.

설계 원칙:
- 슬라이드 1장 = 1 섹션 (level 1; 제목에 "1.2" 같은 번호 패턴이 있으면 level 2/3).
- 슬라이드 내 도형 등장 순서 = blocks 배열 순서.
- 표/그림은 본문 흐름에 ``ref`` 블록으로 삽입되고 데이터는 최상위 ``tables`` /
  ``figures`` 에 저장된다.
- 모든 그림은 ``attachments`` 배열에 ``kind=figure`` 로도 등록된다 (Word 변환기와 동일).
- 슬라이드 노트는 ``[Speaker Notes]`` 마커 단락 뒤에 본문 흐름으로 추가된다.
- 시각적 좌표(왼쪽/오른쪽 텍스트박스 등)는 보존되지 않는다 — reading order =
  python-pptx shape iteration order.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.presentation import Presentation as PresentationType

from .models import (
    Attachment,
    Block,
    ConversionResult,
    Figure,
    Section,
    Source,
    Table,
)
from .parser import (
    extract_chart_title,
    extract_picture_blob,
    extract_slide_title,
    extract_speaker_notes,
    extract_table_data,
    extract_text_lines,
    infer_section_id_from_title,
    is_chart_shape,
    is_picture_shape,
    is_table_shape,
    is_text_shape,
    iter_body_shapes,
    iter_slides_with_index,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
@dataclass
class PptxConverterOptions:
    """PPT 변환기 옵션."""

    division: str
    team: str
    year: int
    seq: int = 1
    output_dir: Path = field(default_factory=lambda: Path("output"))
    extract_images: bool = True
    tags: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ID helpers (Word 변환기와 동일한 포맷)
# ---------------------------------------------------------------------------
def _make_doc_id(opts: PptxConverterOptions) -> str:
    return f"DOC-{opts.division}-{opts.team}-{opts.year}-{opts.seq:06d}"


def _make_fig_id(doc_id: str, n: int) -> str:
    return f"{doc_id}-F{n:03d}"


def _make_tbl_id(doc_id: str, n: int) -> str:
    return f"{doc_id}-T{n:03d}"


def _make_att_id(doc_id: str, n: int) -> str:
    return f"{doc_id}-A{n:03d}"


def _sha256_of_file(path: Path | str, chunk: int = 65536) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for buf in iter(lambda: f.read(chunk), b""):
                h.update(buf)
        return h.hexdigest()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------
class PptxConverter:
    """PowerPoint 프레젠테이션을 DOC type JSON 으로 변환."""

    def __init__(self, options: PptxConverterOptions) -> None:
        self.opts = options
        self.doc_id = _make_doc_id(options)
        self.warnings: list[str] = []

        self.section_root: list[Section] = []
        self.figures: list[Figure] = []
        self.tables: list[Table] = []
        self.sources: list[Source] = []
        self.attachments: list[Attachment] = []

        self.fig_counter = 0
        self.tbl_counter = 0
        self.att_counter = 0

        # 자동 섹션 번호 (제목에 번호가 없는 슬라이드용)
        self.l1_counter = 0
        self.l2_counter = 0
        self.l3_counter = 0

    # ---- public API --------------------------------------------------

    def convert(self, pptx_path: str | Path) -> ConversionResult:
        pptx_path = Path(pptx_path)
        prs = Presentation(str(pptx_path))

        for sinfo in iter_slides_with_index(prs):
            self._process_slide(sinfo.index, sinfo.slide)

        meta = self._build_meta(prs, pptx_path)
        return ConversionResult(
            schema_version="1.0",
            meta=meta,
            sections=self.section_root,
            figures=self.figures,
            tables=self.tables,
            sources=self.sources,
            attachments=self.attachments,
            warnings=self.warnings,
        )

    # ---- slide processing -------------------------------------------

    def _process_slide(self, slide_idx: int, slide: Any) -> None:
        title_text = extract_slide_title(slide)
        section = self._open_section_for_slide(slide_idx, title_text)

        # 본문 도형 순회.
        for shape in iter_body_shapes(slide):
            if is_table_shape(shape):
                self._handle_table_shape(section, shape)
            elif is_picture_shape(shape):
                self._handle_picture_shape(section, shape)
            elif is_chart_shape(shape):
                self._handle_chart_shape(section, shape)
            elif is_text_shape(shape):
                self._handle_text_shape(section, shape)
            # 그 외 (자동도형 안의 텍스트, 커넥터 등) — 텍스트 프레임이 있으면 위에서 잡힘.

        # 슬라이드 노트.
        notes = extract_speaker_notes(slide)
        if notes:
            section.blocks.append(Block(type="paragraph", text="[Speaker Notes]"))
            for line in notes.splitlines():
                line = line.strip()
                if not line:
                    continue
                section.blocks.append(Block(type="paragraph", text=line))

        # 빈 슬라이드 경고
        if not section.blocks and not section.title:
            self.warnings.append(
                f"슬라이드 {slide_idx}: 제목/본문/노트가 모두 비어 있음"
            )

    # ---- section management -----------------------------------------

    def _open_section_for_slide(self, slide_idx: int, title_text: str) -> Section:
        """슬라이드 제목으로부터 섹션 1개를 만들어 트리에 등록.

        제목이 "1.2 작동원리" 형태이면 level=2 로 부모 섹션 찾기 시도, 부모가
        없으면 가상 부모를 만들지 않고 level=1 로 폴백한다 (단순화).
        제목이 비어 있으면 자동 번호 + "(슬라이드 N)" 제목 사용.
        """
        if not title_text:
            self.warnings.append(
                f"슬라이드 {slide_idx}: 제목 placeholder 누락 — 자동 제목 사용"
            )
            title = f"슬라이드 {slide_idx}"
            section_id = self._next_auto_id(level=1)
            section = Section(id=section_id, level=1, title=title)
            self.section_root.append(section)
            return section

        parsed_id, clean_title = infer_section_id_from_title(title_text)

        if parsed_id is not None:
            level = parsed_id.count(".") + 1
            section = Section(id=parsed_id, level=level, title=clean_title)
            self._sync_counters(parsed_id)
            self._attach_section(section, level)
            return section

        # 번호 없는 제목 → level 1 자동 번호.
        section_id = self._next_auto_id(level=1)
        section = Section(id=section_id, level=1, title=clean_title)
        self.section_root.append(section)
        return section

    def _attach_section(self, section: Section, level: int) -> None:
        """level 2/3 섹션을 가장 가까운 상위 레벨 섹션의 children 으로 붙인다.

        매칭되는 부모가 없으면 level 1 로 폴백하여 root 에 추가하고 경고.
        """
        if level == 1:
            self.section_root.append(section)
            return

        parent = self._find_last_section_at_level(level - 1)
        if parent is None:
            # 부모 없음 → root 로 폴백 + 경고.
            self.warnings.append(
                f"섹션 {section.id} (level {level}): 상위 레벨 섹션이 없어 root 로 등록"
            )
            section.level = 1
            self.section_root.append(section)
            return
        parent.children.append(section)

    def _find_last_section_at_level(self, level: int) -> Section | None:
        """현재까지 등록된 섹션 중 지정 level 의 마지막 섹션을 반환."""
        candidates: list[Section] = []

        def walk(nodes: list[Section]) -> None:
            for n in nodes:
                if n.level == level:
                    candidates.append(n)
                walk(n.children)

        walk(self.section_root)
        return candidates[-1] if candidates else None

    def _next_auto_id(self, level: int) -> str:
        if level == 1:
            self.l1_counter += 1
            self.l2_counter = 0
            self.l3_counter = 0
            return f"{self.l1_counter}"
        if level == 2:
            self.l2_counter += 1
            self.l3_counter = 0
            return f"{self.l1_counter}.{self.l2_counter}"
        self.l3_counter += 1
        return f"{self.l1_counter}.{self.l2_counter}.{self.l3_counter}"

    def _sync_counters(self, section_id: str) -> None:
        """원본 제목의 번호와 자동 카운터를 맞춰둔다 (이후 자동번호용)."""
        parts = section_id.split(".")
        try:
            if len(parts) >= 1:
                self.l1_counter = int(parts[0])
            if len(parts) >= 2:
                self.l2_counter = int(parts[1])
            else:
                self.l2_counter = 0
            if len(parts) >= 3:
                self.l3_counter = int(parts[2])
            else:
                self.l3_counter = 0
        except ValueError:
            # 비숫자가 섞인 경우 동기화 포기 — 다음 자동 번호는 기존 값 그대로 진행.
            pass

    # ---- shape handlers ---------------------------------------------

    def _handle_text_shape(self, section: Section, shape: Any) -> None:
        lines = extract_text_lines(shape)
        for text, marker in lines:
            if marker:
                section.blocks.append(
                    Block(type="list_item", text=text, marker=marker)
                )
            else:
                section.blocks.append(Block(type="paragraph", text=text))

    def _handle_table_shape(self, section: Section, shape: Any) -> None:
        headers, rows = extract_table_data(shape)
        if not headers and not rows:
            self.warnings.append(
                f"슬라이드 {section.id}: 빈 표 발견 — 무시함"
            )
            return

        self.tbl_counter += 1
        if not headers:
            width = max((len(r) for r in rows), default=1)
            headers = [f"col{i + 1}" for i in range(width)]
            self.warnings.append(
                f"표 {self.tbl_counter}: 헤더가 비어 있음 — 자동 헤더 사용"
            )
        if rows and any(len(r) != len(headers) for r in rows):
            self.warnings.append(
                f"표 {self.tbl_counter}: 일부 행이 헤더 길이와 다름"
            )

        tbl_id = _make_tbl_id(self.doc_id, self.tbl_counter)
        caption = f"Table {self.tbl_counter}: 슬라이드 {section.id} 표"
        tbl = Table(
            id=tbl_id,
            number=self.tbl_counter,
            caption=caption,
            section_ref=section.id,
            headers=headers,
            rows=rows,
        )
        self.tables.append(tbl)
        section.blocks.append(Block(type="table", ref=tbl_id))
        if tbl_id not in section.table_refs:
            section.table_refs.append(tbl_id)

    def _handle_picture_shape(self, section: Section, shape: Any) -> None:
        self.fig_counter += 1
        fig_id = _make_fig_id(self.doc_id, self.fig_counter)
        caption = f"Figure {self.fig_counter}: 슬라이드 {section.id} 이미지"

        fig = Figure(
            id=fig_id,
            number=self.fig_counter,
            caption=caption,
            section_ref=section.id,
        )
        self.figures.append(fig)
        section.blocks.append(Block(type="figure", ref=fig_id))
        if fig_id not in section.figure_refs:
            section.figure_refs.append(fig_id)

        # 첨부 일반화 (kind=figure) — Word 변환기와 동일.
        self.att_counter += 1
        att_id = _make_att_id(self.doc_id, self.att_counter)
        att = Attachment(
            id=att_id,
            number=self.att_counter,
            kind="figure",
            caption=caption,
            section_ref=section.id,
            extra={"figure_ref": fig_id},
        )
        self.attachments.append(att)

        # 바이너리 추출.
        if self.opts.extract_images:
            blob, ext = extract_picture_blob(shape)
            if blob:
                self._save_image(fig, att, blob, ext)
            else:
                self.warnings.append(
                    f"그림 {self.fig_counter}: 이미지 blob 추출 실패"
                )

    def _save_image(
        self, fig: Figure, att: Attachment, blob: bytes, ext: str
    ) -> None:
        out_root = Path(self.opts.output_dir) / self.doc_id
        try:
            out_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.warnings.append(
                f"그림 출력 폴더 생성 실패: {out_root} ({e})"
            )
            return

        # 그림 본 파일.
        fig_name = f"F{fig.number:03d}.{ext}"
        fig_path = out_root / fig_name
        try:
            fig_path.write_bytes(blob)
        except OSError as e:
            self.warnings.append(
                f"그림 {fig.number}: 파일 쓰기 실패 — {fig_path} ({e})"
            )
            return
        fig.image_path = f"{self.doc_id}/{fig_name}"

        # 첨부 파일 경로 (POSIX-style).
        att_name = f"A{att.number:03d}.{ext}"
        att_path = out_root / att_name
        try:
            att_path.write_bytes(blob)
        except OSError as e:
            self.warnings.append(
                f"첨부 {att.number}: 파일 쓰기 실패 — {att_path} ({e})"
            )
            return
        att.file_name = att_name
        att.file_path = (Path(self.doc_id) / att_name).as_posix()
        att.mime_type = self._ext_to_mime(ext)
        try:
            att.size_bytes = att_path.stat().st_size
        except OSError:
            pass
        att.hash_sha256 = _sha256_of_file(att_path)

    @staticmethod
    def _ext_to_mime(ext: str) -> str:
        ext = ext.lower()
        return {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "bmp": "image/bmp",
            "tiff": "image/tiff",
            "wmf": "image/x-wmf",
            "emf": "image/x-emf",
        }.get(ext, "application/octet-stream")

    def _handle_chart_shape(self, section: Section, shape: Any) -> None:
        """차트 도형 처리.

        현재 단계에서는 데이터 테이블 추출은 보류 (차트의 series → table 매핑은
        XML 구조가 다양하여 안정적이지 않음). 대신 그림처럼 caption + ref 블록을
        생성하고, 실제 이미지 추출이 불가하므로 ``image_path`` 는 None 으로 둔다.
        차트 제목이 있으면 캡션에 반영한다.
        """
        self.fig_counter += 1
        fig_id = _make_fig_id(self.doc_id, self.fig_counter)
        chart_title = extract_chart_title(shape)
        caption = f"Figure {self.fig_counter}: 차트"
        if chart_title:
            caption = f"Figure {self.fig_counter}: 차트 — {chart_title}"

        fig = Figure(
            id=fig_id,
            number=self.fig_counter,
            caption=caption,
            section_ref=section.id,
        )
        self.figures.append(fig)
        section.blocks.append(Block(type="figure", ref=fig_id))
        if fig_id not in section.figure_refs:
            section.figure_refs.append(fig_id)

        # 첨부 (kind=other — 차트는 임베드 부품 형태이므로 figure 가 아닌 일반 첨부로 둔다).
        self.att_counter += 1
        att_id = _make_att_id(self.doc_id, self.att_counter)
        att = Attachment(
            id=att_id,
            number=self.att_counter,
            kind="other",
            caption=caption,
            section_ref=section.id,
            extra={"figure_ref": fig_id, "chart_title": chart_title},
        )
        self.attachments.append(att)
        self.warnings.append(
            f"슬라이드 {section.id}: 차트는 placeholder 만 등록됨 (데이터 추출 미구현)"
        )

    # ---- meta --------------------------------------------------------

    def _build_meta(
        self, prs: PresentationType, pptx_path: Path
    ) -> dict[str, Any]:
        try:
            core = prs.core_properties
        except Exception:  # noqa: BLE001
            core = None

        title = ""
        author = ""
        created_str = ""
        modified_str = ""
        if core is not None:
            title = (getattr(core, "title", "") or "").strip()
            author = (getattr(core, "author", "") or "").strip()
            created = getattr(core, "created", None)
            modified = getattr(core, "modified", None)
            now = datetime.now(tz=timezone.utc)
            created_str = (created or now).strftime("%Y-%m-%d")
            modified_str = (modified or now).strftime("%Y-%m-%d")

        if not title:
            title = pptx_path.stem
        if not created_str:
            created_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        if not modified_str:
            modified_str = created_str

        meta: dict[str, Any] = {
            "doc_id": self.doc_id,
            "title": title,
            "source_format": "pptx",
            "source_file": pptx_path.name,
            "doc_type": "slide",
            "created": created_str,
            "modified": modified_str,
            "author": author,
            "department": f"{self.opts.division}-{self.opts.team}",
            "version": "1.0",
            "tags": list(self.opts.tags),
            "summary": "",
        }
        if self.opts.agents:
            meta["agent_scope"] = list(self.opts.agents)

        if not meta["tags"]:
            self.warnings.append("tags 미지정 → 빈 배열")
        if not meta["summary"]:
            self.warnings.append("summary 미지정 → 빈 문자열")

        return meta


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------
def write_output(
    result: ConversionResult,
    output_dir: Path,
) -> tuple[Path, Path]:
    """JSON 과 경고 로그 저장. (json_path, log_path) 반환."""
    import json

    output_dir.mkdir(parents=True, exist_ok=True)

    doc_id = result.meta["doc_id"]
    json_path = output_dir / f"{doc_id}.json"
    log_path = output_dir / f"{doc_id}.warnings.log"

    json_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if result.warnings:
        log_path.write_text(
            "\n".join(f"[WARN] {w}" for w in result.warnings),
            encoding="utf-8",
        )
    elif log_path.exists():
        log_path.unlink()

    return json_path, log_path


__all__ = [
    "PptxConverter",
    "PptxConverterOptions",
    "write_output",
]
