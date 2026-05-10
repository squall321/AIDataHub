"""감사 로그 서비스 (Agent 31, Migration 0008).

핵심 헬퍼 ``log_action`` 으로 INSERT/UPDATE/DELETE/RESTORE/ACCESS/VIEW
이벤트를 ``audit_log`` 테이블에 한 행 추가한다.

설계 원칙:
    - 로그 실패는 비즈니스 로직을 깨뜨리지 않아야 한다 → 모든 호출은 try/except
      로 감싸 호출자에서 ``best_effort=True`` 옵션으로 무시할 수 있다.
    - field_changes 는 ``{field: [old, new]}`` 형태. ``compute_diff`` 헬퍼 제공.
    - JSON 직렬화 가능한 값만 보관 (datetime/date/UUID 등은 str 로 캐스트).
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 기록되는 action 라벨 (validation 용도, 자유 문자열도 허용).
KNOWN_ACTIONS: tuple[str, ...] = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "RESTORE",
    "ACCESS",
    "VIEW",
)


def _to_jsonable(value: Any) -> Any:
    """JSON 직렬화 안전한 형태로 변환.

    - datetime/date → ISO8601 문자열
    - UUID → str
    - set/tuple → list (재귀)
    - 그 외는 그대로 반환 (json.dumps 에 넘겨 실패 시 호출 측 책임).
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(x) for x in value]
    # fallback: str() 캐스트로 안전 보장.
    return str(value)


def compute_diff(
    old: dict[str, Any] | None,
    new: dict[str, Any] | None,
    *,
    fields: list[str] | None = None,
) -> dict[str, list[Any]]:
    """두 dict 간 변경 필드를 ``{field: [old, new]}`` 로 반환.

    - ``fields`` 가 주어지면 그 키만 비교.
    - None vs '' 는 동일하지 않으며 그대로 변경으로 본다.
    """
    old = old or {}
    new = new or {}
    keys: list[str] = list(fields) if fields else sorted(set(old) | set(new))
    diff: dict[str, list[Any]] = {}
    for k in keys:
        ov = old.get(k)
        nv = new.get(k)
        if ov != nv:
            diff[k] = [_to_jsonable(ov), _to_jsonable(nv)]
    return diff


async def log_action(
    session: AsyncSession,
    *,
    action: str,
    record_id: str | None = None,
    actor: str | None = None,
    field_changes: dict[str, Any] | None = None,
    request_id: str | None = None,
    best_effort: bool = True,
) -> None:
    """``audit_log`` 테이블에 한 이벤트 추가.

    호출자가 ``await session.commit()`` 을 책임진다 (같은 트랜잭션에 묶임).

    Args:
        session: 활성 AsyncSession.
        action: INSERT|UPDATE|DELETE|RESTORE|ACCESS|VIEW 또는 자유 문자열.
        record_id: 대상 레코드 ID (글로벌 이벤트는 None).
        actor: API 키 이름 / 'system' / 'cli' 등.
        field_changes: ``{field: [old, new], ...}``.
        request_id: 미들웨어가 생성한 X-Request-ID.
        best_effort: True 면 모든 예외를 삼키고 로깅만 한다.
    """
    from ..db.models import AuditLog

    try:
        row = AuditLog(
            record_id=record_id,
            actor=actor or "system",
            action=str(action).upper() if action else "UNKNOWN",
            field_changes=_to_jsonable(field_changes or {}),
            request_id=request_id,
        )
        session.add(row)
        # flush 까지만 — commit 은 호출자가.
        await session.flush()
    except Exception as exc:  # pragma: no cover - audit failure must not block
        if best_effort:
            logger.warning("audit log failed: %s (action=%s record=%s)", exc, action, record_id)
            return
        raise


def record_snapshot(rec: Any, fields: list[str] | None = None) -> dict[str, Any]:
    """ORM Record 인스턴스에서 감사 비교용 dict 스냅샷 생성.

    ``fields`` 미지정 시 기본 메타데이터 필드를 캡처. 본문 ``content``,
    ``content_hash`` 는 그래프가 큰 경우 비교가 비싸므로 별도 키로만 표시.
    """
    default_fields = [
        "title",
        "summary",
        "tags",
        "agents",
        "data_type",
        "team",
        "group",
        "year",
        "seq",
        "schema_version",
        "version",
        "author",
        "department",
        "project",
        "classification",
        "status",
        "domain",
        "subject_keywords",
        "source_system",
        "language",
        "parent_record_id",
        "derivation",
        "capabilities",
        "quality_score",
        "valid_from",
        "valid_until",
        "content_hash",
    ]
    use_fields = fields or default_fields
    snap: dict[str, Any] = {}
    for f in use_fields:
        if hasattr(rec, f):
            snap[f] = getattr(rec, f)
    return snap


__all__ = [
    "KNOWN_ACTIONS",
    "compute_diff",
    "log_action",
    "record_snapshot",
]
