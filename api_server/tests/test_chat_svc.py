# chat_svc(자체 챗 오케스트레이션) 단위 테스트.
"""tool-calling 루프·echo·graceful degrade·스펙 검증 — vLLM/PG 불필요.

핵심은 tool-calling 루프(위험지점)를 mock vLLM 으로 end-to-end 검증하는 것.
"""
from __future__ import annotations

import json

import pytest

from api.services import chat_svc


async def _collect(gen):
    return [ev async for ev in gen]


# ── TOOL_SPECS 유효성 ────────────────────────────────────────────
def test_tool_specs_shape_and_match():
    names = []
    for spec in chat_svc.TOOL_SPECS:
        assert spec["type"] == "function"
        fn = spec["function"]
        assert fn["name"] and fn["parameters"]["type"] == "object"
        names.append(fn["name"])
    # 스펙에 있는 도구는 전부 실행기가 있어야 한다 (LLM 이 부르면 실행돼야 함).
    assert set(names) == set(chat_svc.TOOL_EXECUTORS)
    assert len(names) == 6


# ── echo 모드 (원격 없이 SSE 경로 검증) ──────────────────────────
@pytest.mark.asyncio
async def test_echo_mode():
    evs = await _collect(
        chat_svc.stream_chat([{"role": "user", "content": "안녕"}], mode="echo")
    )
    kinds = [e["event"] for e in evs]
    assert kinds == ["status", "result", "done"]
    assert evs[1]["data"]["content"] == "echo: 안녕"


# ── graceful degrade (LLM 미설정) ────────────────────────────────
@pytest.mark.asyncio
async def test_unconfigured_degrades(monkeypatch):
    monkeypatch.setattr(chat_svc, "_llm_config", lambda: None)
    evs = await _collect(chat_svc.stream_chat([{"role": "user", "content": "hi"}]))
    assert evs[0]["event"] == "error"
    assert evs[0]["data"]["code"] == "llm_unconfigured"
    assert evs[-1]["event"] == "done"


@pytest.mark.asyncio
async def test_empty_messages():
    evs = await _collect(chat_svc.stream_chat([]))
    assert evs[0]["event"] == "error" and evs[0]["data"]["code"] == "bad_input"


# ── tool-calling 루프 (mock vLLM) — 오케스트레이션 핵심 ──────────
@pytest.mark.asyncio
async def test_tool_calling_loop(monkeypatch):
    monkeypatch.setattr(chat_svc, "_llm_config", lambda: {"base": "x", "model": "m", "key": "k"})

    calls = {"n": 0, "seen_tool_role": False}

    async def fake_vllm(cfg, messages):
        calls["n"] += 1
        if calls["n"] == 1:
            # 1턴: 도구 호출 지시
            return {
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "list_doc_types", "arguments": "{}"}}
                ],
            }
        # 2턴: 도구 결과를 받은 뒤 최종 답. 이전 대화에 tool 역할이 들어왔는지 확인.
        calls["seen_tool_role"] = any(m.get("role") == "tool" for m in messages)
        return {"content": "문서종류는 3개입니다.", "tool_calls": None}

    async def fake_exec(args, api_key):
        return {"doc_types": ["test_plan", "report", "manual"]}

    monkeypatch.setattr(chat_svc, "_vllm_chat", fake_vllm)
    monkeypatch.setitem(chat_svc.TOOL_EXECUTORS, "list_doc_types", fake_exec)

    evs = await _collect(
        chat_svc.stream_chat([{"role": "user", "content": "문서종류 뭐 있어?"}])
    )
    kinds = [e["event"] for e in evs]
    # status(도구) → result → done
    assert "status" in kinds and kinds[-1] == "done"
    status = next(e for e in evs if e["event"] == "status")
    assert status["data"]["tool"] == "list_doc_types"

    result = next(e for e in evs if e["event"] == "result")
    assert result["data"]["content"] == "문서종류는 3개입니다."
    trace = result["data"]["tool_trace"]
    assert len(trace) == 1 and trace[0]["tool"] == "list_doc_types"
    assert trace[0]["result"] == {"doc_types": ["test_plan", "report", "manual"]}
    # 루프가 도구 결과를 2턴 입력으로 되돌려줬는지 (loop 정확성)
    assert calls["n"] == 2 and calls["seen_tool_role"] is True


@pytest.mark.asyncio
async def test_tool_failure_is_returned_to_llm(monkeypatch):
    """도구가 예외를 던져도 스트림이 죽지 않고 error 를 LLM 에 되돌린다."""
    monkeypatch.setattr(chat_svc, "_llm_config", lambda: {"base": "x", "model": "m", "key": "k"})

    async def fake_vllm(cfg, messages):
        # 항상 도구를 부르지만, 2턴째엔 도구 결과(에러 포함)를 받고 종료.
        if not any(m.get("role") == "tool" for m in messages):
            return {"content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "semantic_search", "arguments": "{\"q\":\"x\"}"}}]}
        return {"content": "검색에 실패했습니다.", "tool_calls": None}

    async def boom(args, api_key):
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_svc, "_vllm_chat", fake_vllm)
    monkeypatch.setitem(chat_svc.TOOL_EXECUTORS, "semantic_search", boom)

    evs = await _collect(chat_svc.stream_chat([{"role": "user", "content": "x 찾아줘"}]))
    result = next(e for e in evs if e["event"] == "result")
    trace = result["data"]["tool_trace"][0]
    assert "failed" in json.dumps(trace["result"], ensure_ascii=False)  # 에러가 trace 에 담김
    assert evs[-1]["event"] == "done"


# ── POST /api/chat 라우트 (in-process ASGI, echo 모드 = PG/vLLM 불필요) ──
@pytest.mark.asyncio
async def test_chat_route_sse_echo(test_client):
    resp = await test_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "라우트확인"}], "mode": "echo"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "event: status" in body
    assert "event: result" in body and "echo: 라우트확인" in body
    assert "event: done" in body
