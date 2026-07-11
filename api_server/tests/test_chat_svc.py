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


# ── LLM 연결 설정 (기본 상암 · 런타임 override) ──────────────────
_LLM_ENV = ("LLM_BACKEND", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY",
            "OPENAI_BASE_URL", "CHAT_MODEL", "OPENAI_ASK_MODEL")


def _isolate_cfg(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_svc, "_runtime_config_path", lambda: tmp_path / "cfg.json")
    for k in _LLM_ENV:
        monkeypatch.delenv(k, raising=False)


def test_config_default_is_sangam(monkeypatch, tmp_path):
    _isolate_cfg(monkeypatch, tmp_path)
    cfg = chat_svc.get_effective_config()
    assert cfg["base_url"] == "http://10.198.143.137:10000/v1"
    assert cfg["model"] == "GLM-5-2"
    assert cfg["connected"] is True and cfg["source"] == "default(상암)"


def test_config_override_and_clear(monkeypatch, tmp_path):
    _isolate_cfg(monkeypatch, tmp_path)
    chat_svc.set_runtime_config(base_url="http://192.168.1.100:8000/v1", model="qwen2.5-7b-dev")
    c = chat_svc._llm_config()
    assert c["base"] == "http://192.168.1.100:8000/v1" and c["model"] == "qwen2.5-7b-dev"
    # backend off → 미연결(degrade)
    chat_svc.set_runtime_config(backend="off")
    assert chat_svc._llm_config() is None
    # 초기화 → 상암 기본 복귀
    chat_svc.clear_runtime_config()
    assert chat_svc._llm_config()["base"] == "http://10.198.143.137:10000/v1"


def test_config_env_over_default(monkeypatch, tmp_path):
    _isolate_cfg(monkeypatch, tmp_path)
    monkeypatch.setenv("LLM_BASE_URL", "http://env-host:9000/v1")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    c = chat_svc._llm_config()
    assert c["base"] == "http://env-host:9000/v1" and c["model"] == "env-model"


def test_config_never_exposes_key(monkeypatch, tmp_path):
    _isolate_cfg(monkeypatch, tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "supersecret")
    cfg = chat_svc.get_effective_config()
    assert "supersecret" not in json.dumps(cfg, ensure_ascii=False)
    assert cfg["has_key"] is True


@pytest.mark.asyncio
async def test_test_connection_off(monkeypatch, tmp_path):
    _isolate_cfg(monkeypatch, tmp_path)
    chat_svc.set_runtime_config(backend="off")
    r = await chat_svc.test_connection()  # 네트워크 안 탐 (backend off)
    assert r["ok"] is False


def test_bool_env_empty_is_default(monkeypatch):
    # 빈 .env 대입(set -a 로 ""으로 export)도 default — 상암 폐쇄망 직결 유지 (NO_PROXY 회귀).
    monkeypatch.setenv("LLM_NO_PROXY", "")
    assert chat_svc._bool_env("LLM_NO_PROXY", True) is True
    monkeypatch.setenv("LLM_NO_PROXY", "false")
    assert chat_svc._bool_env("LLM_NO_PROXY", True) is False
    monkeypatch.delenv("LLM_NO_PROXY", raising=False)
    assert chat_svc._bool_env("LLM_NO_PROXY", True) is True


def test_no_proxy_default_reaches_sangam(monkeypatch, tmp_path):
    # 빈 LLM_NO_PROXY 에서도 no_proxy=True (trust_env off) → 사내 프록시 우회, 상암 직결.
    _isolate_cfg(monkeypatch, tmp_path)
    monkeypatch.setenv("LLM_NO_PROXY", "")  # .env 빈 대입 재현
    assert chat_svc._llm_config()["no_proxy"] is True


def test_float_env_defensive(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_S", "abc")
    assert chat_svc._float_env("LLM_TIMEOUT_S", 120.0) == 120.0  # 이상값 → default
    monkeypatch.setenv("LLM_TIMEOUT_S", "0")
    assert chat_svc._float_env("LLM_TIMEOUT_S", 120.0) == 120.0  # 0 → default
    monkeypatch.setenv("LLM_TIMEOUT_S", "30")
    assert chat_svc._float_env("LLM_TIMEOUT_S", 120.0) == 30.0


def test_base_url_validation_blocks_ssrf(monkeypatch, tmp_path):
    _isolate_cfg(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        chat_svc.set_runtime_config(base_url="http://169.254.169.254/latest/meta-data")  # 메타데이터 차단
    with pytest.raises(ValueError):
        chat_svc.set_runtime_config(base_url="ftp://x/v1")  # 스킴
    # 정상 vLLM 주소는 통과
    chat_svc.set_runtime_config(base_url="http://192.168.1.100:8000/v1", model="m")
    assert chat_svc._llm_config()["base"] == "http://192.168.1.100:8000/v1"


def test_config_override_beats_env(monkeypatch, tmp_path):
    # override 와 env 가 동시에 있을 때 override 우선 (우선순위 회귀).
    _isolate_cfg(monkeypatch, tmp_path)
    monkeypatch.setenv("LLM_BASE_URL", "http://env-host:9000/v1")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    chat_svc.set_runtime_config(base_url="http://ov-host:1/v1", model="ov-model")
    c = chat_svc._llm_config()
    assert c["base"] == "http://ov-host:1/v1" and c["model"] == "ov-model"


@pytest.mark.asyncio
async def test_max_rounds_exhaustion(monkeypatch):
    # LLM 이 매 턴 tool_call 만 반환 → 무한루프 방지(_MAX_ROUNDS) 안전장치 검증.
    monkeypatch.setattr(chat_svc, "_llm_config", lambda: {"base": "x", "model": "m", "key": "k"})

    async def always_tool(cfg, messages):
        return {"content": "", "tool_calls": [
            {"id": "c", "function": {"name": "list_doc_types", "arguments": "{}"}}]}

    async def fake_exec(args, api_key):
        return {"ok": 1}

    monkeypatch.setattr(chat_svc, "_vllm_chat", always_tool)
    monkeypatch.setitem(chat_svc.TOOL_EXECUTORS, "list_doc_types", fake_exec)

    evs = await _collect(chat_svc.stream_chat([{"role": "user", "content": "loop"}]))
    result = next(e for e in evs if e["event"] == "result")
    assert "상한" in result["data"]["content"]  # 상한 도달 메시지
    assert len(result["data"]["tool_trace"]) == chat_svc._MAX_ROUNDS
    assert evs[-1]["event"] == "done"


@pytest.mark.asyncio
async def test_config_endpoints(test_client, monkeypatch, tmp_path):
    _isolate_cfg(monkeypatch, tmp_path)
    # 쓰기 엔드포인트는 require_api_key — 테스트는 인증 의존성을 override 로 우회.
    from api.auth.dependencies import Principal, require_api_key
    from api.main import app

    app.dependency_overrides[require_api_key] = lambda: Principal(
        name="test", agent_scopes=["*"], is_anonymous=False
    )
    try:
        r = await test_client.get("/api/chat/config")  # GET 은 무인증
        assert r.status_code == 200 and r.json()["connected"] is True
        r = await test_client.put("/api/chat/config", json={"base_url": "http://x:1/v1", "model": "m"})
        assert r.status_code == 200 and r.json()["base_url"] == "http://x:1/v1"
        # SSRF 차단 → 400
        r = await test_client.put("/api/chat/config", json={"base_url": "http://169.254.169.254/v1"})
        assert r.status_code == 400
        r = await test_client.delete("/api/chat/config")
        assert "10.198.143.137" in r.json()["base_url"]
    finally:
        app.dependency_overrides.pop(require_api_key, None)


@pytest.mark.asyncio
async def test_config_put_requires_auth(test_client, monkeypatch, tmp_path):
    # override 없이(익명) 쓰기 → 401 (SSRF/키유출 방지 가드 확인).
    _isolate_cfg(monkeypatch, tmp_path)
    r = await test_client.put("/api/chat/config", json={"base_url": "http://x/v1"})
    assert r.status_code == 401


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
