# 자체 챗 오케스트레이션 — vLLM(OpenAI 호환) + 로컬 도구 tool-calling 루프.
"""메인 페이지 챗: 대화로 데이터를 수집·검색한다.

설계 (docs/chat/CONTEXT-NOTES.md):
    - 자체 완결: AIDataHub 프로세스가 vLLM 을 직접 호출하고, 자기 6개 도구를
      로컬로 실행한다(외부 Agent Server 불필요).
    - 단일 진실원: 도구 실행기는 MCP 도구와 같은 서비스 함수를 호출한다.
    - vLLM = httpx OpenAI 호환 ``/chat/completions`` (openai SDK 미설치 대비).
    - 스트리밍 = status+result SSE (토큰 스트리밍 아님, v1). non-stream vLLM 호출.
    - graceful degrade: LLM 미설정 → 안내. ``mode="echo"`` = 원격 없이 SSE 검증.
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

# 도구 루프 상한 — 무한 tool-call 방지.
_MAX_ROUNDS = 8
_VLLM_TIMEOUT = 120.0

_SYSTEM_PROMPT = (
    "당신은 Mobile eXperience AI Data Hub 의 데이터 어시스턴트다. 사용자가 표/문서를 "
    "붙여넣거나 질문하면, 아래 도구로 데이터를 카탈로그에 넣거나 찾는다.\n"
    "데이터 주입(수집) 규약 — 반드시 지켜라:\n"
    "1) import_record 전에 find_similar_data(headers/caption)로 비슷한 기존 데이터를 먼저 확인한다.\n"
    "2) 응답의 suggested(doc_type/tags/graph_type)는 검토해서 채운다 (비슷한 건 비슷하게 분류).\n"
    "3) team/group 은 절대 자동으로 정하지 마라. needs_human 후보를 사용자에게 보여주고 "
    "'이 데이터는 어느 팀/그룹인가요?'라고 물어 사람이 정하게 하라.\n"
    "4) 유사 데이터가 없으면(confidence=low/none) 추측하지 말고 describe_record_schema 로 "
    "가능한 값을 보여주며 직접 물어라.\n"
    "5) import_record 는 먼저 dry_run=true 로 검증하고, 사용자가 확인하면 dry_run=false 로 저장한다.\n"
    "검색 답변은 반드시 record id 를 출처로 인용하라 (예: DATA-HE-CAE-2026-0000000001).\n"
    "간결한 한국어로 답한다."
)


# ---------------------------------------------------------------------------
# 도구 스펙 (OpenAI function 스키마) — 챗이 부를 수 있는 6개
# ---------------------------------------------------------------------------
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "find_similar_data",
            "description": (
                "새 표/문서가 기존 어떤 데이터와 같은 종류인지 임베딩 유사도로 찾아 "
                "doc_type/tags/graph_type 을 제안하고 team/group 후보를 준다. import_record 전에 호출."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "headers": {"type": "array", "items": {"type": "string"}, "description": "표 컬럼명 목록"},
                    "caption": {"type": "string", "description": "표/그림 캡션"},
                    "title": {"type": "string", "description": "제목"},
                    "data_type": {"type": "string", "description": "DOC/DATA/SIM/CAD/LOG/FORM", "default": "DATA"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_record_schema",
            "description": "레코드에 채울 수 있는 필드와 가능한 값(data_type/doc_type/team/group 등) 가이드를 반환.",
            "parameters": {
                "type": "object",
                "properties": {"agent_type": {"type": "string", "description": "특정 에이전트 맥락(선택)"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_doc_types",
            "description": "등록된 세부 문서종류(doc_type) 목록을 반환. 분류할 때 참고.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "import_record",
            "description": (
                "표/문서를 우리 규격 레코드로 저장. record 는 최소 data_type/team/group/title/content 필요. "
                "dry_run=true 로 먼저 검증(부족 필드·제안 반환), 사용자 확인 후 dry_run=false 로 저장."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "record": {"type": "object", "description": "레코드 dict (data_type,team,group,title,content 등)"},
                    "dry_run": {"type": "boolean", "description": "true=검증만, false=실제 저장", "default": True},
                },
                "required": ["record"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_records",
            "description": "정형 필터(team/group/doc_type/data_type/tags/q)로 레코드 목록을 조회.",
            "parameters": {
                "type": "object",
                "properties": {
                    "team": {"type": "string"}, "group": {"type": "string"},
                    "doc_type": {"type": "string"}, "data_type": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "q": {"type": "string", "description": "제목/요약 키워드"},
                    "limit": {"type": "integer", "default": 20},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search",
            "description": "자연어 뜻으로 레코드 섹션을 의미 검색(pgvector). 출처 인용에 사용.",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "검색어(자연어)"},
                    "top_k": {"type": "integer", "default": 8},
                    "data_types": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["q"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# 도구 실행기 — MCP 래퍼(mcp_runtime.py)와 같은 서비스 함수 호출 (단일 진실원)
# ---------------------------------------------------------------------------
async def _exec_find_similar_data(args: dict[str, Any], api_key: str | None) -> Any:
    from ..db.base import SessionLocal
    from . import similarity_svc

    async with SessionLocal() as session:
        return await similarity_svc.suggest_by_similarity(
            session,
            title=args.get("title", "") or "",
            caption=args.get("caption", "") or "",
            headers=args.get("headers"),
            data_type=args.get("data_type", "DATA") or "DATA",
            top_k=int(args.get("top_k", 5) or 5),
        )


async def _exec_describe_record_schema(args: dict[str, Any], api_key: str | None) -> Any:
    from ..db.base import SessionLocal
    from . import ingest_guide_svc

    async with SessionLocal() as session:
        return await ingest_guide_svc.build_guide(session, agent_type=args.get("agent_type") or None)


async def _exec_list_doc_types(args: dict[str, Any], api_key: str | None) -> Any:
    from . import mcp_write_svc

    return await mcp_write_svc.list_doc_types(api_key=api_key)


async def _exec_import_record(args: dict[str, Any], api_key: str | None) -> Any:
    from . import mcp_write_svc

    record = args.get("record")
    if not isinstance(record, dict):
        return {"status": "error", "error": "record must be an object", "code": "bad_input",
                "recoverable": False, "suggestion": "표/문서를 JSON 객체로 전달하세요."}
    return await mcp_write_svc.run_import(
        record=record, dry_run=bool(args.get("dry_run", True)), api_key=api_key,
    )


async def _exec_list_records(args: dict[str, Any], api_key: str | None) -> Any:
    from ..db.base import SessionLocal
    from . import record_query_svc

    async with SessionLocal() as session:
        rows, total = await record_query_svc.query_records(
            session,
            team=args.get("team") or None, group=args.get("group") or None,
            doc_type=args.get("doc_type") or None, data_type=args.get("data_type") or None,
            tags=args.get("tags") or None, q=args.get("q") or None,
            limit=max(1, min(int(args.get("limit", 20) or 20), 100)),
        )
        return {"total": total, "records": [record_query_svc.to_summary(r) for r in rows]}


async def _exec_semantic_search(args: dict[str, Any], api_key: str | None) -> Any:
    from ..db.base import SessionLocal
    from . import search_svc

    async with SessionLocal() as session:
        return await search_svc.semantic_search(
            session, args.get("q", "") or "",
            top_k=int(args.get("top_k", 8) or 8),
            data_types=args.get("data_types") or None,
        )


TOOL_EXECUTORS = {
    "find_similar_data": _exec_find_similar_data,
    "describe_record_schema": _exec_describe_record_schema,
    "list_doc_types": _exec_list_doc_types,
    "import_record": _exec_import_record,
    "list_records": _exec_list_records,
    "semantic_search": _exec_semantic_search,
}

# 사람이 읽는 도구 진행 라벨 (status 이벤트용)
_TOOL_LABELS = {
    "find_similar_data": "비슷한 기존 데이터 확인 중",
    "describe_record_schema": "입력 스키마 안내 준비 중",
    "list_doc_types": "문서종류 목록 조회 중",
    "import_record": "데이터 저장 처리 중",
    "list_records": "레코드 목록 조회 중",
    "semantic_search": "의미 검색 중",
}


# ---------------------------------------------------------------------------
# vLLM (OpenAI 호환) 클라이언트 — httpx 직접
# ---------------------------------------------------------------------------
# 기본 = 상암 B300 프로덕션 LLM (OpenAI 호환). .env / 설정 UI 로 override.
# ReportArchive 규칙과 동일: LLM_BACKEND=openai + LLM_BASE_URL(/v1 포함) + LLM_MODEL.
_SANGAM_BASE = "http://10.198.143.137:10000/v1"
_SANGAM_MODEL = "GLM-5-2"


def _runtime_config_path():
    """설정 UI 가 저장하는 런타임 override 파일 경로 (data dir 내 JSON)."""
    from pathlib import Path

    from ..config import settings

    return Path(settings.attachments_dir).parent / "chat_llm_config.json"


def _read_runtime_override() -> dict[str, Any]:
    """설정 UI override 읽기 (없으면 {}). base_url/model/backend 만."""
    try:
        p = _runtime_config_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:  # 손상/권한 → env 기본으로 폴백
        pass
    return {}


def _bool_env(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    # 빈 문자열(빈 .env 대입은 set -a 로 ""으로 export)도 default 로 —
    # 다른 LLM_* 의 ``X or default`` 체인과 의미를 통일. (없으면 폐쇄망 직결 유지)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _float_env(name: str, default: float) -> float:
    """방어적 float 파싱 — 이상값이 스트림을 죽이지 않게 default 로 폴백."""
    v = os.environ.get(name)
    if v is None or not v.strip():
        return default
    try:
        f = float(v)
        return f if f > 0 else default
    except (ValueError, TypeError):
        return default


def _validate_base_url(url: str) -> str:
    """설정 UI 로 들어온 base_url 검증 — SSRF/자격증명 유출 방지.

    http(s) 만 허용하고, 클라우드 메타데이터·링크로컬 등 위험 호스트를 차단한다.
    잘못되면 ValueError (라우트가 400 으로 변환).
    """
    from urllib.parse import urlparse

    u = (url or "").strip()
    if not u:
        return u
    p = urlparse(u)
    if p.scheme not in ("http", "https"):
        raise ValueError("base_url 은 http/https 만 허용합니다.")
    host = (p.hostname or "").lower()
    if not host:
        raise ValueError("base_url 에 host 가 없습니다.")
    # 클라우드 메타데이터 엔드포인트 명시 차단 (자격증명 탈취 벡터).
    if host in ("169.254.169.254", "metadata.google.internal", "metadata"):
        raise ValueError("차단된 host 입니다 (metadata 엔드포인트).")
    return u.rstrip("/")


def _llm_config() -> dict[str, Any] | None:
    """유효 LLM 접속 설정. 우선순위: 런타임 override(UI) → env → 상암 기본.

    backend 가 off/mock/none 이면 None(미연결) 을 돌려 graceful degrade.
    """
    ov = _read_runtime_override()
    backend = (ov.get("backend") or os.environ.get("LLM_BACKEND") or "openai").strip().lower()
    if backend in ("off", "mock", "none", "disabled", ""):
        return None
    base = (
        ov.get("base_url")
        or os.environ.get("LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")  # 하위호환
        or _SANGAM_BASE
    ).strip().rstrip("/")
    model = (
        ov.get("model")
        or os.environ.get("LLM_MODEL")
        or os.environ.get("CHAT_MODEL")  # 하위호환
        or os.environ.get("OPENAI_ASK_MODEL")
        or _SANGAM_MODEL
    ).strip()
    if not base or not model:
        return None
    return {
        "backend": backend,
        "base": base,
        "model": model,
        "key": (os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "EMPTY").strip(),
        "timeout": _float_env("LLM_TIMEOUT_S", _VLLM_TIMEOUT),
        # 폐쇄망(상암) 직결 — httpx 가 HTTP_PROXY env 를 우회. ReportArchive LLM_NO_PROXY 규칙.
        "no_proxy": _bool_env("LLM_NO_PROXY", True),
    }


def _http_client(cfg: dict[str, Any]) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=cfg.get("timeout", _VLLM_TIMEOUT), trust_env=not cfg.get("no_proxy", True))


async def _vllm_chat(cfg: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    """OpenAI 호환 chat.completions (non-stream, tools). choices[0].message 반환."""
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "tools": TOOL_SPECS,
        "tool_choice": "auto",
        "temperature": 0.2,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"}
    async with _http_client(cfg) as client:
        resp = await client.post(f"{cfg['base']}/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]


# ---------------------------------------------------------------------------
# 설정 UI 지원 — 유효 설정 조회 / override 저장 / 연결 테스트
# ---------------------------------------------------------------------------
def get_effective_config() -> dict[str, Any]:
    """현재 유효 설정 (api_key 값은 절대 노출 안 함). 설정 UI GET 용."""
    ov = _read_runtime_override()
    cfg = _llm_config()
    if cfg is None:
        backend = (ov.get("backend") or os.environ.get("LLM_BACKEND") or "off").strip().lower()
        return {"backend": backend, "base_url": "", "model": "", "connected": False,
                "source": "runtime" if ov else "env", "has_key": bool(os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"))}
    # source 는 base_url 이 실제로 어디서 왔는지로 판단 (override 에 backend 만 있어도 runtime 오표기 방지).
    if ov.get("base_url"):
        source = "runtime"
    elif os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL"):
        source = "env"
    else:
        source = "default(상암)"
    return {
        "backend": cfg["backend"], "base_url": cfg["base"], "model": cfg["model"],
        "connected": True, "no_proxy": cfg["no_proxy"],
        "source": source,
        "has_key": cfg["key"] != "EMPTY",
    }


def set_runtime_config(*, backend: str | None = None, base_url: str | None = None,
                       model: str | None = None) -> dict[str, Any]:
    """설정 UI PUT — override JSON 저장 후 유효 설정 반환. base_url 은 검증(SSRF 방지)."""
    data = _read_runtime_override()
    if backend is not None:
        data["backend"] = backend.strip().lower()
    if base_url is not None:
        data["base_url"] = _validate_base_url(base_url)  # 잘못되면 ValueError → 라우트 400
    if model is not None:
        data["model"] = model.strip()
    p = _runtime_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return get_effective_config()


def clear_runtime_config() -> dict[str, Any]:
    """override 삭제 → env/상암 기본으로 복귀."""
    try:
        _runtime_config_path().unlink(missing_ok=True)
    except Exception:
        pass
    return get_effective_config()


async def test_connection() -> dict[str, Any]:
    """사용자 트리거 연결 테스트 — GET {base}/models. (§8: 자동 프로빙 아님, UI 버튼)."""
    cfg = _llm_config()
    if cfg is None:
        return {"ok": False, "detail": "backend 가 off/mock 이거나 base/model 미설정."}
    try:
        async with _http_client(cfg) as client:
            resp = await client.get(f"{cfg['base']}/models",
                                    headers={"Authorization": f"Bearer {cfg['key']}"}, timeout=10.0)
        if resp.status_code == 200:
            ids = [m.get("id") for m in (resp.json().get("data") or [])]
            served = cfg["model"] in ids if ids else None
            return {"ok": True, "detail": f"{cfg['base']} 응답 200", "models": ids[:20], "model_served": served}
        return {"ok": False, "detail": f"HTTP {resp.status_code} @ {cfg['base']}/models"}
    except Exception as exc:
        return {"ok": False, "detail": f"연결 실패: {exc}"}


# ---------------------------------------------------------------------------
# 챗 스트림 — SSE 이벤트 async generator
# ---------------------------------------------------------------------------
def _ev(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"event": event, "data": data}


async def stream_chat(
    messages: list[dict[str, Any]], *, api_key: str | None = None, mode: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """대화 → SSE 이벤트 스트림.

    yield: {"event": status|result|error|done, "data": {...}} (route 가 SSE 프레임으로 직렬화).
    """
    if not messages or not isinstance(messages, list):
        yield _ev("error", {"code": "bad_input", "message": "messages 가 비었습니다."})
        yield _ev("done", {})
        return

    # dev echo — 원격 vLLM 없이 SSE 경로 자체 검증 (mock 경계 = 이 한 곳).
    if mode == "echo":
        last = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        yield _ev("status", {"step": "echo 모드", "tool": None})
        yield _ev("result", {"role": "assistant", "content": f"echo: {last}", "tool_trace": []})
        yield _ev("done", {})
        return

    cfg = _llm_config()
    if cfg is None:
        yield _ev("error", {
            "code": "llm_unconfigured",
            "message": "LLM 이 연결되지 않았습니다 (backend=off). '데이터 챗 > LLM 연결 설정' 또는 "
                       ".env 의 LLM_BASE_URL / LLM_MODEL 을 설정하세요. 데이터 등록은 대시보드 "
                       "'MCP 도구'/'검색' 탭 또는 REST /api/records/import 로도 가능합니다.",
        })
        yield _ev("done", {})
        return

    convo: list[dict[str, Any]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    convo.extend(messages)
    tool_trace: list[dict[str, Any]] = []

    for _round in range(_MAX_ROUNDS):
        try:
            msg = await _vllm_chat(cfg, convo)
        except httpx.TimeoutException:
            yield _ev("error", {"code": "timeout", "message": "LLM 응답 시간 초과."})
            yield _ev("done", {})
            return
        except httpx.HTTPStatusError as exc:  # 4xx/5xx — 상태코드를 그대로 알려 오진단 방지
            sc = exc.response.status_code
            hint = " (LLM_API_KEY 확인)" if sc in (401, 403) else ""
            yield _ev("error", {"code": "vllm_http_error",
                                 "message": f"LLM HTTP {sc}{hint} @ {cfg['base']}"})
            yield _ev("done", {})
            return
        except (KeyError, IndexError, ValueError) as exc:  # 응답 스키마 이상(choices 등)
            yield _ev("error", {"code": "vllm_bad_response",
                                 "message": f"LLM 응답 형식 오류: {exc}"})
            yield _ev("done", {})
            return
        except Exception as exc:  # 연결/전송 오류
            yield _ev("error", {"code": "vllm_down", "message": f"LLM 연결 실패: {exc}"})
            yield _ev("done", {})
            return

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            yield _ev("result", {
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_trace": tool_trace,
            })
            yield _ev("done", {})
            return

        # id 없는 tool_call 은 합성 — assistant echo 와 tool 메시지의 tool_call_id 매칭 보장
        # (일부 OpenAI 호환 서버가 id 를 생략하면 엄격한 서버가 후속 턴을 거부).
        for _i, tc in enumerate(tool_calls):
            if not tc.get("id"):
                tc["id"] = f"call_{_round}_{_i}"
        # assistant 의 tool_calls 를 대화에 반영 후 각 도구 실행.
        convo.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
        for tc in tool_calls:
            fn = (tc.get("function") or {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {}
            yield _ev("status", {"step": _TOOL_LABELS.get(name, name), "tool": name})

            executor = TOOL_EXECUTORS.get(name)
            if executor is None:
                out: Any = {"error": f"unknown tool: {name}"}
            else:
                try:
                    out = await executor(args, api_key)
                except Exception as exc:  # 도구 실패는 LLM 에 되돌려 스스로 복구 유도
                    out = {"error": f"tool '{name}' failed: {exc}"}

            tool_trace.append({"tool": name, "args": args, "result": out})
            convo.append({
                "role": "tool",
                "tool_call_id": tc["id"],  # 위에서 항상 채워짐 (합성 포함)
                "name": name,
                "content": json.dumps(out, ensure_ascii=False, default=str),
            })

    # 라운드 상한 도달 — 여기까지의 추적을 돌려주고 정리.
    yield _ev("result", {
        "role": "assistant",
        "content": "도구 호출이 상한에 도달했습니다. 요청을 더 구체적으로 나눠 다시 시도해 주세요.",
        "tool_trace": tool_trace,
    })
    yield _ev("done", {})


__all__ = [
    "TOOL_SPECS", "TOOL_EXECUTORS", "stream_chat",
    "get_effective_config", "set_runtime_config", "clear_runtime_config", "test_connection",
]
