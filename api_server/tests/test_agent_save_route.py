# POST · PATCH /api/agents 가 저장한 뒤 500 을 내지 않는지 — 운영자 동기화가 멈추던 것(AD-21)
"""agent 행을 커밋한 뒤 같은 세션에서 표본 임베딩 동기화가 돈다. 그 표본 행 INSERT 가
flush 에서 실패하면 SQLAlchemy 는 세션을 '롤백 대기' 로 두고 붙어 있던 행을 모두 만료시킨다
— 방금 커밋한 agent 까지. 응답(AgentOut.model_validate)이 그 행을 읽다가 칸마다
'Error extracting attribute: PendingRollbackError' 로 ValidationError 가 나 500 이 됐다.
저장은 이미 끝난 뒤라 포털 운영자 동기화(HWAXPortal infra/scripts/sync-he-personas.py)가
첫 건에서 멈췄다(10-28 cae00 실측 — 응답 본문에는 details.type 'ValidationError' 만 있고
까닭 줄은 서버 로그에 있다).
실패는 표본 행 INSERT 를 막는 매퍼 이벤트로 흉내 낸다. 실 PG 의 거절(차원 · 길이 등)과
세션에 남는 꼴이 같다.
"""
from __future__ import annotations

import pytest

try:
    import aiosqlite  # noqa: F401
    aiosqlite_available = True
except ImportError:  # pragma: no cover
    aiosqlite_available = False

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not aiosqlite_available, reason="aiosqlite 미설치"),
]

_KEY = "he-calc-laminate"


def _operator() -> dict:
    """sync-he-personas.py 의 plan() 이 내는 페이로드 꼴(칸 · 형 그대로, 프롬프트만 줄였다)."""
    return {
        "agent_type": _KEY,
        "name": "Laminate Analyzer 운영자",
        "description": "적층 복합재·PCB 스택업의 ABD·열휨을 계산 도구로 푸는 HE팀 운영자.",
        "common_tags": ["HE팀", "MCP 운영자", "해석 계산·물성", "Laminate Analyzer"],
        "sample_queries": [
            "[0/90/0] CFRP 적층 중립면이랑 ABD 계산해줘",
            "동박률 70% 4층 PCB 리플로우 휨 예측해줘",
            "이 적층안 좌굴 여유 1.5 되려면 두께 몇 배 필요해?",
        ],
        "system_prompt": "\n".join([
            "<!-- no-tool-guide -->",
            "# Laminate Analyzer 운영자",
            "HE팀 MCP 운영자 — Laminate Analyzer 도구 9개를 직접 호출해 일한다.",
            "",
            "## 틀리기 쉬운 것",
            "- 두께 단위는 mm 다.",
            "",
            "## 상태를 바꾸는 도구",
            "- 사용자가 그 작업을 요청했을 때만 부른다 — save_case.",
        ]),
        "data_types": [],
        "response_config": {
            "persona_kind": "mcp_operator",
            "mcp_apps": ["heax-laminate_analyzer_mcp"],
            "key_tools": ["analyze_laminate", "check_design_rules", "compute_thermal_response"],
            "managed_by": "HWAXPortal/infra/personas/he-team.json",
        },
    }


def _assert_stored(out: dict, want: dict) -> None:
    assert {k: out.get(k) for k in want} == want


@pytest.fixture
def reject_sample_rows():
    """표본 임베딩 행 INSERT 를 flush 에서 거절한다. 돌려준 dict 의 times 만큼(None — 늘)."""
    from sqlalchemy import event

    from api.db.models import AgentSampleEmbedding

    state: dict = {"times": None}

    def _reject(_mapper, _conn, _target):
        if state["times"] is None or state["times"] > 0:
            if state["times"] is not None:
                state["times"] -= 1
            raise RuntimeError("expected 1024 dimensions, not 768 (시험이 낸 거절)")

    event.listen(AgentSampleEmbedding, "before_insert", _reject)
    try:
        yield state
    finally:
        event.remove(AgentSampleEmbedding, "before_insert", _reject)


async def test_operator_payload_post_then_patch_round_trips(db_client) -> None:
    body = _operator()
    resp = await db_client.post("/api/agents", json=body)
    assert resp.status_code == 201, resp.text
    _assert_stored(resp.json(), body)
    got = (await db_client.get(f"/api/agents/{_KEY}")).json()
    _assert_stored(got, body)
    assert got["samples_indexed_count"] == len(body["sample_queries"])

    # 동기화는 바뀐 칸만 PATCH 한다(MANAGED 중 셋).
    patch = {
        "system_prompt": body["system_prompt"] + "\n- 결과 수치는 도구가 준 값만 쓴다.",
        "sample_queries": [*body["sample_queries"], "폴더블 스택 층별 응력 봐줘"],
        "response_config": {**body["response_config"], "key_tools": ["analyze_laminate"]},
    }
    resp = await db_client.patch(f"/api/agents/{_KEY}", json=patch)
    assert resp.status_code == 200, resp.text
    _assert_stored(resp.json(), {**body, **patch})
    _assert_stored((await db_client.get(f"/api/agents/{_KEY}")).json(), {**body, **patch})


async def test_save_answers_after_sample_sync_fails(db_client, reject_sample_rows) -> None:
    """표본 행이 거듭 거절돼도 저장한 값으로 2xx 를 낸다 — 색인 실패는 samples_stale 로 드러난다."""
    body = _operator()
    resp = await db_client.post("/api/agents", json=body)
    assert resp.status_code == 201, resp.text
    _assert_stored(resp.json(), body)

    patch = {"sample_queries": ["다른 질문"], "system_prompt": "# 고친 프롬프트"}
    resp = await db_client.patch(f"/api/agents/{_KEY}", json=patch)
    assert resp.status_code == 200, resp.text
    _assert_stored(resp.json(), {**body, **patch})

    got = (await db_client.get(f"/api/agents/{_KEY}")).json()
    _assert_stored(got, {**body, **patch})
    assert got["samples_indexed_count"] == 0
    assert got["samples_stale"] is True


async def test_save_answers_when_retry_recovers(db_client, reject_sample_rows) -> None:
    """한 번 거절된 뒤 다시 시도가 붙어도 응답이 저장한 값을 읽는다.

    롤백이 만료시킨 agent 는 다시 시도가 붙었을 때도 다시 읽어야 한다.
    """
    reject_sample_rows["times"] = 1
    body = _operator()
    resp = await db_client.post("/api/agents", json=body)
    assert resp.status_code == 201, resp.text
    _assert_stored(resp.json(), body)

    got = (await db_client.get(f"/api/agents/{_KEY}")).json()
    assert got["samples_indexed_count"] == len(body["sample_queries"])
    assert got["samples_stale"] is False


async def test_invalid_value_rejected_before_saving(db_client) -> None:
    """응답 모델이 못 받는 값은 입력 모델(AgentIn · AgentPatch)이 저장 전에 422 로 거절한다.

    오류는 그 칸을 밝힌다.
    """
    bad = {**_operator(), "sample_queries": [{"q": "질문", "expect": "답"}]}
    resp = await db_client.post("/api/agents", json=bad)
    assert resp.status_code == 422, resp.text
    errors = resp.json()["error"]["details"]["errors"]
    assert any(e["loc"][:2] == ["body", "sample_queries"] for e in errors)
    assert (await db_client.get(f"/api/agents/{_KEY}")).status_code == 404

    body = _operator()
    assert (await db_client.post("/api/agents", json=body)).status_code == 201
    resp = await db_client.patch(f"/api/agents/{_KEY}", json={"response_config": "mcp_operator"})
    assert resp.status_code == 422, resp.text
    errors = resp.json()["error"]["details"]["errors"]
    assert any(e["loc"][:2] == ["body", "response_config"] for e in errors)
    _assert_stored((await db_client.get(f"/api/agents/{_KEY}")).json(), body)
