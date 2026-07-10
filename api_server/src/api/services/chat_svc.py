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
def _llm_config() -> dict[str, str] | None:
    """env 에서 vLLM 접속 설정. base_url+model 이 없으면 None (미설정)."""
    base = (os.environ.get("OPENAI_BASE_URL") or "").strip().rstrip("/")
    model = (os.environ.get("CHAT_MODEL") or os.environ.get("OPENAI_ASK_MODEL") or "").strip()
    if not base or not model:
        return None
    return {
        "base": base,
        "model": model,
        "key": (os.environ.get("OPENAI_API_KEY") or "EMPTY").strip(),
    }


async def _vllm_chat(cfg: dict[str, str], messages: list[dict[str, Any]]) -> dict[str, Any]:
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
    async with httpx.AsyncClient(timeout=_VLLM_TIMEOUT) as client:
        resp = await client.post(f"{cfg['base']}/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]


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
            "message": "LLM(vLLM)이 연결되지 않았습니다. .env 의 OPENAI_BASE_URL 과 "
                       "CHAT_MODEL(또는 OPENAI_ASK_MODEL)을 설정하세요. 데이터 등록은 대시보드 "
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
        except Exception as exc:  # 연결/HTTP 오류
            yield _ev("error", {"code": "vllm_down", "message": f"LLM 호출 실패: {exc}"})
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
                "tool_call_id": tc.get("id", ""),
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


__all__ = ["TOOL_SPECS", "TOOL_EXECUTORS", "stream_chat"]
