# 전문가 저장(REST · MCP)이 저장 뒤 500 을 내지 않는지, 표본 색인 상태가 참인지(AD-21)
"""agent 행을 커밋한 뒤 같은 세션에서 표본 임베딩 동기화가 돈다. 그 표본 행 INSERT 가
flush 에서 실패하면 SQLAlchemy 는 세션을 '롤백 대기' 로 두고 붙어 있던 행을 모두 만료시킨다
— 방금 커밋한 agent 까지. 응답(AgentOut.model_validate)이 그 행을 읽다가 칸마다
'Error extracting attribute: PendingRollbackError' 로 ValidationError 가 나 500 이 됐다.
저장은 이미 끝난 뒤라 포털 운영자 동기화(HWAXPortal infra/scripts/sync-he-personas.py)가
첫 건에서 멈췄다(10-28 cae00 실측 — 응답 본문에는 details.type 'ValidationError' 만 있고
까닭 줄은 서버 로그에 있다).
실패는 표본 행 INSERT 를 막는 매퍼 이벤트로 흉내 낸다. 실 PG 의 거절(차원 · 길이 등)과
세션에 남는 꼴이 같다.

적대 검토(10-30)가 더 찾은 것 — MCP create_agent · patch_agent 는 입력 모델을 거치지 않아 응답
모델이 못 읽는 값을 저장했고(그 agent 의 REST 수정은 저장한 뒤 500), 저장 응답의 표본 색인
상태는 기본값(0 · 거짓)이라 색인 실패를 가렸다. 표본 글만 바꾸고 수가 같으면 GET 도 낡음을
못 봤고, resync-samples-all 은 첫 실패에서 500 이었다.

SQLite 시험은 aiosqlite 가 있어야 돈다(본 venv 에는 없다 — docs/embedder-bge-m3.md '시험').
맨 아래 둘은 DB 없이 돌아 본 venv 에서도 고침을 지킨다.
"""
from __future__ import annotations

import pytest

try:
    import aiosqlite  # noqa: F401
    aiosqlite_available = True
except ImportError:  # pragma: no cover
    aiosqlite_available = False

pytestmark = [pytest.mark.asyncio]
_sqlite = pytest.mark.skipif(not aiosqlite_available, reason="aiosqlite 미설치")

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


def _index_state(out: dict) -> tuple:
    return out["samples_indexed_count"], out["samples_stale"]


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


@pytest.fixture
def mcp_db(test_session_maker, monkeypatch):
    """MCP 쓰기 도구(mcp_write_svc)가 시험 SQLite 세션을 쓰게 한다."""
    from api.services import mcp_write_svc

    monkeypatch.setattr(mcp_write_svc, "SessionLocal", test_session_maker)
    return mcp_write_svc


@_sqlite
async def test_operator_payload_post_then_patch_round_trips(db_client) -> None:
    body = _operator()
    resp = await db_client.post("/api/agents", json=body)
    assert resp.status_code == 201, resp.text
    _assert_stored(resp.json(), body)
    assert _index_state(resp.json()) == (len(body["sample_queries"]), False)
    got = (await db_client.get(f"/api/agents/{_KEY}")).json()
    _assert_stored(got, body)
    assert _index_state(got) == (len(body["sample_queries"]), False)

    # 동기화는 바뀐 칸만 PATCH 한다(MANAGED 중 셋).
    patch = {
        "system_prompt": body["system_prompt"] + "\n- 결과 수치는 도구가 준 값만 쓴다.",
        "sample_queries": [*body["sample_queries"], "폴더블 스택 층별 응력 봐줘"],
        "response_config": {**body["response_config"], "key_tools": ["analyze_laminate"]},
    }
    resp = await db_client.patch(f"/api/agents/{_KEY}", json=patch)
    assert resp.status_code == 200, resp.text
    _assert_stored(resp.json(), {**body, **patch})
    # 바뀐 표본은 다시 색인된다 — 응답과 GET 이 같은 상태를 말한다.
    assert _index_state(resp.json()) == (len(patch["sample_queries"]), False)
    got = (await db_client.get(f"/api/agents/{_KEY}")).json()
    _assert_stored(got, {**body, **patch})
    assert _index_state(got) == (len(patch["sample_queries"]), False)


@_sqlite
async def test_save_answers_after_sample_sync_fails(db_client, reject_sample_rows) -> None:
    """표본 행이 거듭 거절돼도 저장한 값으로 2xx 를 낸다.

    색인 실패는 저장 응답과 GET 의 samples_stale 로 드러난다.
    """
    body = _operator()
    resp = await db_client.post("/api/agents", json=body)
    assert resp.status_code == 201, resp.text
    _assert_stored(resp.json(), body)
    assert _index_state(resp.json()) == (0, True)

    patch = {"sample_queries": ["다른 질문"], "system_prompt": "# 고친 프롬프트"}
    resp = await db_client.patch(f"/api/agents/{_KEY}", json=patch)
    assert resp.status_code == 200, resp.text
    _assert_stored(resp.json(), {**body, **patch})
    assert _index_state(resp.json()) == (0, True)

    got = (await db_client.get(f"/api/agents/{_KEY}")).json()
    _assert_stored(got, {**body, **patch})
    assert _index_state(got) == (0, True)


@_sqlite
async def test_failed_reindex_with_same_count_is_stale(db_client, reject_sample_rows) -> None:
    """표본 글만 바꾸고 수는 그대로인 저장의 재색인이 실패하면 롤백이 옛 행을 남긴다.

    수는 맞지만 옛 글이 색인돼 있으므로 낡음이다 — 수로 견주던 판정은 거짓이라 했다.
    """
    reject_sample_rows["times"] = 0  # 처음 색인은 붙고, 아래부터 늘 거절
    body = _operator()
    assert (await db_client.post("/api/agents", json=body)).status_code == 201
    reject_sample_rows["times"] = None
    new = [f"{q} (고침)" for q in body["sample_queries"]]
    resp = await db_client.patch(f"/api/agents/{_KEY}", json={"sample_queries": new})
    assert resp.status_code == 200, resp.text
    assert _index_state(resp.json()) == (len(new), True)
    got = (await db_client.get(f"/api/agents/{_KEY}")).json()
    assert got["sample_queries"] == new
    assert _index_state(got) == (len(new), True)
    listed = {a["agent_type"]: a for a in (await db_client.get("/api/agents")).json()}
    assert _index_state(listed[_KEY]) == (len(new), True)


@_sqlite
async def test_save_answers_when_retry_recovers(db_client, reject_sample_rows) -> None:
    """한 번 거절된 뒤 다시 시도가 붙어도 응답이 저장한 값을 읽는다.

    롤백이 만료시킨 agent 는 다시 시도가 붙었을 때도 다시 읽어야 한다.
    """
    reject_sample_rows["times"] = 1
    body = _operator()
    resp = await db_client.post("/api/agents", json=body)
    assert resp.status_code == 201, resp.text
    _assert_stored(resp.json(), body)
    assert _index_state(resp.json()) == (len(body["sample_queries"]), False)

    got = (await db_client.get(f"/api/agents/{_KEY}")).json()
    assert _index_state(got) == (len(body["sample_queries"]), False)


@_sqlite
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

    # DB 칸 길이를 넘는 값 — PG 에서는 저장이 DataError 로 500 이었다(SQLite 는 길이를 안 본다).
    resp = await db_client.post("/api/agents", json={**_operator(), "agent_type": "he-" + "x" * 48})
    assert resp.status_code == 422, resp.text  # 51자 — agents.agent_type 은 VARCHAR(50)
    errors = resp.json()["error"]["details"]["errors"]
    assert any(e["loc"][:2] == ["body", "agent_type"] for e in errors)

    body = _operator()
    assert (await db_client.post("/api/agents", json=body)).status_code == 201
    resp = await db_client.patch(f"/api/agents/{_KEY}", json={"response_config": "mcp_operator"})
    assert resp.status_code == 422, resp.text
    errors = resp.json()["error"]["details"]["errors"]
    assert any(e["loc"][:2] == ["body", "response_config"] for e in errors)
    resp = await db_client.patch(f"/api/agents/{_KEY}", json={"required_doc_type": "d" * 41})
    assert resp.status_code == 422, resp.text  # required_doc_type 은 VARCHAR(40)
    errors = resp.json()["error"]["details"]["errors"]
    assert any(e["loc"][:2] == ["body", "required_doc_type"] for e in errors)
    _assert_stored((await db_client.get(f"/api/agents/{_KEY}")).json(), body)


@_sqlite
async def test_mcp_write_checks_values_like_rest(db_client, mcp_db) -> None:
    """MCP create_agent · patch_agent 도 REST 와 같은 입력 모델로 저장 전에 거른다.

    거르지 않던 때는 None 이 든 태그 목록 · 빈 글 retrieval_config 가 저장돼, 그 agent 의
    REST 조회는 500 이고 REST 수정은 저장한 뒤 500 이었다(목록 GET /api/agents 도 500).
    """
    bad = await mcp_db.create_agent(agent={**_operator(), "common_tags": ["HE팀", None]})
    assert (bad["code"], bad["fields"]) == ("invalid_field", ["common_tags"]), bad
    assert (await db_client.get(f"/api/agents/{_KEY}")).status_code == 404

    # name 생략은 MCP 만 받는다 — agent_type 으로 채운다(종전과 같다).
    body = {k: v for k, v in _operator().items() if k != "name"}
    made = await mcp_db.create_agent(agent=body)
    assert made == {"status": "created", "agent_type": _KEY,
                    "samples_indexed": len(body["sample_queries"]), "samples_stale": False}, made

    bad = await mcp_db.patch_agent(agent_type=_KEY, patch={"retrieval_config": ""})
    assert (bad["code"], bad["fields"]) == ("invalid_field", ["retrieval_config"]), bad
    resp = await db_client.patch(f"/api/agents/{_KEY}", json={"description": "고친 설명"})
    assert resp.status_code == 200, resp.text
    _assert_stored(resp.json(), {**body, "name": _KEY, "description": "고친 설명",
                                 "retrieval_config": {}})
    assert (await db_client.get("/api/agents")).status_code == 200


@_sqlite
async def test_mcp_write_reports_real_index_state(mcp_db, reject_sample_rows) -> None:
    """표본 색인이 실패하면 MCP 결과도 그렇게 말한다.

    예전에는 samples_indexed 가 len(sample_queries) 였다.
    """
    made = await mcp_db.create_agent(agent=_operator())
    assert (made["status"], made["samples_indexed"], made["samples_stale"]) == ("created", 0, True)
    patched = await mcp_db.patch_agent(agent_type=_KEY, patch={"sample_queries": ["다른 질문"]})
    assert (patched["status"], patched["samples_indexed"], patched["samples_stale"]) == (
        "patched", 0, True)


@_sqlite
async def test_resync_all_collects_failures_and_goes_on(db_client, reject_sample_rows) -> None:
    """한 agent 의 표본 행이 거절돼도 나머지를 하고 errors 에 남긴다.

    예전에는 첫 실패 뒤 만료된 행을 읽다가 PendingRollbackError 로 500 이었다.
    """
    reject_sample_rows["times"] = 0
    for key in ("he-a-first", "he-b-second"):
        resp = await db_client.post("/api/agents", json={**_operator(), "agent_type": key})
        assert resp.status_code == 201, resp.text
    reject_sample_rows["times"] = 1  # 첫 agent 의 첫 행만 거절
    resp = await db_client.post("/api/agents/resync-samples-all")
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["agents_total"] == 2
    assert [e["agent_type"] for e in out["errors"]] == ["he-a-first"]
    assert [s["agent_type"] for s in out["successes"]] == ["he-b-second"]


# ---- DB 없이 도는 것(본 venv 에서도 공식 시험에서 실행된다) ----
async def test_mcp_rejects_before_opening_a_session(monkeypatch) -> None:
    """MCP 쪽 거르기는 세션을 열기 전에 끝난다 — 틀린 값은 DB 에 닿지 않는다."""
    from api.services import mcp_write_svc

    def _no_db():
        raise AssertionError("거르지 않고 세션을 열었다")

    monkeypatch.setattr(mcp_write_svc, "SessionLocal", _no_db)
    bad = await mcp_write_svc.create_agent(
        agent={**_operator(), "retrieval_config": [["top_k", 5]]})
    assert (bad["code"], bad["fields"]) == ("invalid_field", ["retrieval_config"]), bad
    bad = await mcp_write_svc.patch_agent(
        agent_type=_KEY, patch={"common_tags": ["HE팀", None], "data_types": "DOC"})
    assert (bad["code"], bad["fields"]) == ("invalid_field", ["common_tags", "data_types"]), bad
    assert "common_tags.1" in bad["error"]


async def test_sync_failure_rolls_back_and_rereads_the_agent(monkeypatch) -> None:
    """표본 동기화가 실패할 때마다 세션을 롤백하고 agent 를 다시 읽는다(고침의 핵심).

    롤백이 없으면 다시 시도도 PendingRollbackError 이고, 다시 읽기가 없으면 응답이 만료된 행을
    읽는다. 위 SQLite 시험이 실제 세션으로 같은 것을 본다.
    """
    from types import SimpleNamespace

    from api.services import agent_svc, sample_embedding_svc

    calls: list = []

    class _Session:
        async def rollback(self) -> None:
            calls.append("rollback")

        async def refresh(self, obj) -> None:
            calls.append(("refresh", obj.agent_type))

    async def _rejected(_session, *, agent_type, sample_queries):
        calls.append(("sync", agent_type))
        raise RuntimeError("expected 1024 dimensions, not 768 (시험이 낸 거절)")

    monkeypatch.setattr(sample_embedding_svc, "sync_agent_samples", _rejected)
    await agent_svc._sync_samples_safely(
        _Session(), agent=SimpleNamespace(agent_type=_KEY), samples=["질문"])
    assert calls == [("sync", _KEY), "rollback", ("refresh", _KEY)] * 2
