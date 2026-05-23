"""Wave-5 P1 — mcp_upload_svc / apptainer_build_svc 단위 테스트.

검증 범위 (PASS 목표 8건):
    1. validate_manifest 정상 (모든 required 필드)
    2. INVALID_MANIFEST — name regex 위반
    3. RESERVED_NAME — echo_args 충돌 거부
    4. NO_SAMPLES — samples 누락 시 거절
    5. persist_output placeholder — {args.X}/{parsed.Y}/{tool_name} 치환
    6. dispatch_call dryrun — AIDH_MCP_UPLOADS_DRYRUN=1 시 실 apptainer skip
    7. apptainer build dryrun + cache hit (동일 sha 면 재빌드 skip)
    8. smoke_run sample expected_exit 일치 케이스 (dryrun)

전제:
    - subprocess (apptainer) 는 모두 ``AIDH_BUILD_DRYRUN`` /
      ``AIDH_MCP_UPLOADS_DRYRUN`` env 로 격리. 호스트에 apptainer 미설치라도 PASS.
"""
from __future__ import annotations

import asyncio
import json
import os
import zipfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 픽스처 helpers
# ---------------------------------------------------------------------------
def _good_manifest_dict(name: str = "my_tool") -> dict:
    return {
        "name": name,
        "description": "테스트 도구 — 단순 echo.",
        "script": "tool.py",
        "runtime": "python",
        "python_version": "3.12",
        "args": [
            {"name": "x", "type": "string", "required": True, "description": "test"},
            {"name": "n", "type": "integer", "default": 1, "description": "count"},
        ],
        "timeout_sec": 30,
        "return": {"format": "json"},
        "persist_output": {
            "enabled": True,
            "data_type": "SIM",
            "team": "HE",
            "group": "CAE",
            "title_template": "Result: {args.x} ({tool_name})",
            "summary_template": "n={args.n}, computed={parsed.value}",
            "tags": ["test", "wave5"],
            "dedup_key": "{args.x}_{args.n}",
        },
        "llm_hints": {
            "when_to_use": "테스트 시.",
            "example_calls": [],
        },
    }


def _make_zip_bundle(tmp_path: Path, manifest_dict: dict, with_samples: bool = True) -> Path:
    """tmp 디렉토리에 manifest.yaml + tool.py + samples/ 포함하는 zip 생성."""
    import yaml  # type: ignore[import-not-found]

    src = tmp_path / "bundle_src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "manifest.yaml").write_text(yaml.safe_dump(manifest_dict), encoding="utf-8")
    (src / "tool.py").write_text(
        '#!/usr/bin/env python3\nimport json, sys\n'
        'print(json.dumps({"value": 42}))\n',
        encoding="utf-8",
    )
    if with_samples:
        sdir = src / "samples"
        sdir.mkdir(exist_ok=True)
        (sdir / "case1.json").write_text(
            json.dumps({"args": {"x": "hello"}, "expected_exit": 0}),
            encoding="utf-8",
        )

    zpath = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in src.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(src))
    return zpath


# ---------------------------------------------------------------------------
# 1. validate_manifest 정상
# ---------------------------------------------------------------------------
def test_validate_manifest_ok():
    from api.services.mcp_upload_svc import validate_manifest

    m = validate_manifest(_good_manifest_dict())
    assert m.name == "my_tool"
    assert m.runtime == "python"
    assert len(m.args) == 2
    assert m.args[0].name == "x" and m.args[0].required is True
    assert m.args[1].name == "n" and m.args[1].default == 1
    assert m.persist_output.enabled is True
    assert m.persist_output.data_type == "SIM"
    assert m.return_format == "json"
    assert m.timeout_sec == 30


# ---------------------------------------------------------------------------
# 2. INVALID_MANIFEST — name regex
# ---------------------------------------------------------------------------
def test_validate_manifest_invalid_name():
    from api.services.mcp_upload_svc import UploadError, validate_manifest

    raw = _good_manifest_dict(name="Bad-Name!")
    with pytest.raises(UploadError) as exc_info:
        validate_manifest(raw)
    assert exc_info.value.code == "INVALID_MANIFEST"
    assert "name" in exc_info.value.message_ko


# ---------------------------------------------------------------------------
# 3. RESERVED_NAME — echo_args 충돌
# ---------------------------------------------------------------------------
def test_validate_manifest_reserved_name():
    from api.services.mcp_upload_svc import UploadError, validate_manifest

    raw = _good_manifest_dict(name="echo_args")
    with pytest.raises(UploadError) as exc_info:
        validate_manifest(raw)
    assert exc_info.value.code == "RESERVED_NAME"


def test_validate_manifest_reserved_discover():
    """built-in MCP tool 이름과 충돌도 거부."""
    from api.services.mcp_upload_svc import UploadError, validate_manifest

    raw = _good_manifest_dict(name="discover")
    with pytest.raises(UploadError) as exc_info:
        validate_manifest(raw)
    assert exc_info.value.code == "RESERVED_NAME"


# ---------------------------------------------------------------------------
# 4. NO_SAMPLES — samples 누락 거절
# ---------------------------------------------------------------------------
def test_process_upload_no_samples(tmp_path: Path, monkeypatch):
    """zip 에 samples/ 가 없으면 NO_SAMPLES 에러."""
    from api.services.mcp_upload_svc import UploadError, process_upload

    monkeypatch.setenv("AIDH_BUILD_DRYRUN", "1")
    monkeypatch.setenv("AIDH_MCP_UPLOADS_DIR", str(tmp_path / "uploads"))

    zpath = _make_zip_bundle(tmp_path, _good_manifest_dict(), with_samples=False)
    with pytest.raises(UploadError) as exc_info:
        process_upload(zpath, uploader="alice@example.com")
    assert exc_info.value.code == "NO_SAMPLES"


# ---------------------------------------------------------------------------
# 5. persist_output placeholder 렌더링
# ---------------------------------------------------------------------------
def test_render_template_placeholders():
    from api.services.mcp_upload_svc import render_template

    ctx = {
        "tool_name": "my_tool",
        "args": {"x": "hello", "n": 3},
        "parsed": {"value": 42, "label": "ok"},
    }
    # tool_name
    assert render_template("name={tool_name}", ctx) == "name=my_tool"
    # args.X
    assert render_template("x={args.x}, n={args.n}", ctx) == "x=hello, n=3"
    # parsed.Y
    assert render_template("v={parsed.value}", ctx) == "v=42"
    # 결합
    assert (
        render_template("{tool_name}: {args.x}={parsed.value}", ctx)
        == "my_tool: hello=42"
    )
    # 누락 키 → 빈 문자열
    assert render_template("missing={args.missing}", ctx) == "missing="
    # escape {{ }} → { }
    assert render_template("literal {{x}} = {args.x}", ctx) == "literal {x} = hello"


def test_render_persist_preview_via_dispatch_dryrun(monkeypatch):
    """dispatch_call dryrun 결과의 persist_preview 가 placeholder 치환된 dict 인지."""
    from api.services.mcp_upload_svc import dispatch_call, validate_manifest

    monkeypatch.setenv("AIDH_MCP_UPLOADS_DRYRUN", "1")

    manifest = validate_manifest(_good_manifest_dict())
    result = asyncio.run(dispatch_call(manifest, {"x": "world", "n": 7}))
    assert result["ok"] is True
    assert result["dryrun"] is True
    preview = result["persist_preview"]
    # title_template = "Result: {args.x} ({tool_name})"
    assert preview["title"] == "Result: world (my_tool)"
    # dryrun stdout 은 {"_dryrun":true,...} 이므로 parsed.value 없음 → 빈 문자열
    assert preview["summary"].startswith("n=7, computed=")
    assert preview["data_type"] == "SIM"
    assert "test" in preview["tags"]
    # dedup_key = "{args.x}_{args.n}"
    assert preview["dedup_key"] == "world_7"


# ---------------------------------------------------------------------------
# 6. dispatch_call dryrun
# ---------------------------------------------------------------------------
def test_dispatch_call_dryrun_no_subprocess(monkeypatch):
    """AIDH_MCP_UPLOADS_DRYRUN=1 면 실 subprocess 호출 없이 fake 응답."""
    from api.services.mcp_upload_svc import dispatch_call, validate_manifest

    monkeypatch.setenv("AIDH_MCP_UPLOADS_DRYRUN", "1")

    # asyncio.create_subprocess_exec 가 호출되면 즉시 실패하도록 monkeypatch
    async def _boom(*args, **kw):
        raise AssertionError("subprocess called in dryrun!")

    monkeypatch.setattr("asyncio.create_subprocess_exec", _boom)

    manifest = validate_manifest(_good_manifest_dict())
    result = asyncio.run(dispatch_call(manifest, {"x": "hi"}))
    assert result["ok"] is True
    assert result["dryrun"] is True


def test_dispatch_call_missing_required():
    """필수 인자 누락 시 ok=False."""
    from api.services.mcp_upload_svc import dispatch_call, validate_manifest

    manifest = validate_manifest(_good_manifest_dict())
    result = asyncio.run(dispatch_call(manifest, {}))  # x 누락
    assert result["ok"] is False
    assert "missing required" in (result.get("error") or "")


# ---------------------------------------------------------------------------
# 7. apptainer build dryrun + cache hit
# ---------------------------------------------------------------------------
def test_build_sif_dryrun_and_cache(tmp_path: Path, monkeypatch):
    """dryrun=1 으로 fake sif 생성 + 두 번째 호출은 cache hit."""
    from api.services.apptainer_build_svc import build_sif, generate_def

    monkeypatch.setenv("AIDH_BUILD_DRYRUN", "1")

    def_text = generate_def({
        "name": "demo", "runtime": "python", "python_version": "3.12",
        "script": "tool.py",
    })
    assert "Bootstrap: docker" in def_text
    assert "python:3.12-slim" in def_text

    dest = tmp_path / "out"
    sif1 = build_sif(def_text, "abc123" * 10, dest)
    assert sif1.exists()
    mtime1 = sif1.stat().st_mtime_ns

    # 캐시 hit — 동일 sha 재호출 시 파일은 그대로 (mtime 변경 안 됨이 이상적이나
    # 빈 파일 캐시 hit 분기는 그냥 기존 경로 반환).
    # cache hit 분기를 타려면 sif 가 size>0 이어야 함 → 임의 바이트 채워서 검증.
    sif1.write_bytes(b"FAKE_SIF_CACHED")
    sif2 = build_sif(def_text, "abc123" * 10, dest)
    assert sif2 == sif1
    # cache hit 이면 mtime 안 바뀜 (덮어쓰기 없음)
    assert sif2.read_bytes() == b"FAKE_SIF_CACHED"


def test_generate_def_non_python_raises():
    """Python 외 runtime 은 NotImplementedError."""
    from api.services.apptainer_build_svc import generate_def

    with pytest.raises(NotImplementedError):
        generate_def({"name": "demo", "runtime": "node"})


# ---------------------------------------------------------------------------
# 8. smoke_run sample expected_exit 일치 / 불일치
# ---------------------------------------------------------------------------
def test_smoke_run_dryrun_exit_match(tmp_path: Path, monkeypatch):
    """dryrun 에서 expected_exit 일치 (matched_exit=True)."""
    from api.services.apptainer_build_svc import smoke_run
    from api.services.mcp_upload_svc import validate_manifest

    monkeypatch.setenv("AIDH_BUILD_DRYRUN", "1")
    manifest = validate_manifest(_good_manifest_dict())

    sample_ok = {"args": {"x": "y"}, "expected_exit": 0}
    sif_fake = tmp_path / "fake.sif"
    sif_fake.write_bytes(b"")

    r1 = smoke_run(sif_fake, sample_ok, manifest)
    assert r1["ok"] is True
    assert r1["matched_exit"] is True

    # negative sample (expected_exit=2) 도 dryrun 은 expected_exit 그대로 반환하므로 PASS.
    sample_neg = {"args": {"x": "y"}, "expected_exit": 2}
    r2 = smoke_run(sif_fake, sample_neg, manifest)
    assert r2["ok"] is True
    assert r2["exit_code"] == 2
    assert r2["matched_exit"] is True


# ---------------------------------------------------------------------------
# 보너스: process_upload 전체 파이프라인 (dryrun) — happy path
# ---------------------------------------------------------------------------
def test_process_upload_happy_path_dryrun(tmp_path: Path, monkeypatch):
    """zip 업로드 → 검증 → fake build → smoke (dryrun) → ok=True."""
    from api.services.mcp_upload_svc import process_upload

    monkeypatch.setenv("AIDH_BUILD_DRYRUN", "1")
    monkeypatch.setenv("AIDH_MCP_UPLOADS_DIR", str(tmp_path / "uploads"))

    zpath = _make_zip_bundle(tmp_path, _good_manifest_dict())
    result = process_upload(zpath, uploader="alice@example.com")
    assert result["ok"] is True
    assert result["name"] == "my_tool"
    assert len(result["sha"]) == 64
    assert result["sif_path"].endswith(".sif")
    assert len(result["smoke"]) == 1
    assert result["smoke"][0]["matched_exit"] is True


def test_process_upload_missing_uploader(tmp_path: Path, monkeypatch):
    """uploader 빈 문자열 → MISSING_UPLOADER."""
    from api.services.mcp_upload_svc import UploadError, process_upload

    monkeypatch.setenv("AIDH_BUILD_DRYRUN", "1")
    monkeypatch.setenv("AIDH_MCP_UPLOADS_DIR", str(tmp_path / "uploads"))

    zpath = _make_zip_bundle(tmp_path, _good_manifest_dict())
    with pytest.raises(UploadError) as exc_info:
        process_upload(zpath, uploader="   ")
    assert exc_info.value.code == "MISSING_UPLOADER"
