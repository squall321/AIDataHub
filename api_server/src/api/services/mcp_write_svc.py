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
        suggestions = _suggest(rec)
        # team/group 이 부족하면 유사도 기반 제안을 추가 (룰 없는 자동분류).
        # 확정 X — Claude 가 confidence 보고 사용자에게 확인 (B3).
        if ("team" in missing or "group" in missing) and not rec.get("id"):
            content = rec.get("content") or {}
            if isinstance(content, dict):
                try:
                    async with SessionLocal() as _s:
                        from . import similarity_svc
                        sim = await similarity_svc.suggest_by_similarity(
                            _s,
                            title=str(rec.get("title") or ""),
                            caption=str(content.get("caption") or ""),
                            headers=content.get("headers") if isinstance(content.get("headers"), list) else None,
                            data_type=str(rec.get("data_type") or "DATA"),
                        )
                    if sim.get("suggested"):
                        suggestions["by_similarity"] = sim
                except Exception:  # noqa: BLE001 — 제안은 best-effort
                    pass
        return {
            "status": "incomplete",
            "ask_user": missing,
            "suggestions": suggestions,
            "auto_filled": auto_filled,
            "note": "위 필드를 채워 같은 record 에 합쳐 다시 호출하세요 (dry_run 권장). "
                    "by_similarity 제안이 있으면 confidence 를 보고 사용자에게 확인 후 채우세요.",
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


# ===========================================================================
# Phase 2 — Agent / DocType 정의를 Claude 대화로 (VSCode extension 이관)
#
# 모든 write 로직은 이미 서비스로 분리돼 있다 (agent_svc / agent_draft_svc /
# doc_type_svc — 전부 session + dict 받는 plain 함수). 여기선 인증 + 에러봉투만
# 입혀 MCP 에 노출한다. REST 핸들러와 같은 서비스를 호출 → 단일 진실원천.
# ===========================================================================

# agent 정의 폼 — Claude 가 "무엇을 채울지" 알도록 introspection 으로 노출.
_AGENT_FIELDS = {
    "agent_type": "필수. 고유 식별자 (kebab-case, 예: battery-test-analyst)",
    "name": "표시 이름",
    "description": "이 챗봇 페르소나가 무엇을 다루는지 1~2문장",
    "data_types": "다루는 data_type 목록 (DOC/DATA/SIM/CAD/LOG/FORM/OTHER 중)",
    "required_doc_type": "한정할 doc_type code (선택)",
    "common_tags": "이 영역 대표 태그",
    "required_tags": "검색 시 반드시 가져야 할 태그 (선택)",
    "excluded_tags": "제외할 태그 (선택)",
    "system_prompt": "필수. 답변 톤/인용 규약/거부 규칙. 한국어 권장",
    "sample_queries": "이 페르소나가 받을 법한 질문 예시 3~5개 (의미검색 매칭용)",
    "retrieval_config": "검색 설정 {top_k:int, score_threshold:float}",
}


async def _authed(api_key: str | None, session) -> tuple[Any, dict | None]:
    """인증 → (principal, error_envelope). error 면 두번째가 dict."""
    try:
        principal = await resolve_principal(session, api_key)
    except AuthenticationError as exc:
        return None, {"status": "error", "error": str(exc),
                      "code": "auth_failed", "recoverable": True,
                      "suggestion": "유효한 X-API-Key 를 전달하세요."}
    return principal, None


async def describe_agent_schema() -> dict[str, Any]:
    """agent 정의에 필요한 필드/설명 — Claude 가 create_agent 전에 무엇을
    채울지 알도록. (VSCode 의 agent 폼을 introspection 으로 대체)"""
    return {"fields": _AGENT_FIELDS,
            "note": "create_agent 로 저장. sample_queries 가 있으면 자동으로 임베딩 동기화됨."}


async def draft_agent(
    *, hint: str | None = None, record_ids: list[str] | None = None,
    filter_tags: list[str] | None = None, filter_data_types: list[str] | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """기존 레코드 신호 + 자연어 의도 → agent 정의 초안 (저장 X)."""
    async with SessionLocal() as session:
        _, err = await _authed(api_key, session)
        if err:
            return err
        from . import agent_draft_svc
        draft = await agent_draft_svc.generate_draft(
            session, record_ids=record_ids, filter_tags=filter_tags,
            filter_data_types=filter_data_types, hint=hint,
        )
        return {"status": "draft", "draft": draft,
                "note": "검토 후 create_agent 로 저장하세요. system_prompt/sample_queries 를 다듬으면 검색 품질↑."}


async def create_agent(*, agent: dict[str, Any], api_key: str | None = None) -> dict[str, Any]:
    """agent(검색 챗봇 페르소나) 신규 등록."""
    if not isinstance(agent, dict) or not agent.get("agent_type"):
        return {"status": "error", "error": "agent.agent_type required",
                "code": "missing_field", "recoverable": True,
                "suggestion": "agent_type(고유 식별자)을 포함하세요. describe_agent_schema 참고."}
    async with SessionLocal() as session:
        principal, err = await _authed(api_key, session)
        if err:
            return err
        from . import agent_svc
        actor = f"mcp:{principal.name}" if not principal.is_anonymous else "mcp:anonymous"
        try:
            row = await agent_svc.create_agent(session, agent, changed_by=actor)
        except ValueError as exc:  # 이미 존재
            return {"status": "error", "error": str(exc), "code": "duplicate",
                    "recoverable": True, "suggestion": "patch_agent 로 기존 agent 를 수정하세요."}
        return {"status": "created", "agent_type": row.agent_type,
                "samples_indexed": len(row.sample_queries or [])}


async def patch_agent(*, agent_type: str, patch: dict[str, Any], api_key: str | None = None) -> dict[str, Any]:
    """기존 agent 부분 수정 (준 필드만)."""
    if not agent_type:
        return {"status": "error", "error": "agent_type required",
                "code": "missing_field", "recoverable": True, "suggestion": "수정할 agent_type 을 지정하세요."}
    async with SessionLocal() as session:
        principal, err = await _authed(api_key, session)
        if err:
            return err
        from . import agent_svc
        actor = f"mcp:{principal.name}" if not principal.is_anonymous else "mcp:anonymous"
        row = await agent_svc.update_agent(session, agent_type, patch or {}, changed_by=actor)
        if row is None:
            return {"status": "error", "error": f"agent not found: {agent_type}",
                    "code": "not_found", "recoverable": True,
                    "suggestion": "create_agent 로 새로 만들거나 list_agents 로 확인하세요."}
        return {"status": "patched", "agent_type": row.agent_type}


async def bind_records_to_agent(*, agent_type: str, api_key: str | None = None) -> dict[str, Any]:
    """agent 의 기대 스키마(required_doc_type/tags/data_types)에 맞는 기존
    레코드를 그 agent 에 자동 연결. create_agent 후 데이터를 붙이는 마지막 고리."""
    if not agent_type:
        return {"status": "error", "error": "agent_type required",
                "code": "missing_field", "recoverable": True,
                "suggestion": "연결할 agent_type 을 지정하세요."}
    async with SessionLocal() as session:
        _, err = await _authed(api_key, session)
        if err:
            return err
        from . import agent_draft_svc, agent_svc
        agent = await agent_svc.get_agent(session, agent_type)
        if agent is None:
            return {"status": "error", "error": f"agent not found: {agent_type}",
                    "code": "not_found", "recoverable": True,
                    "suggestion": "먼저 create_agent 로 만들거나 list_agents 로 확인하세요."}
        result = await agent_draft_svc.bind_matching_records(
            session,
            agent_type=agent_type,
            required_doc_type=agent.required_doc_type,
            required_tags=list(agent.required_tags or []),
            common_tags=list(agent.common_tags or []),
            data_types=list(agent.data_types or []),
        )
        return {"status": "bound", **result}


async def list_doc_types(*, api_key: str | None = None) -> dict[str, Any]:
    """등록된 doc_type(의미 분류) 목록 — create_agent 의 required_doc_type 선택용."""
    async with SessionLocal() as session:
        from . import doc_type_svc
        rows = await doc_type_svc.list_doc_types(session)
        return {"doc_types": [
            {"code": d.code, "name": d.name, "description": d.description or "",
             "expected_sections": list(getattr(d, "expected_sections", None) or [])}
            for d in rows
        ]}


async def create_doc_type(*, doc_type: dict[str, Any], api_key: str | None = None) -> dict[str, Any]:
    """새 doc_type(의미 분류) 등록."""
    if not isinstance(doc_type, dict) or not doc_type.get("code"):
        return {"status": "error", "error": "doc_type.code required",
                "code": "missing_field", "recoverable": True, "suggestion": "code(식별자)와 name 을 포함하세요."}
    async with SessionLocal() as session:
        _, err = await _authed(api_key, session)
        if err:
            return err
        from . import doc_type_svc
        try:
            dt = await doc_type_svc.create_doc_type(session, doc_type)
        except Exception as exc:  # noqa: BLE001 — 중복 등
            return {"status": "error", "error": str(exc)[:160], "code": "create_failed",
                    "recoverable": True, "suggestion": "이미 있는 code 인지 list_doc_types 로 확인하세요."}
        return {"status": "created", "code": dt.code}


# ===========================================================================
# 파일 변환 브리지 (Path B) — 서버측 정밀 변환 (docx/xlsx/pdf/pptx → JSON)
#
# MCP 는 바이너리를 못 받는다. 그래서 두 경로:
#   Path A: Claude 가 첨부를 파싱 → import_record(dict)  [작은/구조화 데이터]
#   Path B: 사용자가 inbox 폴더에 파일을 두고 convert_file(name) 호출 →
#           서버가 기존 변환기(convert_file_dispatch)로 정밀 변환 → record 초안
#           반환 → import_record 로 흐름  [수식/병합셀/대용량 등 정밀 필요]
#
# 보안: 임의 서버 경로 읽기 = 위험. inbox 디렉터리 하위로만 제한 (traversal 차단).
# ===========================================================================
import os as _os
from pathlib import Path as _Path


def _convert_inbox() -> _Path:
    """변환 허용 디렉터리. ``AIDH_CONVERT_INBOX`` 미설정 시 ~/aidh-inbox.
    사용자가 여기에 파일을 두고 convert_file 로 변환한다."""
    raw = _os.environ.get("AIDH_CONVERT_INBOX") or str(_Path.home() / "aidh-inbox")
    p = _Path(raw).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


def _resolve_in_inbox(name: str) -> _Path | None:
    """``name`` 을 inbox 하위 실경로로 해석. inbox 밖이면 None (traversal 차단)."""
    inbox = _convert_inbox()
    cand = (inbox / name).resolve()
    try:
        cand.relative_to(inbox)  # inbox 밖이면 ValueError
    except ValueError:
        return None
    return cand if cand.is_file() else None


async def run_convert(
    *,
    file: str,
    team: str = "",
    group: str = "",
    year: int = 0,
    api_key: str | None = None,
    auto_save: bool = False,
) -> dict[str, Any]:
    """inbox 의 파일을 우리 JSON 규격으로 정밀 변환 (Path B).

    반환:
        - ``converted``: record 초안 (Claude 가 검토 후 import_record 로 저장)
        - ``auto_save=true`` 면 변환 직후 바로 import_record 까지 (dry_run 권장)
        - ``error``: inbox 밖 / 미지원 포맷 / 변환 실패
    """
    from datetime import datetime, timezone

    name = (file or "").strip()
    if not name:
        return {"status": "error", "error": "file name required",
                "code": "missing_field", "recoverable": True,
                "suggestion": f"먼저 파일을 inbox 에 두세요: {_convert_inbox()}"}

    path = _resolve_in_inbox(name)
    if path is None:
        return {"status": "error", "error": f"file not in inbox: {name}",
                "code": "not_in_inbox", "recoverable": True,
                "suggestion": (f"보안상 inbox 하위 파일만 변환합니다. {_convert_inbox()} 에 "
                               "파일을 두고 파일명만 전달하세요 (경로/.. 불가).")}

    # 인증
    async with SessionLocal() as session:
        principal, err = await _authed(api_key, session)
        if err:
            return err

    # 포맷 감지 + 변환 (동기 — 스레드 오프로딩)
    import asyncio

    from .converter_dispatch import (
        ConvertRequest,
        UnsupportedFormatError,
        convert_file,
        detect_format,
    )

    try:
        fmt = detect_format(path.name)
    except UnsupportedFormatError as exc:
        return {"status": "error", "error": str(exc), "code": "unsupported_format",
                "recoverable": True, "suggestion": "docx/xlsx/pdf/pptx/md 만 지원합니다."}

    req = ConvertRequest(
        team=(team or "TBD").upper(),
        group=(group or "TBD").upper(),
        year=int(year) if year else datetime.now(timezone.utc).year,
    )
    try:
        payload = await asyncio.to_thread(convert_file, path, fmt, req)
    except Exception as exc:  # noqa: BLE001 — 변환 실패 전반
        return {"status": "error", "error": f"conversion failed: {exc}"[:200],
                "code": "convert_failed", "recoverable": False,
                "suggestion": "파일이 손상됐거나 변환기가 처리 못하는 구조일 수 있습니다."}

    if auto_save:
        # 변환 결과를 곧바로 import (dry_run — 부족 필드는 import 가 되묻는다)
        result = await run_import(record=payload, dry_run=True, api_key=api_key)
        return {"status": "converted", "format": fmt.value, "record": payload,
                "import_preview": result,
                "note": "team/group 이 TBD 면 import 가 되묻는다. 확정 후 import_record(dry_run=false)."}

    return {"status": "converted", "format": fmt.value, "record": payload,
            "note": "이 record 를 검토 후 import_record 로 저장하세요 (team/group 확인 필수)."}


__all__ = [
    "run_import",
    "run_convert",
    "describe_agent_schema",
    "draft_agent",
    "create_agent",
    "patch_agent",
    "bind_records_to_agent",
    "list_doc_types",
    "create_doc_type",
]
