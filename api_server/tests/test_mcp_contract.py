# MCP 도구 계약 시험 — 인자 설명 100% · 읽기/쓰기 표지 100% · 인자 계약 불변
"""HWAXPortal docs/mcp-modernize 공통 계약(C1 · C2 · C3 · C4 · C6 · C9)을
이 허브의 MCP 도구가 지키는지.

FastMCP 의 list_tools 결과(클라이언트가 받는 꼴 그대로 — 카멜 이름 · null 뺌)를
별 프로세스에서 뽑아 본다. 별 프로세스인 까닭 — mcp_runtime 을 import 하면 업로드 ·
연합 도구 등록이 DB 를 읽는다. 둘을 끄고 DB 주소를 닿지 않는 곳으로 두어 이 시험이
어떤 DB 에도 닿지 않게 한다(test_record_query_svc 의 설명문 시험과 같은 방식).
업로드 도구는 DB 행 대신 가짜 행으로 register_all_uploads 의 실제 길을 태운다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# 인자 계약 — 고치기 전 dev 게이트웨이 tools/list 에서 뽑은 (인자 이름 차례, required, 기본값).
# 이름 · 필수 · 기본값을 바꾸면 게이트웨이 · 에이전트 서버 · 심의 엔진의 호출이 깨진다
# — 바꾸려면 소비처부터.
_CONTRACT: dict[str, tuple[tuple, tuple, dict]] = {
    "discover": (("compact",), (), {"compact": False}),
    "list_agents": (
        ("compact", "domain", "tag"),
        (),
        {"compact": False, "domain": None, "tag": None},
    ),
    "list_agent_domains": ((), (), {}),
    "list_tags": (("q", "min_count", "limit"), (), {"q": "", "min_count": 1, "limit": 100}),
    "recommend_agents": (
        ("q", "top_k", "top_k_tools", "agent_type", "onto"),
        ("q",),
        {"top_k": 5, "top_k_tools": 3, "agent_type": None, "onto": "off"},
    ),
    "get_agent_session": (("agent_type",), ("agent_type",), {}),
    "agent_search": (
        ("agent_type", "q", "mode", "top_k", "as_of", "as_of_undated"),
        ("agent_type", "q"),
        {"mode": "semantic", "top_k": None, "as_of": "", "as_of_undated": "keep"},
    ),
    "semantic_search": (
        ("q", "top_k", "data_types", "agent_type"),
        ("q",),
        {"top_k": 10, "data_types": None, "agent_type": None},
    ),
    "hybrid_search": (
        ("q", "top_k", "data_types", "agent_type"),
        ("q",),
        {"top_k": 10, "data_types": None, "agent_type": None},
    ),
    "fts_search": (("q", "top_k", "agent_type"), ("q",), {"top_k": 10, "agent_type": None}),
    # 새 도구(HWAXPortal docs/request-1125) — 코어 개체 key 로 이어진 카드
    "cards_by_core": (
        ("type_slug", "key", "hub", "limit", "offset"),
        ("key", "type_slug"),
        {"hub": "", "limit": 20, "offset": 0},
    ),
    "tag_search": (("tags", "agent_type", "top_k"), ("tags",), {"agent_type": None, "top_k": 20}),
    "list_records": (
        (
            "team", "group", "doc_type", "data_type", "tags", "agents", "year", "q", "limit",
            "offset",
        ),
        (),
        {
            "team": "", "group": "", "doc_type": "", "data_type": "", "tags": None, "agents": None,
            "year": 0, "q": "", "limit": 20, "offset": 0,
        },
    ),
    "data_aggregate": (
        ("record_id", "op", "column", "group_by", "where"),
        ("op", "record_id"),
        {"column": "", "group_by": "", "where": ""},
    ),
    "get_record": (("record_id",), ("record_id",), {}),
    "get_record_sections": (
        ("record_id", "sections", "limit", "offset"),
        ("record_id",),
        {"sections": None, "limit": None, "offset": 0},
    ),
    "get_context_bundle": (
        ("agent_type", "format", "max_records", "max_sections_per_record"),
        ("agent_type",),
        {"format": "json", "max_records": 10, "max_sections_per_record": 8},
    ),
    "describe_data_capability": (("data_type", "graph_type"), ("data_type",), {"graph_type": ""}),
    "describe_record_schema": (("agent_type",), (), {"agent_type": ""}),
    "find_similar_data": (
        ("headers", "caption", "title", "data_type", "top_k"),
        (),
        {"headers": None, "caption": "", "title": "", "data_type": "DATA", "top_k": 5},
    ),
    "import_record": (
        ("record", "dry_run", "api_key", "agent_type"),
        ("record",),
        {"dry_run": True, "api_key": "", "agent_type": ""},
    ),
    "describe_agent_schema": ((), (), {}),
    "draft_agent": (
        ("hint", "record_ids", "filter_tags", "filter_data_types", "api_key"),
        (),
        {
            "hint": "", "record_ids": None, "filter_tags": None, "filter_data_types": None,
            "api_key": "",
        },
    ),
    "create_agent": (("agent", "api_key"), ("agent",), {"api_key": ""}),
    "patch_agent": (("agent_type", "patch", "api_key"), ("agent_type", "patch"), {"api_key": ""}),
    "bind_records_to_agent": (("agent_type", "api_key"), ("agent_type",), {"api_key": ""}),
    "list_doc_types": (("api_key",), (), {"api_key": ""}),
    "create_doc_type": (("doc_type", "api_key"), ("doc_type",), {"api_key": ""}),
    "convert_file": (
        ("file", "team", "group", "year", "auto_save", "api_key"),
        ("file",),
        {"team": "", "group": "", "year": 0, "auto_save": False, "api_key": ""},
    ),
    "echo_args": (("message", "repeat", "upper"), ("message",), {"repeat": 1, "upper": False}),
    "fetch_rerank_model": (
        ("model_id", "dest_root", "force"),
        (),
        {"model_id": "BAAI/bge-reranker-v2-m3", "dest_root": "/opt/models", "force": False},
    ),
}

# 쓰는 도구 — 코드로 확인한 것(mcp_write_svc · routes/records._import_one · agent_draft_svc ·
# 스크립트). 나머지는 읽기다. 값은 (readOnlyHint, destructiveHint, idempotentHint, openWorldHint).
_WRITES = {
    # 있는 id 면 그 레코드를 덮어쓴다 · id 가 없으면 새 번호
    "import_record": (False, True, False, False),
    "create_agent": (False, False, False, False),
    "patch_agent": (False, True, True, False),             # 준 필드를 덮어쓴다
    # agents 에 더하기만 · 이미 이어진 것은 건너뜀
    "bind_records_to_agent": (False, False, True, False),
    "create_doc_type": (False, False, False, False),
    "fetch_rerank_model": (False, False, True, True),      # 서버 디스크에 모델을 받는다
}
_OPEN_WORLD_READS = {"draft_agent"}   # 쓰지 않지만 설정된 바깥 LLM 을 부를 수 있다

# 품질 카드(HWAXMcpGateway mcp_quality_card.py)가 「설명에 이력이 섞였다」 로 세는 꼴과 같다.
_HISTORY_RE = re.compile(
    r"(20\d\d-\d\d-\d\d|\b\d{1,2}-\d{2} (?:결정|사용자)|\b[A-Z]{1,4}-D?\d{1,3}\b|실측)")

_PROBE = r"""
import asyncio, json, os
from pydantic import ValidationError
from api import mcp_runtime
from api.services import data_svc, mcp_upload_svc as u

mcp = mcp_runtime.mcp

# 업로드 도구 셋 — DB 행 대신 가짜 행으로 register_all_uploads 의 실제 길을 태운다.
# import 때의 DB 읽기를 막으려고 껐던 스위치를 이제 푼다(DB 를 읽는 _load_uploads_sync 는
# 아래에서 갈아 끼운다).
os.environ.pop("AIDH_MCP_UPLOADS", None)
def _row(name, persist, dedup=""):
    m = u.UploadManifest(
        name=name, description="probe upload tool", script="run.py",
        args=[u.UploadArg(name="e_modulus", type="number", required=True,
                          description="탄성계수 (GPa)."),
              u.UploadArg(name="out_path", type="string", default="/work/out.png")],
        persist_output=u.PersistOutput(enabled=persist, data_type="SIM", dedup_key=dedup))
    return {"name": name, "current_sha": "0" * 12, "current_version": 1,
            "manifest": u._manifest_to_dict(m)}

u._load_uploads_sync = lambda: [_row("probe_persist", True), _row("probe_pure", False),
                                _row("probe_dedup", True, "{{args.e_modulus}}")]
registered = u.register_all_uploads(mcp)

tools = [t.model_dump(by_alias=True, exclude_none=True) for t in asyncio.run(mcp.list_tools())]

tm = mcp._tool_manager
def _ok(tool, args):
    try:
        tm.get_tool(tool).fn_metadata.arg_model.model_validate(args)
        return True
    except ValidationError:
        return False

checks = {
    "registered": registered,
    "aggregate_ops": list(data_svc.AGGREGATE_OPS),
    # 글 'null' 이 None 으로 풀리면 기준일 거름이 조용히 꺼진다 — as_of 는 str 그대로여야 한다.
    "as_of_null_kept": tm.get_tool("agent_search").fn_metadata.pre_parse_json({"as_of": "null"}),
    # 소비처(리스크 앱)가 모르는 인자를 얹어 부른다 — 지금처럼 버려져야지 거절되면 안 된다.
    "extra_agent_search": _ok("agent_search", {
        "agent_type": "a", "q": "x", "mode": "hybrid",
        "required_tags": ["t"], "retrieval_config": {"top_k": 3}}),
    "extra_hybrid": _ok("hybrid_search", {
        "q": "x", "top_k": 5, "tags": ["t"], "exclude_tags": ["u"]}),
    "extra_tag_search": _ok("tag_search", {"tags": "a,b", "limit": 20}),
    # 소비처가 실제로 보내는 값은 받아야 한다(에이전트 서버 · 리스크 앱 호출부).
    "consumer_values": all([
        _ok("list_records", {"agents": ["k"], "limit": 100, "offset": 200}),
        _ok("list_records", {"agents": ["k"], "doc_type": "card", "limit": 20}),
        _ok("recommend_agents", {"q": "x", "top_k": 40, "onto": "shadow"}),
        _ok("recommend_agents", {"q": "x", "top_k": 10, "onto": "rank", "top_k_tools": 0}),
        _ok("list_agents", {"compact": True, "domain": "cam"}),
        _ok("agent_search", {"agent_type": "a", "q": "x", "mode": "fts"}),
        _ok("get_record_sections", {"record_id": "r"}),
        _ok("data_aggregate", {"record_id": "r", "op": "avg", "column": "stress"}),
    ]),
    # 닫은 어휘 · 경계 밖은 스키마가 거절한다.
    "rejects": {
        "list_records_limit_101": not _ok("list_records", {"limit": 101}),
        "list_records_limit_0": not _ok("list_records", {"limit": 0}),
        "top_k_tools_11": not _ok("recommend_agents", {"q": "x", "top_k_tools": 11}),
        "onto_bogus": not _ok("recommend_agents", {"q": "x", "onto": "bogus"}),
        "op_median": not _ok("data_aggregate", {"record_id": "r", "op": "median"}),
        "mode_bogus": not _ok("agent_search", {"agent_type": "a", "q": "x", "mode": "vector"}),
    },
}
print("@@PROBE@@" + json.dumps({"tools": tools, "checks": checks}, ensure_ascii=False))
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    src = str(Path(__file__).resolve().parents[1] / "src")
    env = {**os.environ, "PYTHONPATH": src, "AIDH_MCP_FEDERATION": "off", "AIDH_MCP_UPLOADS": "off",
           "DATABASE_URL": "postgresql+asyncpg://nobody:x@127.0.0.1:1/none"}
    env.pop("AIDH_MCP_SCRIPTS", None)        # 저장소의 스크립트 도구 둘도 함께 본다
    env.pop("AIDH_MCP_SCRIPTS_DIR", None)
    out = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, env=env,
                         cwd=tmp_path_factory.mktemp("mcp_contract"), timeout=180)
    assert out.returncode == 0, out.stderr[-3000:]
    payload = out.stdout.split("@@PROBE@@", 1)[1]
    data = json.loads(payload)
    data["by_name"] = {t["name"]: t for t in data["tools"]}
    return data


_UPLOADS = ("probe_persist", "probe_pure", "probe_dedup")


def test_tool_names_unchanged(probe):
    """C9 — 이름을 바꾸지 않는다(소비처가 이름으로 부른다). 저장소의 도구 31 + 가짜 업로드 셋."""
    assert set(probe["by_name"]) == set(_CONTRACT) | set(_UPLOADS)
    assert probe["checks"]["registered"] == list(_UPLOADS)


@pytest.mark.parametrize("name", sorted(_CONTRACT))
def test_arg_contract_unchanged(probe, name):
    """인자 이름 · 차례 · required · 기본값이 고치기 전과 같다."""
    props_want, req_want, dflt_want = _CONTRACT[name]
    schema = probe["by_name"][name]["inputSchema"]
    props = schema.get("properties", {})
    assert tuple(props) == props_want
    assert tuple(sorted(schema.get("required", []))) == req_want
    assert {k: v["default"] for k, v in props.items() if "default" in v} == dflt_want


def test_every_arg_described(probe):
    """C2 — 모든 인자에 스키마 설명(산문 「Args:」 가 아니라 inputSchema 에).

    업로드 도구는 매니페스트 설명을 싣는다.
    """
    missing = [(t["name"], k) for t in probe["tools"]
               for k, v in (t["inputSchema"].get("properties") or {}).items()
               if not str(v.get("description") or "").strip()]
    assert missing == []
    up = probe["by_name"]["probe_persist"]["inputSchema"]["properties"]
    assert up["e_modulus"]["description"] == "탄성계수 (GPa)."
    assert "설명이 없다" in up["out_path"]["description"]       # 비면 없다고 적는다(짓지 않는다)


def test_every_tool_has_hints(probe):
    """C6 — 모든 도구에 네 표지가 참 · 거짓으로. 직렬화한 꼴에 카멜 이름으로 나가야 한다
    (SDK 1.x 는 read_only_hint= 같은 스네이크 이름을 모르는 칸으로 붙여 표지가 비어 나간다)."""
    for t in probe["tools"]:
        ann = t.get("annotations") or {}
        for k in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
            assert isinstance(ann.get(k), bool), (t["name"], k, ann)
        assert not any("_" in k for k in ann), (t["name"], ann)


@pytest.mark.parametrize("name", sorted(_CONTRACT))
def test_hint_values(probe, name):
    """읽기 · 쓰기 판정 — 쓰는 도구만 readOnlyHint 거짓.

    읽기 도구는 destructive 거짓 · idempotent 참.
    """
    ann = probe["by_name"][name]["annotations"]
    got = (ann["readOnlyHint"], ann["destructiveHint"], ann["idempotentHint"], ann["openWorldHint"])
    want = _WRITES.get(name, (True, False, True, name in _OPEN_WORLD_READS))
    assert got == want


def test_upload_hints_follow_persist_output(probe):
    """업로드 도구 — persist_output 이 켜졌으면 레코드를 넣으니 읽기가 아니다.

    꺼졌으면 남는 것이 없어 읽기다.
    """
    def ann(n):
        a = probe["by_name"][n]["annotations"]
        return (a["readOnlyHint"], a["destructiveHint"], a["idempotentHint"], a["openWorldHint"])
    assert ann("probe_persist") == (False, False, False, False)
    # 같은 dedup 키면 같은 레코드를 고친다
    assert ann("probe_dedup") == (False, False, True, False)
    assert ann("probe_pure") == (True, False, True, False)


@pytest.mark.parametrize("name", sorted(_CONTRACT))
def test_description_shape(probe, name):
    """C1 — 설명 길이 300 ~ 1,500자, 이력 · 날짜 · 결정 번호를 싣지 않는다."""
    d = probe["by_name"][name].get("description") or ""
    assert 300 <= len(d) <= 1500, len(d)
    assert not _HISTORY_RE.search(d), _HISTORY_RE.search(d).group(0)


def test_closed_vocab_and_bounds(probe):
    """C3 · C4 — 코드의 상수 집합만 enum 으로, 코드가 쓰는 상한만 경계로."""
    p = {n: t["inputSchema"].get("properties", {}) for n, t in probe["by_name"].items()}
    assert p["data_aggregate"]["op"]["enum"] == probe["checks"]["aggregate_ops"]
    assert p["recommend_agents"]["onto"]["enum"] == ["off", "shadow", "rank"]
    assert p["agent_search"]["mode"]["enum"] == ["semantic", "fts", "tag", "hybrid"]
    assert p["agent_search"]["as_of_undated"]["enum"] == ["keep", "exclude"]
    lim = p["list_records"]["limit"]
    assert (lim["minimum"], lim["maximum"]) == (1, 100)    # list_records 의 min(limit, 100) 과 같다
    cl = p["cards_by_core"]["limit"]
    assert (cl["minimum"], cl["maximum"]) == (1, 100)      # onto_svc.cards_by_core 의 min(limit, 100) 과 같다
    tk = p["recommend_agents"]["top_k_tools"]
    assert (tk["minimum"], tk["maximum"]) == (0, 10)       # min(int(top_k_tools), 10) 과 같다
    # 대소문자를 가리지 않고 받는 칸은 닫지 않는다 — enum 이면 지금 되는 'Markdown' · 'doc' 를
    # 거절한다.
    assert "enum" not in p["get_context_bundle"]["format"]
    assert "enum" not in p["describe_data_capability"]["data_type"]
    assert all(probe["checks"]["rejects"].values()), probe["checks"]["rejects"]


def test_consumer_calls_still_validate(probe):
    """소비처 호출부의 실제 인자 · 모르는 인자 얹기 · as_of 의 글 'null' 이 고치기 전처럼 통한다."""
    c = probe["checks"]
    assert c["consumer_values"] is True
    assert (c["extra_agent_search"], c["extra_hybrid"], c["extra_tag_search"]) == (True, True, True)
    assert c["as_of_null_kept"] == {"as_of": "null"}


def test_script_hint_defaults_are_conservative(tmp_path):
    """표지를 말하지 않는 셸 스크립트 도구는 읽기로 표시하지 않는다.

    모르는 표지 칸은 매니페스트 오류다.
    """
    from api.mcp_scripts import load_manifests, script_tool_annotations

    script = tmp_path / "t.sh"
    script.write_text("#!/bin/sh\necho ok\n")
    script.chmod(0o755)
    (tmp_path / "plain.mcp.yaml").write_text("name: plain\ndescription: x\nscript: ./t.sh\n")
    (tmp_path / "typo.mcp.yaml").write_text(
        "name: typo\ndescription: x\nscript: ./t.sh\nannotations:\n  readonly: true\n")
    (tmp_path / "ro.mcp.yaml").write_text(
        "name: ro\ndescription: x\nscript: ./t.sh\n"
        "annotations:\n  read_only: true\n  destructive: true\n")
    got = {m.name: script_tool_annotations(m) for m in load_manifests(tmp_path)}
    assert set(got) == {"plain", "ro"}                       # typo 는 실려지지 않는다
    p = got["plain"]
    hints = (p.readOnlyHint, p.destructiveHint, p.idempotentHint, p.openWorldHint)
    assert hints == (False, True, False, True)
    assert (got["ro"].readOnlyHint, got["ro"].destructiveHint) == (True, False)
