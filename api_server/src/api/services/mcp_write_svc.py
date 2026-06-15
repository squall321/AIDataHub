"""MCP write 도구 오케스트레이션 — Claude Desktop drag&drop → 저장.

DESKTOP_MCP_MIGRATION_PLAN.md v2 Phase 0+1 구현.

설계 (적대적 검증 반영):
    - B1 stateless 멀티스텝 → 단일 진입점 :func:`run_import`. dry_run=True 면
      검증/되묻기, dry_run=False 면 저장. 각 호출은 **완전한 record dict** 를
      받으므로 서버 세션 상태가 필요 없다.
    - B3 team/group silent error → ``data_type/team/group`` 은 자동 채우지 않고
      ask_user 로 되묻는다. ``year`` 만 현재년도 자동기본 + ``auto_filled`` 로 보고.
    - B5 인증 → :func:`resolve_principal` 로 REST 와 동일 검증, actor 기록.
    - 에러 봉투 → ``{error, code, recoverable, suggestion}`` 표준.

이 모듈은 라우트 핸들러가 아니라 plain async 함수만 노출 — MCP 도구와
(필요시) REST 가 같은 로직을 공유한다 (단일 진실원천).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..auth.dependencies import resolve_principal
from ..db.base import SessionLocal
from ..errors import AuthenticationError

# auto_seq 채번에 필요한 필드 (id 가 없을 때). title 은 별도 항상 필수.
_AUTO_SEQ_FIELDS = ("data_type", "team", "group", "year")


def _scan_missing(record: dict[str, Any]) -> list[str]:
    """저장에 빠진 필수 필드를 **한 번에** 수집 (멀티 라운드트립 방지).

    _import_one 은 첫 에러에서 멈추므로, 되묻기 전에 여기서 전부 스캔한다.
    id 가 있으면 거기서 data_type/team/group/year 가 파싱되므로 title 만 본다.
    """
    missing: list[str] = []
    if not record.get("title"):
        missing.append("title")
    if not record.get("id"):
        for k in _AUTO_SEQ_FIELDS:
            if not record.get(k):
                missing.append(k)
    return missing


def _suggest(record: dict[str, Any]) -> dict[str, Any]:
    """빠진 필드의 제안값. confidence 동반 — 낮으면 Claude 가 반드시 되묻는다.

    title: content.caption > 첫 헤더 묶음 > None. 쓰레기 헤더(Unnamed 등)면 low.
    data_type: content 모양으로 추론 (headers+rows→DATA, sections→DOC).
    """
    sug: dict[str, Any] = {}
    content = record.get("content") or {}

    # title 제안
    if not record.get("title"):
        cap = (content.get("caption") or "").strip() if isinstance(content, dict) else ""
        if cap:
            sug["title"] = {"suggested": cap[:120], "confidence": "high", "reason": "content.caption"}
        else:
            headers = content.get("headers") if isinstance(content, dict) else None
            if isinstance(headers, list) and headers:
                clean = [str(h) for h in headers if h and not str(h).lower().startswith("unnamed")]
                if clean:
                    sug["title"] = {
                        "suggested": " · ".join(clean[:4]),
                        "confidence": "low",
                        "reason": "헤더 기반 — 사용자 확인 권장",
                    }

    # data_type 추론
    if not record.get("id") and not record.get("data_type") and isinstance(content, dict):
        if "headers" in content and "rows" in content:
            sug["data_type"] = {"suggested": "DATA", "confidence": "high", "reason": "headers+rows 구조"}
        elif "sections" in content:
            sug["data_type"] = {"suggested": "DOC", "confidence": "high", "reason": "sections 구조"}

    return sug


def _classify_error(outcome: dict[str, Any]) -> dict[str, Any]:
    """_import_one 의 error 문자열 → 표준 봉투 {code, recoverable, suggestion}.

    Claude 가 사용자에게 의미있게 설명할 수 있도록 코드화."""
    err = str(outcome.get("error") or "")
    low = err.lower()
    if "title is required" in low:
        return {"code": "missing_title", "recoverable": True,
                "suggestion": "제목(title)을 알려주세요."}
    if "auto_seq needs" in low or "id missing" in low:
        return {"code": "missing_id_fields", "recoverable": True,
                "suggestion": "data_type/team/group/year 를 알려주세요 (자동 ID 채번에 필요)."}
    if "invalid id" in low:
        return {"code": "invalid_id", "recoverable": True,
                "suggestion": "id 형식을 확인하세요 (예: DATA-HE-CAE-2026-0000000001)."}
    if "not registered" in low or "team" in low and "group" in low:
        return {"code": "team_not_found", "recoverable": False,
                "suggestion": "해당 team/group 이 조직에 등록돼 있지 않습니다. 운영자에게 등록을 요청하세요."}
    if "validation failed" in low:
        return {"code": "validation_error", "recoverable": True,
                "suggestion": "content 형식이 스키마와 맞지 않습니다. data_type 별 형태를 확인하세요."}
    if "integrity" in low or "duplicate" in low:
        return {"code": "duplicate", "recoverable": False,
                "suggestion": "이미 같은 ID 의 레코드가 있습니다."}
    return {"code": "import_error", "recoverable": False, "suggestion": err[:160]}


async def run_import(
    *,
    record: dict[str, Any],
    dry_run: bool = True,
    api_key: str | None = None,
    agent_type: str | None = None,
) -> dict[str, Any]:
    """drag&drop 된 record dict 를 검증(dry_run) 또는 저장.

    반환 봉투 (status 로 분기):
        - ``incomplete`` : 필수 필드 부족 → ``ask_user`` + ``suggestions``
        - ``ready``      : dry_run 통과, 저장 가능 → ``id`` / ``would``
        - ``saved``      : 실제 저장 완료 → ``id`` / ``action``
        - ``error``      : 실패 → ``error`` / ``code`` / ``recoverable`` / ``suggestion``
    """
    if not isinstance(record, dict):
        return {"status": "error", "error": "record must be an object",
                "code": "bad_input", "recoverable": False,
                "suggestion": "표/문서를 JSON 객체로 전달하세요."}

    rec = dict(record)  # 복사 (호출자 입력 불변)

    # year 만 현재년도 자동기본 (가시적으로 보고 — B3: team/group 은 자동채움 금지)
    auto_filled: dict[str, Any] = {}
    if not rec.get("id") and not rec.get("year"):
        rec["year"] = datetime.now(timezone.utc).year
        auto_filled["year"] = rec["year"]

    # 0) 부족 필드 사전 스캔 — 한 번에 되묻기
    missing = _scan_missing(rec)
    if missing:
        return {
            "status": "incomplete",
            "ask_user": missing,
            "suggestions": _suggest(rec),
            "auto_filled": auto_filled,
            "note": "위 필드를 채워 같은 record 에 합쳐 다시 호출하세요 (dry_run 권장).",
        }

    async with SessionLocal() as session:
        # 1) 인증 (REST 와 동일 로직 — 단일 진실원천)
        try:
            principal = await resolve_principal(session, api_key)
        except AuthenticationError as exc:
            return {"status": "error", "error": str(exc),
                    "code": "auth_failed", "recoverable": True,
                    "suggestion": "유효한 X-API-Key 를 전달하세요."}
        actor = f"mcp:{principal.name}" if not principal.is_anonymous else "mcp:anonymous"

        # 2) import (auto_seq 항상 on — desktop 흐름은 id 를 사용자가 모름)
        from ..routes.records import _import_one  # lazy: 순환 import 회피

        outcome = await _import_one(
            session, raw=rec, auto_seq=True, dry_run=dry_run,
            actor=actor, request_id=None,
        )

        if outcome.get("error"):
            env = _classify_error(outcome)
            return {"status": "error", "error": outcome["error"],
                    "auto_filled": auto_filled, **env}

        if dry_run:
            return {
                "status": "ready",
                "id": outcome.get("id"),
                "would": outcome.get("would"),
                "warnings": outcome.get("warnings", []),
                "auto_filled": auto_filled,
                "note": "저장하려면 같은 record 로 dry_run=false 재호출.",
            }

        # 실제 저장 — commit
        await session.commit()
        return {
            "status": "saved",
            "id": outcome.get("id"),
            "action": outcome.get("action"),
            "warnings": outcome.get("warnings", []),
            "auto_filled": auto_filled,
        }


__all__ = ["run_import"]
