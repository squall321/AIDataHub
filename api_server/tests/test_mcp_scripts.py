"""mcp_scripts dynamic tool — 매니페스트 로더 / 실행 / 보안 게이트 검증.

라이브 MCP 서버 없이 가능한 부분만. 통합은 별도.
"""
from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 픽스처 — temp 디렉토리에 스크립트 + 매니페스트 1세트 생성
# ---------------------------------------------------------------------------
def _make_tool(
    base: Path,
    *,
    name: str = "demo",
    body: str = "#!/usr/bin/env bash\necho \"$@\"\n",
    manifest_extra: str = "",
) -> Path:
    script = base / f"{name}.sh"
    script.write_text(body)
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    manifest = base / f"{name}.mcp.yaml"
    manifest.write_text(
        f"""name: {name}
description: "demo"
script: ./{name}.sh
args:
  - {{name: msg, type: string, required: true}}
{manifest_extra}
"""
    )
    return manifest


# ---------------------------------------------------------------------------
# 매니페스트 로더
# ---------------------------------------------------------------------------
def test_load_manifest_basic(tmp_path: Path) -> None:
    from api.mcp_scripts import load_manifests

    _make_tool(tmp_path, name="demo")
    out = load_manifests(tmp_path)
    assert len(out) == 1
    m = out[0]
    assert m.name == "demo"
    assert m.script_path.exists()
    assert m.args[0].name == "msg"
    assert m.args[0].required is True


def test_load_skip_invalid_name(tmp_path: Path) -> None:
    """이름이 영숫자/언더스코어 외면 skip."""
    from api.mcp_scripts import load_manifests

    (tmp_path / "bad.mcp.yaml").write_text(
        "name: 'bad name!'\ndescription: x\nscript: ./bad.sh\n"
    )
    (tmp_path / "bad.sh").write_text("#!/bin/sh\n")
    out = load_manifests(tmp_path)
    assert out == []


def test_load_reject_path_escape(tmp_path: Path) -> None:
    """script 가 base_dir 바깥이면 거부 (보안 게이트 1)."""
    from api.mcp_scripts import load_manifests

    outside = tmp_path.parent / "evil.sh"
    outside.write_text("#!/bin/sh\necho pwned\n")
    outside.chmod(0o755)
    (tmp_path / "evil.mcp.yaml").write_text(
        f"name: evil\ndescription: x\nscript: {outside}\n"
    )
    out = load_manifests(tmp_path)
    assert out == []  # rejected


def test_load_reject_symlink(tmp_path: Path) -> None:
    """script 가 심볼릭링크면 거부 (보안 게이트 2)."""
    from api.mcp_scripts import load_manifests

    real = tmp_path / "real.sh"
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    link = tmp_path / "link.sh"
    link.symlink_to(real)
    (tmp_path / "link.mcp.yaml").write_text(
        "name: link\ndescription: x\nscript: ./link.sh\n"
    )
    out = load_manifests(tmp_path)
    assert out == []


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
def test_run_script_long_flags(tmp_path: Path) -> None:
    """long_flags 스타일로 인자가 정확히 전달됨."""
    from api.mcp_scripts import load_manifests, run_script

    _make_tool(tmp_path, name="echoer", body="#!/usr/bin/env bash\necho \"$@\"\n")
    manifests = load_manifests(tmp_path)
    assert len(manifests) == 1
    result = asyncio.run(run_script(manifests[0], {"msg": "hello"}))
    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert "--msg hello" in result["stdout"]


def test_run_script_required_missing(tmp_path: Path) -> None:
    from api.mcp_scripts import load_manifests, run_script

    _make_tool(tmp_path, name="echoer")
    m = load_manifests(tmp_path)[0]
    with pytest.raises(ValueError, match="missing required arg"):
        asyncio.run(run_script(m, {}))


def test_run_script_timeout(tmp_path: Path) -> None:
    """timeout 강제 (보안 게이트 5)."""
    from api.mcp_scripts import load_manifests, run_script

    _make_tool(
        tmp_path,
        name="slow",
        body="#!/usr/bin/env bash\nsleep 10\n",
        manifest_extra="timeout_sec: 1\n",
    )
    m = load_manifests(tmp_path)[0]
    result = asyncio.run(run_script(m, {"msg": "x"}))
    assert result["ok"] is False
    assert result.get("timeout") is True


def test_run_script_env_isolation(tmp_path: Path) -> None:
    """env_allowlist 외 변수는 자식에 안 보임 (보안 게이트 4)."""
    from api.mcp_scripts import load_manifests, run_script

    _make_tool(
        tmp_path,
        name="envcheck",
        body="#!/usr/bin/env bash\necho \"SECRET=${SECRET:-unset}\"\n",
    )
    m = load_manifests(tmp_path)[0]
    os.environ["SECRET"] = "should_not_be_visible"
    try:
        result = asyncio.run(run_script(m, {"msg": "x"}))
        assert "SECRET=unset" in result["stdout"]
    finally:
        os.environ.pop("SECRET", None)


def test_run_script_shell_metachar_safe(tmp_path: Path) -> None:
    """인자에 셸 메타문자가 와도 무력화 (보안 게이트 3).

    페이로드는 stdout 에 리터럴로 echo. 부수효과 (파일 생성, command substitution)
    가 일어났는지로 셸 해석 여부 판정 — payload 자체에 포함된 문자열로 판정하면
    오탐 (false negative).
    """
    from api.mcp_scripts import load_manifests, run_script

    _make_tool(
        tmp_path,
        name="meta",
        body="#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n",
    )
    m = load_manifests(tmp_path)[0]
    canary = tmp_path / "aidh_pwned_canary"
    payload = f"; touch {canary} ; echo $(whoami)"
    result = asyncio.run(run_script(m, {"msg": payload}))
    # 1) 페이로드가 stdout 에 리터럴로 보임
    assert payload in result["stdout"]
    # 2) 셸이 해석됐다면 canary 파일이 생성됐을 것
    assert not canary.exists(), (
        f"canary file created — shell metachar was interpreted! "
        f"stdout={result['stdout']!r}"
    )
    # 3) command substitution `$(whoami)` 가 실행됐다면 그 결과가 stdout 에 추가로 보였을 것
    #    무력화됐다면 stdout 은 정확히 "--msg\n<payload>\n" 만.
    assert result["stdout"] == f"--msg\n{payload}\n"


# ---------------------------------------------------------------------------
# 동적 등록 — FastMCP add_tool 호환
# ---------------------------------------------------------------------------
def test_make_handler_signature(tmp_path: Path) -> None:
    """handler 의 합성 시그니처가 FastMCP introspect 호환."""
    import inspect

    from api.mcp_scripts import _make_handler, load_manifests

    _make_tool(tmp_path, name="sig", manifest_extra="  - {name: count, type: integer, default: 1}\n")
    m = load_manifests(tmp_path)[0]
    handler = _make_handler(m)
    sig = inspect.signature(handler)
    names = list(sig.parameters.keys())
    assert names == ["msg", "count"]
    assert sig.parameters["msg"].annotation is str
    assert sig.parameters["count"].annotation is int
    assert sig.parameters["count"].default == 1


def test_register_disabled_by_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from api.mcp_scripts import register_all_scripts

    monkeypatch.setenv("AIDH_MCP_SCRIPTS", "off")
    _make_tool(tmp_path, name="x")

    class FakeMcp:
        added: list[str] = []
        def add_tool(self, fn, **kw):
            self.added.append(kw.get("name", fn.__name__))

    mcp = FakeMcp()
    out = register_all_scripts(mcp, tmp_path)
    assert out == []
    assert mcp.added == []
