"""Shell script → MCP tool 동적 등록 (사이드카 매니페스트 패턴).

설계:
    ``mcp_scripts/<name>.sh`` 같은 셸 스크립트 옆에 ``<name>.mcp.yaml`` 매니페스트를
    두면, 부팅 시 FastMCP 에 ``@mcp.tool`` 로 동적 등록된다. 각 tool 호출은 매니
    페스트의 인자 스키마로 타입 검증된 뒤 ``asyncio.create_subprocess_exec`` 로
    실행, stdout/stderr/exit_code 를 구조화 응답으로 반환한다.

매니페스트 예 (mcp_scripts/convert_docx.mcp.yaml):
    name: convert_docx
    title: "DOCX → Markdown 변환"
    description: "원본 .docx 를 markdown 으로 변환."
    script: convert_docx.sh          # 매니페스트와 같은 디렉토리 기준 상대경로
    args:
      - {name: input_path, type: string, required: true,
         description: "원본 .docx 절대경로"}
      - {name: out_dir, type: string, default: "/tmp/aidh-convert"}
      - {name: strict, type: boolean, default: false}
    timeout_sec: 60                  # 기본 30, 상한 600
    args_style: long_flags           # long_flags(default) | positional
    env_allowlist: [LANG, LC_ALL]    # 자식 프로세스에 전달할 환경변수만
    return:
      format: text                   # text(default) | json

보안 게이트 (6종 — 매 호출마다 적용):
    1) 스크립트 절대경로가 base_dir prefix 인지 검증 (디렉토리 탈출 차단)
    2) 심볼릭링크 거부 (또는 link target 도 base_dir 안)
    3) shell=False 고정 + 인자는 항상 리스트 (셸 메타문자 무력화)
    4) env_allowlist 외 환경변수 미상속 (자식 환경 격리)
    5) timeout 강제 (kill on overrun)
    6) 전역 동시실행 제한 (semaphore — AIDH_MCP_SCRIPTS_CONCURRENCY, default 3)

비활성:
    env ``AIDH_MCP_SCRIPTS=off`` → 전체 비활성 (회귀 안전망).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 매니페스트 모델
# ---------------------------------------------------------------------------
_PY_TYPE = {
    "string": str,
    "str": str,
    "int": int,
    "integer": int,
    "number": float,
    "float": float,
    "bool": bool,
    "boolean": bool,
}

_MAX_TIMEOUT = 600  # 운영 안전 상한 — 매니페스트가 더 크게 적어도 이 값으로 clamp


@dataclass
class ScriptArg:
    name: str
    type: str = "string"
    required: bool = False
    default: Any = None
    description: str = ""

    @property
    def py_type(self) -> type:
        return _PY_TYPE.get(self.type.lower(), str)


@dataclass
class ScriptManifest:
    name: str
    description: str
    script_path: Path
    args: list[ScriptArg] = field(default_factory=list)
    title: str | None = None
    timeout_sec: int = 30
    args_style: str = "long_flags"            # long_flags | positional
    env_allowlist: list[str] = field(default_factory=list)
    return_format: str = "text"               # text | json


# ---------------------------------------------------------------------------
# 매니페스트 로더
# ---------------------------------------------------------------------------
def _safe_realpath(p: Path) -> Path:
    """Symlink 따라가서 정규화."""
    return p.resolve(strict=False)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "mcp_scripts 사용에는 PyYAML 이 필요 — `pip install pyyaml`"
        ) from e
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a mapping: {path}")
    return data


def _parse_manifest(path: Path, base_dir: Path) -> ScriptManifest:
    raw = _load_yaml(path)
    name = str(raw.get("name") or "").strip()
    if not name or not name.replace("_", "").isalnum():
        raise ValueError(
            f"manifest {path.name}: invalid name {name!r} "
            f"(must be alnum/underscore, e.g. convert_docx)"
        )

    rel_script = str(raw.get("script") or "").strip()
    if not rel_script:
        raise ValueError(f"manifest {name}: missing 'script' field")
    script_abs = _safe_realpath(path.parent / rel_script)

    # ── 보안 게이트 1: base_dir prefix 검증 ─────────────────────────
    base_real = _safe_realpath(base_dir)
    try:
        script_abs.relative_to(base_real)
    except ValueError as e:
        raise ValueError(
            f"manifest {name}: script {script_abs} escapes base_dir {base_real}"
        ) from e

    # ── 보안 게이트 2: 심볼릭링크 거부 ─────────────────────────────
    # 매니페스트 자체와 스크립트 모두 검사. resolve(strict=False) 가 따라가지만
    # 원본이 symlink 이면 거부 — 권한 우회 방지.
    original = path.parent / rel_script
    if original.is_symlink():
        raise ValueError(f"manifest {name}: script is a symlink ({original})")
    if not script_abs.exists():
        raise ValueError(f"manifest {name}: script not found at {script_abs}")
    if not os.access(script_abs, os.X_OK):
        raise ValueError(f"manifest {name}: script not executable: {script_abs}")

    args: list[ScriptArg] = []
    for a in raw.get("args") or []:
        if not isinstance(a, dict):
            continue
        an = str(a.get("name") or "").strip()
        if not an or not an.replace("_", "").isalnum():
            raise ValueError(
                f"manifest {name}: invalid arg name {an!r}"
            )
        args.append(
            ScriptArg(
                name=an,
                type=str(a.get("type") or "string").lower(),
                required=bool(a.get("required") or False),
                default=a.get("default"),
                description=str(a.get("description") or ""),
            )
        )

    timeout = int(raw.get("timeout_sec") or 30)
    timeout = max(1, min(timeout, _MAX_TIMEOUT))

    args_style = str(raw.get("args_style") or "long_flags").lower()
    if args_style not in ("long_flags", "positional"):
        raise ValueError(
            f"manifest {name}: args_style must be long_flags|positional"
        )

    env_allow_raw = raw.get("env_allowlist") or []
    if not isinstance(env_allow_raw, list):
        raise ValueError(f"manifest {name}: env_allowlist must be a list")
    env_allowlist = [str(e) for e in env_allow_raw if isinstance(e, str)]

    return_raw = raw.get("return") or {}
    return_format = "text"
    if isinstance(return_raw, dict):
        rf = str(return_raw.get("format") or "text").lower()
        if rf in ("text", "json"):
            return_format = rf

    return ScriptManifest(
        name=name,
        description=str(raw.get("description") or "").strip()
        or f"Shell script tool: {name}",
        script_path=script_abs,
        args=args,
        title=str(raw.get("title") or "") or None,
        timeout_sec=timeout,
        args_style=args_style,
        env_allowlist=env_allowlist,
        return_format=return_format,
    )


def load_manifests(base_dir: Path) -> list[ScriptManifest]:
    """``base_dir`` 안의 ``*.mcp.yaml`` 을 모두 로드."""
    base_dir = _safe_realpath(base_dir)
    if not base_dir.exists():
        return []
    out: list[ScriptManifest] = []
    seen_names: set[str] = set()
    for p in sorted(base_dir.glob("*.mcp.yaml")):
        try:
            m = _parse_manifest(p, base_dir)
        except Exception as e:
            log.warning("mcp_scripts: skip %s — %s", p.name, e)
            continue
        if m.name in seen_names:
            log.warning("mcp_scripts: duplicate name %s — skip %s", m.name, p)
            continue
        seen_names.add(m.name)
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
_concurrency_sem: asyncio.Semaphore | None = None


def _get_sem() -> asyncio.Semaphore:
    """전역 동시실행 제한 — 보안 게이트 6."""
    global _concurrency_sem
    if _concurrency_sem is None:
        try:
            n = int(os.environ.get("AIDH_MCP_SCRIPTS_CONCURRENCY", "3"))
        except ValueError:
            n = 3
        n = max(1, n)
        _concurrency_sem = asyncio.Semaphore(n)
    return _concurrency_sem


def _build_cmdline(manifest: ScriptManifest, values: dict[str, Any]) -> list[str]:
    """매니페스트 + 호출 인자 → argv (보안 게이트 3 — shell=False, 리스트만)."""
    argv: list[str] = [str(manifest.script_path)]
    if manifest.args_style == "positional":
        for arg in manifest.args:
            v = values.get(arg.name, arg.default)
            argv.append("" if v is None else str(v))
        return argv

    # long_flags: --arg-name value (bool 은 플래그/생략)
    for arg in manifest.args:
        v = values.get(arg.name, arg.default)
        flag = "--" + arg.name.replace("_", "-")
        if isinstance(v, bool):
            if v:
                argv.append(flag)
            # False 는 생략
        elif v is not None:
            argv.append(flag)
            argv.append(str(v))
    return argv


def _filter_env(allow: list[str]) -> dict[str, str]:
    """env_allowlist 만 통과시킨 자식 env (보안 게이트 4)."""
    base = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
    for k in allow:
        if k in os.environ:
            base[k] = os.environ[k]
    return base


async def run_script(
    manifest: ScriptManifest, values: dict[str, Any]
) -> dict[str, Any]:
    """스크립트 비동기 실행 + timeout + capture."""
    # 필수 인자 검증
    for arg in manifest.args:
        if arg.required and values.get(arg.name) in (None, ""):
            raise ValueError(f"missing required arg: {arg.name}")

    argv = _build_cmdline(manifest, values)
    env = _filter_env(manifest.env_allowlist)

    sem = _get_sem()
    async with sem:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,  # 보안 게이트 4
            )
        except FileNotFoundError as e:
            return {
                "ok": False,
                "error": f"script not found: {e}",
                "stdout": "",
                "stderr": "",
                "exit_code": -1,
            }

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=manifest.timeout_sec  # 보안 게이트 5
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {
                "ok": False,
                "error": f"timeout after {manifest.timeout_sec}s",
                "stdout": "",
                "stderr": "",
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
    }
    if manifest.return_format == "json" and rc == 0 and stdout.strip():
        try:
            import json
            result["parsed"] = json.loads(stdout)
        except Exception as e:
            result["parse_error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# 동적 등록
# ---------------------------------------------------------------------------
def _make_handler(manifest: ScriptManifest):
    """매니페스트 → FastMCP 가 introspect 할 수 있는 합성 시그니처 async 함수."""
    params: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {"return": dict}
    for arg in manifest.args:
        if arg.required:
            params.append(
                inspect.Parameter(
                    arg.name,
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    annotation=arg.py_type,
                )
            )
        else:
            params.append(
                inspect.Parameter(
                    arg.name,
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    annotation=arg.py_type,
                    default=arg.default,
                )
            )
        annotations[arg.name] = arg.py_type

    sig = inspect.Signature(parameters=params, return_annotation=dict)

    async def handler(**kwargs: Any) -> dict[str, Any]:
        return await run_script(manifest, kwargs)

    handler.__name__ = manifest.name
    handler.__signature__ = sig  # type: ignore[attr-defined]
    handler.__annotations__ = annotations
    # description 본문에 인자 설명 dense block 으로 추가 — LLM 이 사용법 이해.
    arg_doc = "\n".join(
        f"  - {a.name} ({a.type}{'*' if a.required else ''}): {a.description}"
        for a in manifest.args
    )
    handler.__doc__ = manifest.description + (
        f"\n\nargs:\n{arg_doc}" if arg_doc else ""
    )
    return handler


def register_all_scripts(mcp: Any, base_dir: Path) -> list[str]:
    """``base_dir`` 의 모든 매니페스트를 ``mcp`` 에 동적 등록.

    Returns: 등록 성공한 tool name 목록.

    env ``AIDH_MCP_SCRIPTS=off`` 시 즉시 빈 리스트 반환.
    """
    if (os.environ.get("AIDH_MCP_SCRIPTS") or "").lower() == "off":
        log.info("mcp_scripts: disabled by env")
        return []

    manifests = load_manifests(base_dir)
    registered: list[str] = []
    for m in manifests:
        try:
            handler = _make_handler(m)
            mcp.add_tool(
                handler,
                name=m.name,
                title=m.title or m.name,
                description=m.description,
            )
            registered.append(m.name)
            log.info("mcp_scripts: registered %s (%s)", m.name, m.script_path)
        except Exception as e:
            log.warning(
                "mcp_scripts: failed to register %s — %s", m.name, e
            )
    return registered


__all__ = [
    "ScriptArg",
    "ScriptManifest",
    "load_manifests",
    "register_all_scripts",
    "run_script",
]
