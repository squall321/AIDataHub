"""파일/디렉터리에서 JSON 을 읽어 정규화·검증.

CLI 에서 사용하는 얇은 어댑터.
"""
from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..schemas import RecordIn
from .normalizer import normalize

logger = logging.getLogger(__name__)


def iter_json_files(path: Path, recursive: bool = False) -> Iterator[Path]:
    """경로에서 처리할 ``.json`` 파일을 순회한다.

    - ``path`` 가 파일이면 그 파일만.
    - 디렉터리이면 ``*.json`` (재귀 옵션). ``.warnings.log`` 등은 제외.
    """
    if path.is_file():
        if path.suffix.lower() == ".json":
            yield path
        return

    if not path.is_dir():
        raise FileNotFoundError(f"Path not found: {path}")

    pattern = "**/*.json" if recursive else "*.json"
    for p in sorted(path.glob(pattern)):
        if p.is_file():
            yield p


def load_json(file_path: Path) -> dict[str, Any]:
    """JSON 파일을 dict 로 읽는다 (UTF-8 / UTF-8-BOM 모두 허용)."""
    text = file_path.read_text(encoding="utf-8-sig")
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError(
            f"{file_path}: top-level JSON must be an object, got {type(obj).__name__}"
        )
    return obj


def load_and_normalize(file_path: Path) -> RecordIn:
    """JSON 파일 → ``RecordIn``. ``source_file`` 이 비어있으면 파일명을 채운다."""
    raw = load_json(file_path)
    record = normalize(raw)
    if not record.source_file:
        record = record.model_copy(update={"source_file": file_path.name})
    return record


def copy_figures(
    doc_id: str,
    *,
    source_root: Path,
    figures_dir: Path,
) -> int:
    """``{source_root}/{doc_id}/`` 가 존재하면 ``{figures_dir}/{doc_id}/`` 로 복사한다.

    멱등 — 이미 복사되어 있어도 같은 동작을 안전하게 반복한다.
    원본 폴더가 없으면 0 을 반환하고 조용히 통과한다.

    Returns:
        복사된 파일 개수 (디렉터리/하위 디렉터리 제외).
    """
    src_dir = (source_root / doc_id).resolve()
    if not src_dir.is_dir():
        return 0

    dst_dir = (figures_dir / doc_id).resolve()
    dst_dir.parent.mkdir(parents=True, exist_ok=True)

    # shutil.copytree 의 dirs_exist_ok=True 로 멱등 유지.
    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)

    # 복사된 파일 개수 (재귀)
    n = 0
    for p in dst_dir.rglob("*"):
        if p.is_file():
            n += 1
    logger.info("copied %d figure file(s) for %s -> %s", n, doc_id, dst_dir)
    return n


def copy_attachments(
    doc_id: str,
    *,
    source_root: Path,
    attachments_dir: Path,
) -> int:
    """``{source_root}/{doc_id}/`` 가 존재하면 ``{attachments_dir}/{doc_id}/`` 로 복사한다.

    ``copy_figures`` 의 일반화 — 같은 폴더 레이아웃을 공유한다 (한 doc 의
    figure / pdf / xlsx / step / dwg ... 가 모두 한 폴더 안에 평탄하게).
    멱등 동작이며 ``shutil.copytree(dirs_exist_ok=True)`` 로 cross-platform
    (Windows / Linux) 모두 안전하다. ``pathlib.Path`` 만 사용하므로 OS 별
    분리자 차이도 자동 흡수된다.

    Args:
        doc_id: 레코드 ID (예: ``"DOC-HE-CAE-2026-000001"``).
        source_root: 원본 루트 (예: 변환기 ``output_dir``).
        attachments_dir: 대상 루트 (보통 ``settings.attachments_dir``).

    Returns:
        복사된 파일 개수.
    """
    src_dir = (source_root / doc_id).resolve()
    if not src_dir.is_dir():
        return 0

    dst_dir = (attachments_dir / doc_id).resolve()
    dst_dir.parent.mkdir(parents=True, exist_ok=True)

    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)

    n = 0
    for p in dst_dir.rglob("*"):
        if p.is_file():
            n += 1
    logger.info(
        "copied %d attachment file(s) for %s -> %s", n, doc_id, dst_dir
    )
    return n


__all__ = [
    "copy_attachments",
    "copy_figures",
    "iter_json_files",
    "load_and_normalize",
    "load_json",
]
