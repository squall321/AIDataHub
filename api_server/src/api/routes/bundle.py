"""``/api/ingest/bundle`` — 사전 변환된 JSON + 자원 폴더 번들 적재.

변환기 (`api_server/src/{converter,excel_converter,...}`) 가 출력한
``output/{doc_id}.json`` + ``output/{doc_id}/`` (자원 폴더) 를 한꺼번에
업로드해 DB 적재 + 정적 마운트 디렉터리에 배치하는 엔드포인트.

``/api/convert/ingest`` 와의 차이:

- ``/api/convert/ingest`` — 원본 파일(.docx/.pptx) 를 받아 **서버에서**
  변환기 실행 후 적재.
- ``/api/ingest/bundle`` — **이미 변환된** JSON + 자원 폴더를 zip 으로
  받아 변환 단계를 skip 하고 바로 DB + 정적 마운트로 흘려 보냄.

지원 zip 레이아웃 (둘 다 허용):

(A) 변환기 출력 컨벤션 (권장):

    bundle.zip
    ├── DOC-HE-CAE-2026-0000000001.json    ← 메인 JSON (이름 = doc_id 또는 자유)
    └── DOC-HE-CAE-2026-0000000001/        ← 자원 폴더 (이름 = doc_id 정확히)
        ├── F001.png
        ├── F002.png
        └── A001.xlsx

(B) 평탄화 컨벤션 (zip 압축 단순화):

    bundle.zip
    ├── record.json                    ← 메인 JSON (이름 무관)
    ├── F001.png                       ← 자원 (basename 만)
    ├── F002.png
    └── A001.xlsx

서버 동작:

1. zip 임시 디렉터리에 해제.
2. ``.json`` 파일 1개 탐색 (둘 이상이면 422 거부).
3. ``normalize(json)`` → RecordIn → ``record.id`` 도출.
4. 자원 파일 위치 확정:
   - (A) 형태면 ``{tmp}/{doc_id}/`` 디렉터리 그대로.
   - (B) 형태면 ``.json`` 외 모든 파일을 ``{tmp}/{doc_id}/`` 로 자동 이동.
5. ``write_record(session, record_in)`` → DB 적재.
6. ``copy_figures`` / ``copy_attachments`` 로 ``figures_dir`` /
   ``attachments_dir`` 에 자원 복사.
7. 임시 디렉터리 정리.
8. 결과 manifest 반환 (record id, copied file 개수, 경고).

검증:

- JSON 의 ``figures[].image_path`` / ``attachments[].file_path`` 가
  실제 zip 안에 존재하는지 확인 — 누락 시 warnings 에 기록 (거부 X).
- zip 안에 있는데 JSON 이 참조하지 않는 파일도 warnings 에 기록.

크기 제한:

- ``settings.max_upload_mb`` (기본 50MB) — multipart 자체.
- zip 압축 해제 후 합산 크기는 ``MAX_BUNDLE_BYTES`` (기본 200MB) 로
  추가 검증 (zip bomb 방지).
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.base import get_session
from ..errors import APIError, ValidationError
from ..ingest.db_writer import write_record
from ..ingest.loader import copy_attachments, copy_figures, load_json
from ..ingest.normalizer import normalize

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_BUNDLE_BYTES = 200 * 1024 * 1024  # 200MB 합산 (zip bomb 방지)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_extract(zip_path: Path, dest: Path) -> int:
    """zip 안전 해제. path traversal 방지 + 합산 크기 제한.

    Returns:
        해제된 총 바이트 수.

    Raises:
        ValidationError: traversal 시도, 절대경로, 크기 초과.
    """
    total_bytes = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            # path traversal / absolute path 거부
            name = info.filename
            if name.startswith("/") or name.startswith("\\"):
                raise ValidationError(f"absolute path in zip: {name!r}")
            if ".." in Path(name).parts:
                raise ValidationError(f"path traversal in zip: {name!r}")
            if info.file_size > MAX_BUNDLE_BYTES:
                raise ValidationError(
                    f"single file in zip exceeds {MAX_BUNDLE_BYTES} bytes: {name!r}"
                )
            total_bytes += info.file_size
            if total_bytes > MAX_BUNDLE_BYTES:
                raise ValidationError(
                    f"zip uncompressed total exceeds {MAX_BUNDLE_BYTES} bytes"
                )
        zf.extractall(dest)
    return total_bytes


def _find_main_json(root: Path) -> Path:
    """zip 해제 폴더에서 메인 JSON 찾기.

    규칙:
    - 루트 (또는 첫 번째 단일 하위 폴더 — 일부 zip 도구가 만드는
      "wrapper" 폴더를 자동으로 벗겨낸다) 에서 ``*.json`` 1개만 허용.
    - ``.warnings.log`` 등 부수 파일은 무시.
    """
    # zip 도구가 단일 wrapper 폴더로 묶었을 수 있음 — 자동 strip
    entries = [p for p in root.iterdir() if not p.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        # 단일 하위 디렉터리만 있으면 그 안을 root 로 간주
        root = entries[0]

    json_files = [
        p for p in root.iterdir()
        if p.is_file() and p.suffix.lower() == ".json"
        and not p.name.endswith(".warnings.log")
    ]
    if len(json_files) == 0:
        raise ValidationError(
            "bundle must contain exactly one .json file at top level"
        )
    if len(json_files) > 1:
        raise ValidationError(
            f"bundle has {len(json_files)} .json files; expected exactly one: "
            f"{[p.name for p in json_files]}"
        )
    return json_files[0]


def _stage_resources(extract_root: Path, json_path: Path, doc_id: str) -> Path:
    """자원 파일을 ``{extract_root}/{doc_id}/`` 형태로 정규화.

    레이아웃 (A): 이미 ``{extract_root}/{doc_id}/`` 가 있으면 그대로.
    레이아웃 (B): 평탄 — JSON 외 모든 파일을 ``{doc_id}/`` 로 이동.

    Returns:
        ``{extract_root}/{doc_id}/`` 경로 (자원 폴더). 자원이 없으면
        디렉터리는 만들어두되 빈 폴더로 둔다.
    """
    json_root = json_path.parent
    target = json_root / doc_id

    # 이미 (A) 형태로 폴더가 있으면 추가 동작 없음
    if target.is_dir():
        return target

    target.mkdir(parents=True, exist_ok=True)

    # (B) — JSON 외 모든 파일을 target 으로 이동
    for p in list(json_root.iterdir()):
        if p == target:
            continue
        if p == json_path:
            continue
        if p.name.endswith(".warnings.log"):
            continue
        if p.is_file():
            shutil.move(str(p), str(target / p.name))
        # 하위 폴더 (다른 컨벤션으로 묶인) 는 무시 — 위 (A) 검사가 우선.

    return target


def _validate_resource_references(
    record_id: str,
    raw: dict[str, Any],
    resources_dir: Path,
) -> tuple[list[str], list[str]]:
    """JSON 내 참조 vs 실제 파일 cross-check.

    Returns:
        (missing, extra) — JSON 참조했으나 zip 에 없는 파일 / zip 에는
        있으나 JSON 이 참조 안 한 파일.
    """
    referenced: set[str] = set()
    for fig in raw.get("figures") or []:
        path = fig.get("image_path")
        if isinstance(path, str) and path:
            # path 는 "{doc_id}/F001.png" 형태 — basename 만 추출
            referenced.add(Path(path).name)
    for att in raw.get("attachments") or []:
        path = att.get("file_path")
        if isinstance(path, str) and path:
            referenced.add(Path(path).name)
    # Excel data.v1 의 경우 figures/attachments 가 raw["content"] 안에 있을 수도
    content = raw.get("content")
    if isinstance(content, dict):
        for fig in content.get("figures") or []:
            path = fig.get("image_path")
            if isinstance(path, str) and path:
                referenced.add(Path(path).name)
        for att in content.get("attachments") or []:
            path = att.get("file_path")
            if isinstance(path, str) and path:
                referenced.add(Path(path).name)

    actual: set[str] = set()
    if resources_dir.is_dir():
        for p in resources_dir.iterdir():
            if p.is_file():
                actual.add(p.name)

    missing = sorted(referenced - actual)
    extra = sorted(actual - referenced)
    return missing, extra


# ---------------------------------------------------------------------------
# POST /api/ingest/bundle
# ---------------------------------------------------------------------------
@router.post(
    "/bundle",
    summary="ZIP 번들 업로드 → JSON + 자원 적재",
    status_code=status.HTTP_201_CREATED,
)
async def ingest_bundle(
    file: UploadFile = File(..., description="ZIP 번들 (.zip)"),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """ZIP 번들 적재.

    Body: multipart/form-data
        file: zip 파일 (변환기 출력 폴더 통째로 압축한 것).

    Response 201::

        {
          "id": "DOC-HE-CAE-2026-0000000001",
          "data_type": "DOC",
          "title": "...",
          "figures_copied": 12,
          "attachments_copied": 3,
          "warnings": {
            "missing_resources": [...],   // JSON 이 참조하나 zip 에 없음
            "extra_resources": [...]      // zip 에 있으나 JSON 이 참조 안 함
          }
        }
    """
    # ---- 입력 검증 -------------------------------------------------------
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise ValidationError("file must be a .zip bundle")

    # ---- 임시 디렉터리에 zip 저장 + 해제 ---------------------------------
    tmp_root = Path(tempfile.mkdtemp(prefix="aidh-bundle-"))
    try:
        zip_path = tmp_root / "bundle.zip"
        # 스트리밍 저장 (multipart 자체 크기는 settings.max_upload_mb 가 제한)
        size = 0
        max_upload = settings.max_upload_mb * 1024 * 1024
        with zip_path.open("wb") as out:
            while chunk := await file.read(64 * 1024):
                size += len(chunk)
                if size > max_upload:
                    raise ValidationError(
                        f"upload exceeds {settings.max_upload_mb} MB"
                    )
                out.write(chunk)

        extract_root = tmp_root / "ext"
        extract_root.mkdir()
        try:
            extracted_bytes = _safe_extract(zip_path, extract_root)
        except zipfile.BadZipFile as exc:
            raise ValidationError(f"invalid zip: {exc}") from exc

        log.info(
            "bundle uploaded: %s size=%d bytes uncompressed=%d bytes",
            file.filename, size, extracted_bytes,
        )

        # ---- 메인 JSON 탐색 + load --------------------------------------
        json_path = _find_main_json(extract_root)
        raw = load_json(json_path)

        # ---- normalize → RecordIn ---------------------------------------
        try:
            record_in = normalize(raw)
        except (ValueError, TypeError) as exc:
            raise ValidationError(
                f"normalize failed: {exc}"
            ) from exc

        doc_id = record_in.id

        # ---- 자원 폴더 정규화 (A or B) ----------------------------------
        resources_dir = _stage_resources(
            extract_root, json_path, doc_id
        )

        # ---- 참조 cross-check (warn-only) -------------------------------
        missing, extra = _validate_resource_references(
            doc_id, raw, resources_dir
        )
        if missing:
            log.warning(
                "bundle %s: referenced resources not in zip: %s",
                doc_id, missing
            )
        if extra:
            log.info(
                "bundle %s: zip has resources not referenced by JSON: %s",
                doc_id, extra
            )

        # ---- DB 적재 ----------------------------------------------------
        try:
            await write_record(session, record_in)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            log.exception("write_record failed for %s: %s", doc_id, exc)
            raise APIError(
                code="DB_WRITE_FAILED",
                message=f"failed to persist record {doc_id}: {exc}",
                status_code=500,
            ) from exc

        # ---- 정적 마운트 디렉터리에 자원 복사 ---------------------------
        # source_root 는 resources_dir 의 부모 (= json_path 부모) — copy_*
        # 함수가 ``{source_root}/{doc_id}/`` 를 찾아 복사한다.
        source_root = resources_dir.parent
        figures_copied = copy_figures(
            doc_id,
            source_root=source_root,
            figures_dir=settings.figures_dir,
        )
        attachments_copied = copy_attachments(
            doc_id,
            source_root=source_root,
            attachments_dir=settings.attachments_dir,
        )

        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content={
                "id": doc_id,
                "data_type": record_in.data_type,
                "title": record_in.title,
                "figures_copied": figures_copied,
                "attachments_copied": attachments_copied,
                "warnings": {
                    "missing_resources": missing,
                    "extra_resources": extra,
                },
            },
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


__all__ = ["router"]
