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
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.base import get_session
from ..errors import APIError, ValidationError
from ..ingest.db_writer import write_record
from ..ingest.loader import copy_attachments
from ..ingest.normalizer import normalize
from ..schemas.common import ACCESS_PATTERNS, DERIVATIONS, STATUSES
from ..services.converter_dispatch import (
    EXTENSION_MAP,
    ConvertRequest,
    SourceFormat,
    convert_file,
    detect_format,
)
from ..services.seq import next_seq

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


def _parse_date(raw: str | None) -> date | None:
    """ISO 날짜 문자열(``YYYY-MM-DD``) → :class:`datetime.date`.

    빈 문자열/None → ``None``. 형식 오류 시 :class:`ValidationError`.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError as exc:
        raise ValidationError(
            f"날짜 형식이 잘못되었습니다 (YYYY-MM-DD): {raw!r}",
            details={"value": raw},
        ) from exc


def _build_overrides(
    *,
    status_value: str | None,
    language: str | None,
    subject_keywords: str | None,
    derivation: str | None,
    quality_score: int | None,
    valid_from: str | None,
    valid_until: str | None,
    title_override: str | None,
    summary_override: str | None,
    agent_hints: str | None = None,
    related_record_ids: str | None = None,
    parent_record_id: str | None = None,
    query_examples: str | None = None,
    access_pattern: str | None = None,
) -> dict[str, Any]:
    """확장 폼 필드 → ``RecordIn.model_copy(update=...)`` 인자 dict.

    빈 값(None / 빈 문자열 / 빈 리스트) 은 override 하지 않는다 — normalizer 결과
    또는 ``RecordIn`` 기본값을 유지하기 위함이다.

    추가로 ``status`` / ``derivation`` / ``quality_score`` 의 도메인 검증을 수행해
    422 ``VALIDATION_ERROR`` 로 변환한다 (Pydantic 검증보다 사용자 친화적 메시지).
    """
    overrides: dict[str, Any] = {}

    if status_value:
        if status_value not in STATUSES:
            raise ValidationError(
                f"status 값이 잘못되었습니다: {status_value!r}",
                details={"allowed": list(STATUSES), "value": status_value},
            )
        overrides["status"] = status_value

    if language:
        overrides["language"] = language

    kw = _split_csv(subject_keywords or "")
    if kw:
        overrides["subject_keywords"] = kw

    if derivation:
        if derivation not in DERIVATIONS:
            raise ValidationError(
                f"derivation 값이 잘못되었습니다: {derivation!r}",
                details={"allowed": list(DERIVATIONS), "value": derivation},
            )
        overrides["derivation"] = derivation

    if quality_score is not None:
        if not (0 <= int(quality_score) <= 100):
            raise ValidationError(
                "quality_score 는 0~100 범위여야 합니다",
                details={"value": quality_score},
            )
        overrides["quality_score"] = int(quality_score)

    vf = _parse_date(valid_from)
    vu = _parse_date(valid_until)
    if vf is not None and vu is not None and vf > vu:
        raise ValidationError(
            "valid_from 은 valid_until 이전이어야 합니다",
            details={"valid_from": str(vf), "valid_until": str(vu)},
        )
    if vf is not None:
        overrides["valid_from"] = vf
    if vu is not None:
        overrides["valid_until"] = vu

    if title_override and title_override.strip():
        overrides["title"] = title_override.strip()
    if summary_override and summary_override.strip():
        overrides["summary"] = summary_override.strip()

    # ---- Agent discovery hints (Migration 0007) ---------------------------
    if agent_hints and agent_hints.strip():
        overrides["agent_hints"] = agent_hints.strip()

    related = _split_csv(related_record_ids or "")
    if related:
        overrides["related_record_ids"] = related

    if parent_record_id and parent_record_id.strip():
        overrides["parent_record_id"] = parent_record_id.strip()

    examples = _split_csv(query_examples or "")
    if examples:
        overrides["query_examples"] = examples

    if access_pattern:
        if access_pattern not in ACCESS_PATTERNS:
            raise ValidationError(
                f"access_pattern 값이 잘못되었습니다: {access_pattern!r}",
                details={
                    "allowed": list(ACCESS_PATTERNS),
                    "value": access_pattern,
                },
            )
        overrides["access_pattern"] = access_pattern

    return overrides


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
    team: str = Form(...),
    group: str = Form(...),
    year: int = Form(...),
    seq: int = Form(0),
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
            team=team,
            group=group,
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
    team: str = Form(...),
    group: str = Form(...),
    year: int = Form(...),
    seq: int = Form(0),
    tags: str = Form(""),
    agents: str = Form(""),
    classification: str = Form("internal"),
    domain: str | None = Form(None),
    # ---- 확장 메타 (metadata_spec.md §1) -------------------------------
    status_field: str = Form("draft", alias="status"),
    language: str = Form("ko"),
    subject_keywords: str = Form(""),
    derivation: str = Form("original"),
    quality_score: int | None = Form(None),
    valid_from: str = Form(""),
    valid_until: str = Form(""),
    title_override: str = Form(""),
    summary_override: str = Form(""),
    # ---- Agent discovery hints (Migration 0007) -----------------------
    agent_hints: str = Form(""),
    related_record_ids: str = Form(""),
    parent_record_id: str = Form(""),
    query_examples: str = Form(""),
    access_pattern: str = Form(""),
    persist_attachments: bool = Form(True),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """업로드 → 변환 → ``write_record()`` 호출. 레코드 요약 반환.

    Args:
        seq: 시퀀스 번호. 0 또는 미지정이면 ``MAX(seq)+1`` 자동 할당.
        persist_attachments: True (기본) 면 변환 산출물의 ``{record_id}/`` 폴더
            를 ``settings.attachments_dir`` 로 복사한다.
    """
    _validate_extension(file.filename or "")
    max_bytes = settings.max_upload_mb * 1024 * 1024

    # 폼 입력 단계 검증 (도메인 enum / 날짜 / quality_score) — 라우터에서
    # 422 로 빠르게 변환하기 위해 normalize 전에 수행.
    overrides = _build_overrides(
        status_value=status_field,
        language=language,
        subject_keywords=subject_keywords,
        derivation=derivation,
        quality_score=quality_score,
        valid_from=valid_from,
        valid_until=valid_until,
        title_override=title_override,
        summary_override=summary_override,
        agent_hints=agent_hints,
        related_record_ids=related_record_ids,
        parent_record_id=parent_record_id,
        query_examples=query_examples,
        access_pattern=access_pattern,
    )

    work_dir = _make_temp_dir()
    try:
        saved = await _save_upload(file, work_dir, max_bytes=max_bytes)
        fmt = detect_format(saved.name)

        # ---- S1. auto-seq -----------------------------------------------
        # seq=0 (또는 음수) → backend 가 (data_type, team, group, year)
        # 튜플 단위로 ``MAX(seq)+1`` 을 할당한다. 단일-writer 가정.
        # data_type 은 포맷에서 추정한다 (DOCX/PPTX/MD/PDF→DOC, XLSX→DATA).
        effective_seq = seq
        if effective_seq is None or int(effective_seq) <= 0:
            inferred_dt = "DATA" if fmt == SourceFormat.XLSX else "DOC"
            effective_seq = await next_seq(
                session,
                data_type=inferred_dt,
                team=team,
                group=group,
                year=year,
            )
            log.info(
                "auto-seq assigned: type=%s team=%s group=%s year=%s -> seq=%d",
                inferred_dt,
                team,
                group,
                year,
                effective_seq,
            )

        req = ConvertRequest(
            team=team,
            group=group,
            year=year,
            seq=effective_seq,
            tags=_split_csv(tags),
            agents=_split_csv(agents),
            classification=classification,
            domain=domain,
            output_dir=work_dir / "out",
        )
        log.info(
            "convert_and_ingest: file=%s fmt=%s seq=%s",
            saved.name,
            fmt.value,
            effective_seq,
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

        # 확장 폼 필드 머지 — 비어있는 필드는 normalizer 결과 / 기본값 유지.
        if overrides:
            try:
                record_in = record_in.model_copy(update=overrides)
            except Exception as exc:  # pragma: no cover — model_copy 는 거의 실패하지 않음
                raise ValidationError(
                    f"override 적용 실패: {exc}",
                    details={"overrides": {k: str(v) for k, v in overrides.items()}},
                ) from exc

        write_result = await write_record(session, record_in)
        await session.commit()

        record = write_result.record

        # ---- S5. attachment binary persistence --------------------------
        attachments_copied = 0
        if persist_attachments:
            try:
                attachments_copied = copy_attachments(
                    record.id,
                    source_root=work_dir / "out",
                    attachments_dir=settings.attachments_dir,
                )
            except Exception as exc:  # noqa: BLE001 — 첨부 복사 실패는 인제스트를 막지 않는다.
                log.warning(
                    "attachment persistence failed for %s: %s",
                    record.id,
                    exc,
                )

        # ---- S4. auto-embedding trigger (best-effort) -------------------
        if write_result.action in ("inserted", "updated"):
            try:
                from ..services.jobs import maybe_schedule_auto_embed

                maybe_schedule_auto_embed(record.id)
            except Exception as exc:  # noqa: BLE001
                log.debug("auto-embed schedule skipped: %s", exc)

        body: dict[str, Any] = {
            "record_id": record_in.id,
            "status": write_result.action,
            "sections_written": write_result.sections_written,
            "assigned_seq": int(effective_seq),
            "attachments_persisted": int(attachments_copied),
            "record": {
                "id": record.id,
                "data_type": record.data_type,
                "title": record.title,
                "summary": record.summary,
                "tags": list(record.tags or []),
                "agents": list(record.agents or []),
                "team": record.team,
                "group": record.group,
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
