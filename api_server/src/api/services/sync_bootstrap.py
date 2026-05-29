"""부팅 시 ``config/sync_sources.yml`` 기반 sync_sources 자동 등록·갱신.

운영 원칙:
    - **idempotent**: 같은 yaml 로 N번 부팅해도 결과 동일.
    - **preserve unknown**: yaml 에 없고 DB 에만 있는 source 는 그대로 유지
      (사용자가 REST 로 직접 등록한 항목 보호).
    - **secret 분리**: api_key 는 yaml 에 평문 X. ``api_key_env`` 키로 환경변수
      이름만 명시.
    - **변경 추적**: name 으로 매칭 → 기존이면 PATCH (logging 으로 변경 항목 기록).
    - **best-effort**: 한 source 실패해도 다른 source 진행. 모든 실패 로그.

환경변수:
    AIDH_SYNC_CONFIG_FILE       — yaml 경로 (기본 config/sync_sources.yml)
    AIDH_SYNC_BOOTSTRAP=false   — 비활성. 기본은 yaml 있으면 자동 실행.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import SyncSource

logger = logging.getLogger(__name__)

# yaml 의 ${VAR} 환경변수 치환 패턴
_ENV_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

# yaml → DB 컬럼 매핑 (api_key_env 는 별도 처리)
_DIRECT_FIELDS = (
    "description", "base_url", "auth_header", "list_endpoint", "list_method",
    "detail_endpoint", "cursor_param", "since_param", "limit_param",
    "page_size", "max_rps", "retry_max", "retry_backoff_sec",
    "trust_pii_masked", "schedule_cron", "enabled",
)


def _resolve_env_substitutions(obj: Any) -> Any:
    """YAML 문자열의 ``${VAR}`` 를 환경변수 값으로 치환. 누락은 빈 문자열."""
    if isinstance(obj, str):
        return _ENV_VAR_RE.sub(
            lambda m: os.environ.get(m.group(1), ""),
            obj,
        )
    if isinstance(obj, dict):
        return {k: _resolve_env_substitutions(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_substitutions(x) for x in obj]
    return obj


def _resolve_api_key(spec: dict[str, Any]) -> tuple[str | None, str | None]:
    """api_key 소스 결정 우선순위:
        1. api_key_env (환경변수 이름)
        2. api_key_file (파일 경로 — Docker secret 등)
        3. (비권장) api_key 평문

    Returns: (api_key 값 or None, 경고 메시지 or None)
    """
    if env_var := spec.get("api_key_env"):
        val = os.environ.get(env_var)
        if not val:
            return None, f"api_key_env={env_var!r} not set"
        return val, None
    if file_path := spec.get("api_key_file"):
        try:
            return Path(file_path).read_text(encoding="utf-8").strip(), None
        except OSError as exc:
            return None, f"api_key_file={file_path!r} unreadable: {exc}"
    if plain := spec.get("api_key"):
        return str(plain), "api_key 평문 사용 — api_key_env / api_key_file 권장"
    return None, None


def _spec_to_kwargs(spec: dict[str, Any]) -> dict[str, Any]:
    """yaml dict → SyncSource constructor kwargs."""
    kwargs: dict[str, Any] = {"name": spec["name"]}
    for k in _DIRECT_FIELDS:
        if k in spec:
            kwargs[k] = spec[k]
    if "mapping_rules" in spec:
        kwargs["mapping_rules"] = spec["mapping_rules"] or {}
    return kwargs


async def bootstrap_sync_sources(
    session: AsyncSession,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """yaml 1개를 읽어 sync_sources 에 idempotent 반영.

    Returns:
        {created: [name...], updated: [name...], unchanged: [...], errors: [...]}
    """
    if os.environ.get("AIDH_SYNC_BOOTSTRAP", "true").lower() in ("false", "0", "no"):
        logger.info("sync_bootstrap: AIDH_SYNC_BOOTSTRAP=false — skip")
        return {"created": [], "updated": [], "unchanged": [], "errors": [], "skipped": True}

    path = Path(
        config_path
        or os.environ.get("AIDH_SYNC_CONFIG_FILE")
        or "config/sync_sources.yml"
    )
    if not path.is_absolute():
        # repo root 기준 — api_server/src/api/services/sync_bootstrap.py 에서 parents[4] 가 repo root
        repo_root = Path(__file__).resolve().parents[4]
        path = repo_root / path

    if not path.exists():
        logger.info("sync_bootstrap: %s 없음 — skip", path)
        return {"created": [], "updated": [], "unchanged": [], "errors": [], "skipped": True}

    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("sync_bootstrap: PyYAML not installed — skip")
        return {"created": [], "updated": [], "unchanged": [], "errors": ["pyyaml missing"]}

    try:
        body = path.read_text(encoding="utf-8")
        resolved = _resolve_env_substitutions(body)
        data = yaml.safe_load(resolved) or {}
    except Exception as exc:
        logger.error("sync_bootstrap: %s 읽기 실패: %s", path, exc)
        return {"created": [], "updated": [], "unchanged": [], "errors": [str(exc)]}

    specs = data.get("sources") or []
    if not isinstance(specs, list):
        return {"created": [], "updated": [], "unchanged": [], "errors": ["sources must be a list"]}

    created: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    errors: list[str] = []

    for spec in specs:
        if not isinstance(spec, dict) or not spec.get("name"):
            errors.append(f"invalid spec (missing name): {str(spec)[:80]}")
            continue
        name = spec["name"]
        try:
            kwargs = _spec_to_kwargs(spec)
            api_key, warn = _resolve_api_key(spec)
            if warn:
                logger.warning("sync_bootstrap [%s]: %s", name, warn)
            if api_key is not None:
                kwargs["api_key"] = api_key

            existing = (
                await session.execute(
                    select(SyncSource).where(SyncSource.name == name)
                )
            ).scalar_one_or_none()

            if existing is None:
                src = SyncSource(**kwargs)
                session.add(src)
                await session.flush()
                created.append(name)
                logger.info(
                    "sync_bootstrap [%s]: created (base_url=%s, has_api_key=%s)",
                    name, kwargs.get("base_url"), bool(api_key),
                )
            else:
                # 변경 항목만 적용
                changes: list[str] = []
                for k, v in kwargs.items():
                    if k == "name":
                        continue
                    cur = getattr(existing, k, None)
                    if cur != v:
                        setattr(existing, k, v)
                        # api_key 와 mapping_rules 는 값 노출 X — 변경 사실만 기록
                        if k in ("api_key", "mapping_rules"):
                            changes.append(k)
                        else:
                            changes.append(f"{k}={v!r}")
                if changes:
                    updated.append(name)
                    logger.info(
                        "sync_bootstrap [%s]: updated — %s",
                        name, ", ".join(changes),
                    )
                else:
                    unchanged.append(name)

            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.exception("sync_bootstrap [%s]: failed", name)
            errors.append(f"{name}: {exc}")

    logger.info(
        "sync_bootstrap done: created=%s updated=%s unchanged=%s errors=%s",
        len(created), len(updated), len(unchanged), len(errors),
    )
    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "errors": errors,
        "config_file": str(path),
    }


__all__ = ["bootstrap_sync_sources"]
