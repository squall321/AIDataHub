"""``/api/convert`` — 파일 업로드 → 변환 → (선택) DB 적재.

엔드포인트:
    - ``POST /api/convert/``        : 변환만, JSON 반환.
    - ``POST /api/convert/ingest``  : 변환 + DB INSERT/UPDATE, 레코드 요약 반환.

동작 흐름:
    1. ``UploadFile`` 을 임시 디렉터리에 저장 (스트리밍 + 크기 검증).
    2. 확장자로 ``SourceFormat`` 판정.
    3. ``converter_dispatch.convert_file`` 호출.
    4. ``/api/convert/`` 는 결과 dict 그대로 반환.
       ``/api/convert/ingest`` 는 ``ingest.normalizer.normalize`` → ``db_writer.write_record``
       을 거쳐 DB 에 영속화하고 요약 반환.
    5. ``finally`` 절에서 임시 파일/폴더 정리.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.base import get_session
from ..errors import APIError, ValidationError
from ..ingest.db_writer import write_record
from ..ingest.normalizer import normalize
from ..services.converter_dispatch import (
    EXTENSION_MAP,
    ConvertRequest,
    convert_file,
    detect_format,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/convert", tags=["convert"])


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------
def _resolve_413_status() -> int:
    # Starlette ≥ 0.36 renamed 413 to CONTENT_TOO_LARGE; keep a fallback for older.
    if hasattr(status, "HTTP_413_CONTENT_TOO_LARGE"):
        return status.HTTP_413_CONTENT_TOO_LARGE
    if hasattr(status, "HTTP_413_REQUEST_ENTITY_TOO_LARGE"):
        return status.HTTP_413_REQUEST_ENTITY_TOO_LARGE  # noqa: SLF001
    return 413


class PayloadTooLargeError(APIError):
    status_code = _resolve_413_status()
    code = "PAYLOAD_TOO_LARGE"


def _split_csv(raw: str) -> list[str]:
    """``"a,b,c"`` → ``["a", "b", "c"]`` (공백/빈 토큰 제거)."""
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def _make_temp_dir() -> Path:
    """업로드용 임시 폴더를 만든다 (구성된 ``upload_temp_dir`` 하위)."""
    base = Path(settings.upload_temp_dir)
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="upload_", dir=str(base)))


async def _save_upload(
    upload: UploadFile,
    dest_dir: Path,
    *,
    max_bytes: int,
) -> Path:
    """``UploadFile`` 을 ``dest_dir`` 에 청크 단위로 저장하면서 크기를 검증.

    Returns:
        저장된 파일 경로.

    Raises:
        ValidationError: 파일명이 비어있을 때.
        PayloadTooLargeError: 누적 바이트 > ``max_bytes``.
    """
    if not upload.filename:
        raise ValidationError("파일명이 비어 있습니다")

    dest = dest_dir / Path(upload.filename).name  # path traversal 방지
    total = 0
    chunk_size = 1 << 20  # 1 MiB

    with dest.open("wb") as fh:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                fh.close()
                try:
                    dest.unlink(missing_ok=True)
                except OSError:
                    pass
                raise PayloadTooLargeError(
                    f"업로드 크기 초과: {total} bytes > {max_bytes} bytes",
                    details={"max_bytes": max_bytes, "received_bytes": total},
                )
            fh.write(chunk)

    if total == 0:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise ValidationError("빈 파일은 업로드할 수 없습니다")

    return dest


def _cleanup(path: Path) -> None:
    """임시 디렉터리 정리 — 실패해도 silent."""
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:  # pragma: no cover
        pass


def _validate_extension(filename: str) -> None:
    """확장자가 화이트리스트에 있는지 사전 검증.

    ``detect_format`` 도 동일한 체크를 하지만, 메시지의 명료성을 위해
    분리해 둔다.
    """
    ext = Path(filename).suffix.lower()
    if ext not in EXTENSION_MAP:
        from ..services.converter_dispatch import UnsupportedFormatError

        raise UnsupportedFormatError(
            f"확장자 {ext or '(없음)'} 미지원",
            details={
                "filename": filename,
                "extension": ext,
                "supported": sorted(EXTENSION_MAP.keys()),
            },
        )


# ---------------------------------------------------------------------------
# 라우트 — 변환만
# ---------------------------------------------------------------------------
@router.post("/", summary="파일 업로드 → 변환된 JSON 반환")
@router.post("", include_in_schema=False)
async def convert_only(
    file: UploadFile = File(...),
    division: str = Form(...),
    team: str = Form(...),
    year: int = Form(...),
    seq: int = Form(1),
    tags: str = Form(""),
    agents: str = Form(""),
    classification: str = Form("internal"),
    domain: str | None = Form(None),
) -> JSONResponse:
    """업로드 파일을 변환하고 결과 dict 를 그대로 돌려준다 (DB 적재 없음)."""
    _validate_extension(file.filename or "")
    max_bytes = settings.max_upload_mb * 1024 * 1024

    work_dir = _make_temp_dir()
    try:
        saved = await _save_upload(file, work_dir, max_bytes=max_bytes)
        fmt = detect_format(saved.name)
        req = ConvertRequest(
            division=division,
            team=team,
            year=year,
            seq=seq,
            tags=_split_csv(tags),
            agents=_split_csv(agents),
            classification=classification,
            domain=domain,
            output_dir=work_dir / "out",
        )
        log.info(
            "convert_only: file=%s fmt=%s size_bytes=%s",
            saved.name,
            fmt.value,
            saved.stat().st_size,
        )
        result = convert_file(saved, fmt, req)
        return JSONResponse(content=result, status_code=status.HTTP_200_OK)
    finally:
        _cleanup(work_dir)


# ---------------------------------------------------------------------------
# 라우트 — 변환 + 적재
# ---------------------------------------------------------------------------
@router.post("/ingest", summary="파일 업로드 → 변환 → DB 적재")
async def convert_and_ingest(
    file: UploadFile = File(...),
    division: str = Form(...),
    team: str = Form(...),
    year: int = Form(...),
    seq: int = Form(1),
    tags: str = Form(""),
    agents: str = Form(""),
    classification: str = Form("internal"),
    domain: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """업로드 → 변환 → ``write_record()`` 호출. 레코드 요약 반환."""
    _validate_extension(file.filename or "")
    max_bytes = settings.max_upload_mb * 1024 * 1024

    work_dir = _make_temp_dir()
    try:
        saved = await _save_upload(file, work_dir, max_bytes=max_bytes)
        fmt = detect_format(saved.name)
        req = ConvertRequest(
            division=division,
            team=team,
            year=year,
            seq=seq,
            tags=_split_csv(tags),
            agents=_split_csv(agents),
            classification=classification,
            domain=domain,
            output_dir=work_dir / "out",
        )
        log.info(
            "convert_and_ingest: file=%s fmt=%s",
            saved.name,
            fmt.value,
        )
        payload = convert_file(saved, fmt, req)

        # source_file 보강 — normalize 가 비어 있으면 채운다.
        if isinstance(payload, dict) and not payload.get("source_file"):
            payload["source_file"] = saved.name

        try:
            record_in = normalize(payload)
        except (ValueError, TypeError) as exc:
            raise ValidationError(
                f"정규화 실패: {exc}",
                details={"payload_keys": sorted(payload.keys()) if isinstance(payload, dict) else None},
            ) from exc

        write_result = await write_record(session, record_in)
        await session.commit()

        record = write_result.record
        body: dict[str, Any] = {
            "record_id": record_in.id,
            "status": write_result.action,
            "sections_written": write_result.sections_written,
            "record": {
                "id": record.id,
                "data_type": record.data_type,
                "title": record.title,
                "summary": record.summary,
                "tags": list(record.tags or []),
                "agents": list(record.agents or []),
                "division": record.division,
                "team": record.team,
                "year": record.year,
                "seq": record.seq,
                "source_file": record.source_file,
                "content_hash": record.content_hash,
            },
        }
        return JSONResponse(content=body, status_code=status.HTTP_200_OK)
    finally:
        _cleanup(work_dir)


__all__ = ["router"]
