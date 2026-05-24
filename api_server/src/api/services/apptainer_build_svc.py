"""Wave-5 P1 — Apptainer .def 생성 + 빌드 + smoke run.

본 모듈은 ``mcp_upload_svc`` 가 위임하는 두 작업을 담는다:
    1. .def 자동 생성 — runtime 별 base image + COPY + runscript.
    2. ``apptainer build`` 호출 + sha 기준 캐시 + build_log 캡쳐.
    3. samples 실행 (apptainer exec) + expected_exit 일치 검증.
    4. 등록된 도구 호출 시 컨테이너 안에서 인자 전달 + stdout 캡쳐.

운영 환경 가정:
    - 호스트에 ``apptainer`` 바이너리 존재. 없으면 build_sif/exec 모두 실패.
    - 폐쇄망: base image 는 사전 캐시된 docker image 또는 OCI registry mirror.

테스트 환경 가정:
    - env ``AIDH_BUILD_DRYRUN=1`` → ``apptainer build`` 호출 skip + fake sif
      (빈 파일) 생성. smoke_run / exec_in_container 도 fake pass.

MVP 범위:
    - Python runtime 만 .def 생성. 그 외는 NotImplementedError.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


_BUILD_TIMEOUT_SEC = 1800       # 30분 (wave-5 plan §13 BUILD_TIMEOUT)
_DEFAULT_PYTHON_BASE = "python:3.12-slim"


def _is_dryrun() -> bool:
    return (os.environ.get("AIDH_BUILD_DRYRUN") or "").strip() == "1"


# ---------------------------------------------------------------------------
# .def 텍스트 생성
# ---------------------------------------------------------------------------
def generate_def(manifest: dict[str, Any]) -> str:
    """매니페스트 dict → Apptainer .def 텍스트 (Python runtime 만).

    Args:
        manifest: ``{name, runtime, python_version, script}`` 최소 키 필요.

    Returns:
        Apptainer .def 형식 텍스트.

    Raises:
        NotImplementedError: Python 외 runtime.
    """
    runtime = (manifest.get("runtime") or "python").lower()
    if runtime != "python":
        raise NotImplementedError(
            f"runtime={runtime} 는 MVP 미지원. Python 만 .def 생성 가능."
        )

    pyver = str(manifest.get("python_version") or "3.12").strip()
    # base image — python:<pyver>-slim 으로 고정 (폐쇄망 캐시 단순화).
    if pyver in ("3.12", "3.11", "3.10"):
        base_image = f"python:{pyver}-slim"
    else:
        base_image = _DEFAULT_PYTHON_BASE

    script = str(manifest.get("script") or "tool.py").strip()
    name = str(manifest.get("name") or "uploaded_tool").strip()

    # %files: bundle 의 모든 파일을 /opt/tool 로 복사 (호출 시점 마운트 아님 — 빌드 내장).
    # %post: requirements.txt 가 있으면 설치.
    # %runscript: python /opt/tool/<script> "$@"
    return f"""Bootstrap: docker
From: {base_image}

%labels
    aidh.tool.name {name}
    aidh.tool.runtime python

%files
    . /opt/tool

%post
    set -eu
    if [ -f /opt/tool/requirements.txt ]; then
        pip install --no-cache-dir -r /opt/tool/requirements.txt
    fi
    chmod +x /opt/tool/{script} 2>/dev/null || true

%runscript
    exec python /opt/tool/{script} "$@"
"""


# ---------------------------------------------------------------------------
# Build — apptainer build + cache hit (sha 기준)
# ---------------------------------------------------------------------------
def build_sif(
    def_text: str,
    sha: str,
    dest_dir: Path,
) -> Path:
    """``apptainer build`` 호출하여 sif 생성. cache hit 시 skip.

    Args:
        def_text: generate_def 의 출력.
        sha: zip bundle sha256 — sif 파일명에 사용.
        dest_dir: sif 와 .def 가 저장될 디렉토리.

    Returns:
        생성된 sif 경로 (이미 존재하면 그대로 반환).

    Raises:
        RuntimeError: apptainer build 가 0 이 아닌 종료 코드 반환 시 (non-dryrun).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    sif_path = dest_dir / f"{sha}.sif"
    def_path = dest_dir / f"{sha}.def"
    log_path = dest_dir / f"{sha}.build.log"

    # 캐시 hit
    if sif_path.exists() and sif_path.stat().st_size > 0:
        log.info("apptainer_build: cache hit %s", sif_path)
        return sif_path

    def_path.write_text(def_text, encoding="utf-8")

    if _is_dryrun():
        # fake sif (빈 파일 — 단순 캐시 hit 시뮬레이션용)
        sif_path.write_bytes(b"")
        log_path.write_text("[dryrun] build skipped\n", encoding="utf-8")
        log.info("apptainer_build: dryrun fake sif at %s", sif_path)
        return sif_path

    # 실 apptainer build — subprocess (동기). 운영 worker 가 이 함수 호출.
    import subprocess
    try:
        cp = subprocess.run(
            ["apptainer", "build", "--fakeroot", str(sif_path), str(def_path)],
            capture_output=True,
            text=True,
            timeout=_BUILD_TIMEOUT_SEC,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "apptainer 바이너리 미존재 — 운영 서버 설치 필요."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"BUILD_TIMEOUT: {_BUILD_TIMEOUT_SEC}s 초과") from e

    log_text = (cp.stdout or "") + "\n----STDERR----\n" + (cp.stderr or "")
    log_path.write_text(log_text, encoding="utf-8")

    if cp.returncode != 0:
        # build_log_tail 마지막 50줄 — wave-5 plan §13.
        tail = "\n".join(log_text.splitlines()[-50:])
        raise RuntimeError(
            f"APT_INSTALL_FAIL or BUILD_FAIL (exit {cp.returncode})\n--- log tail ---\n{tail}"
        )

    return sif_path


# ---------------------------------------------------------------------------
# Smoke run — sample 1건 실행 후 expected_exit / expected_stdout_contains 검증.
# ---------------------------------------------------------------------------
def smoke_run(
    sif_path: Path,
    sample: dict[str, Any],
    manifest: Any,
) -> dict[str, Any]:
    """단일 sample 의 smoke 검증.

    sample 형식:
        {
            "args": {arg_name: value, ...},
            "expected_exit": int (default 0),
            "expected_stdout_contains": str (optional),
            "expected_parsed_keys": [str, ...] (optional, return.format=json 일 때)
        }

    Returns:
        {"ok": bool, "exit_code": int, "stdout": str, "stderr": str,
         "matched_exit": bool, "matched_contains": bool, "matched_parsed_keys": bool}
    """
    expected_exit = int(sample.get("expected_exit") or 0)
    expected_contains = sample.get("expected_stdout_contains")
    expected_parsed_keys = sample.get("expected_parsed_keys") or []
    args = dict(sample.get("args") or {})

    if _is_dryrun():
        # fake pass — exit 가 expected_exit 와 일치한다고 가정.
        # 단, expected_exit != 0 인 negative sample 도 통과되어야 하므로
        # exit_code 는 expected_exit 그대로 fake.
        return {
            "ok": True,
            "exit_code": expected_exit,
            "stdout": f"[dryrun] {manifest.name if hasattr(manifest, 'name') else 'tool'} args={json.dumps(args)}",
            "stderr": "",
            "dryrun": True,
            "matched_exit": True,
            "matched_contains": expected_contains is None,  # dryrun 은 검증 skip
            "matched_parsed_keys": not expected_parsed_keys,
        }

    # 실 apptainer exec — argv 합성 후 호출.
    argv = _build_argv(manifest, args)
    cmd = [
        "apptainer", "exec",
        "--containall", "--no-home", "--writable-tmpfs",
        "--net=none" if not _capability_net(manifest) else "--net",
        str(sif_path),
        *argv,
    ]
    timeout = int(getattr(manifest, "timeout_sec", 60) or 60)

    import subprocess
    try:
        cp = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"timeout after {timeout}s",
            "matched_exit": False,
            "matched_contains": False,
            "matched_parsed_keys": False,
        }

    matched_exit = (cp.returncode == expected_exit)
    matched_contains = (
        expected_contains is None
        or str(expected_contains) in (cp.stdout or "")
    )
    matched_parsed_keys = True
    if expected_parsed_keys and (cp.stdout or "").strip():
        try:
            parsed = json.loads(cp.stdout.strip().splitlines()[-1])
            matched_parsed_keys = all(k in parsed for k in expected_parsed_keys)
        except Exception:
            matched_parsed_keys = False

    ok = matched_exit and matched_contains and matched_parsed_keys
    return {
        "ok": ok,
        "exit_code": cp.returncode,
        "stdout": cp.stdout or "",
        "stderr": cp.stderr or "",
        "matched_exit": matched_exit,
        "matched_contains": matched_contains,
        "matched_parsed_keys": matched_parsed_keys,
    }


# ---------------------------------------------------------------------------
# 호출 시 dispatch helper — mcp_upload_svc.dispatch_call 이 위임.
# ---------------------------------------------------------------------------
async def exec_in_container(manifest: Any, args: dict[str, Any]) -> dict[str, Any]:
    """apptainer exec subprocess 비동기 호출 (도구 호출 시점)."""
    if _is_dryrun():
        return {
            "ok": True,
            "exit_code": 0,
            "stdout": json.dumps({"_dryrun": True, "args": args}),
            "stderr": "",
            "dryrun": True,
        }

    sif_path = manifest.sif_path
    if sif_path is None or not Path(sif_path).exists():
        return {
            "ok": False,
            "error": f"sif not found: {sif_path}",
            "exit_code": -1,
        }

    argv = _build_argv(manifest, args)

    # P1.5 — 호스트 임시 디렉토리를 컨테이너 /work 에 bind 마운트.
    # 도구가 /work/out.png 등에 쓰면 호스트에서 capture 가능.
    # capture_files 비활성이어도 항상 마운트해서 호환 (스캔 안 하면 무영향).
    import tempfile as _tempfile
    host_workdir = Path(_tempfile.mkdtemp(prefix="aidh-work-"))

    cmd = [
        "apptainer", "exec",
        "--containall", "--no-home", "--writable-tmpfs",
        "--bind", f"{host_workdir}:/work",
        "--net=none" if not _capability_net(manifest) else "--net",
        str(sif_path),
        *argv,
    ]
    timeout = int(getattr(manifest, "timeout_sec", 60) or 60)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        return {
            "ok": False,
            "error": f"apptainer not available: {e}",
            "exit_code": -1,
            "workdir": str(host_workdir),
        }

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {
            "ok": False,
            "error": f"timeout after {timeout}s",
            "exit_code": -1,
            "timeout": True,
        }

    stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
    stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
    rc = int(proc.returncode or 0)
    result: dict[str, Any] = {
        "ok": rc == 0,
        "exit_code": rc,
        "stdout": stdout,
        "stderr": stderr,
        "workdir": str(host_workdir),  # capture_files 가 스캔 (caller 책임 — 후 cleanup 도)
    }
    if getattr(manifest, "return_format", "text") == "json" and rc == 0 and stdout.strip():
        try:
            result["parsed"] = json.loads(stdout.strip().splitlines()[-1])
        except Exception as e:
            result["parse_error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# 내부 — argv 합성 (long_flags / positional)
# ---------------------------------------------------------------------------
def _build_argv(manifest: Any, values: dict[str, Any]) -> list[str]:
    """매니페스트 + 호출 인자 → argv. wave-4 mcp_scripts._build_cmdline 동등."""
    argv: list[str] = []
    args_attr = getattr(manifest, "args", []) or []
    style = getattr(manifest, "args_style", "long_flags")

    if style == "positional":
        for arg in args_attr:
            v = values.get(arg.name, arg.default)
            argv.append("" if v is None else str(v))
        return argv

    for arg in args_attr:
        v = values.get(arg.name, arg.default)
        flag = "--" + arg.name.replace("_", "-")
        if isinstance(v, bool):
            if v:
                argv.append(flag)
        elif v is not None:
            argv.append(flag)
            argv.append(str(v))
    return argv


def _capability_net(manifest: Any) -> bool:
    """platform_capability.net 추출 — 향후 매니페스트 확장 시 활성."""
    cap = getattr(manifest, "platform_capability", None) or {}
    if isinstance(cap, dict):
        return bool(cap.get("net") or False)
    return False


__all__ = [
    "build_sif",
    "exec_in_container",
    "generate_def",
    "smoke_run",
]
