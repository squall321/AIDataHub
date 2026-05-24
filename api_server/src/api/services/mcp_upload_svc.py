"""Wave-5 P1 — CLI binary 업로드 → MCP tool 동적 등록 서비스.

설계 요지:
    - wave-4 ``mcp_scripts`` 가 운영자 작성 ``.sh`` 를 호스트 직접 실행한다면,
      wave-5 는 외부 업로드 zip 을 Apptainer 컨테이너로 격리 실행한다.
    - 본 모듈은 다음 책임을 가진다:
        1. ``register_all_uploads(mcp)`` — 부팅 시 DB ``mcp_uploads`` 에서
           등록된 도구 메타를 읽어 FastMCP 에 add_tool 으로 동적 등록.
        2. ``validate_manifest(raw)`` — pre-flight 검증 (wave-5 plan §5).
        3. ``process_upload(zip_path, uploader, ...)`` — 업로드 파이프라인.
        4. ``dispatch_call(name, args)`` — 호출 시점 apptainer exec subprocess.
        5. ``persist_output`` placeholder 엔진 — title/summary/dedup 등.

MVP 범위:
    - Python runtime 만 실제 빌드/실행 지원. Node/JVM/.NET/Wine 은
      ``NotImplementedError`` 로 reserve.
    - apptainer 실제 호출은 ``apptainer_build_svc`` 에 위임. env
      ``AIDH_MCP_UPLOADS_DRYRUN=1`` 이면 build/exec 모두 mock.

환경변수:
    - ``AIDH_MCP_UPLOADS=off``       — register_all_uploads 즉시 빈 리스트.
    - ``AIDH_MCP_UPLOADS_DRYRUN=1``  — dispatch_call 시 실 apptainer 호출 skip.
    - ``AIDH_MCP_UPLOADS_DIR``       — sif/manifest cache 루트 (default:
                                       ``<repo>/api_server/mcp_uploads/_uploads/``).
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 상수 / 정규식 / reserved set
# ---------------------------------------------------------------------------
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_ARG_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_MAX_TIMEOUT = 1800            # 30 분 — wave-5 plan §13 BUILD_TIMEOUT 동기.
_BUNDLE_LIMIT_BYTES = 100 * 1024 * 1024  # 100MB — wave-5 plan §5.
_VALID_DATA_TYPES = {"DOC", "DATA", "SIM", "CAD", "LOG", "FORM", "OTHER"}
_VALID_RUNTIMES = {"python", "node", "jar", "dotnet", "binary", "wine"}
_SUPPORTED_RUNTIMES = {"python"}   # MVP — 그 외는 NotImplementedError.

# wave-4 도구 + built-in MCP tool 이름 → 충돌 차단.
_RESERVED_NAMES = {
    "echo_args", "fetch_rerank_model",
    "discover", "list_agents", "recommend_agents",
    "get_agent_session", "agent_search",
    "semantic_search", "hybrid_search", "fts_search", "tag_search",
    "get_record", "get_record_sections", "get_context_bundle",
}

_PY_TYPE = {
    "string": str, "str": str,
    "integer": int, "int": int,
    "number": float, "float": float,
    "boolean": bool, "bool": bool,
}


# ---------------------------------------------------------------------------
# 매니페스트 dataclass — wave-4 ScriptManifest 의 wave-5 확장.
# ---------------------------------------------------------------------------
@dataclass
class UploadArg:
    name: str
    type: str = "string"
    required: bool = False
    default: Any = None
    description: str = ""

    @property
    def py_type(self) -> type:
        return _PY_TYPE.get(self.type.lower(), str)


@dataclass
class PersistOutput:
    enabled: bool = False
    data_type: str | None = None
    team: str | None = None
    group: str | None = None
    title_template: str = ""
    summary_template: str = ""
    body_template: str = ""
    tags: list[str] = field(default_factory=list)
    dedup_key: str = ""


@dataclass
class LLMHints:
    when_to_use: str = ""
    not_for: str = ""
    example_calls: list[dict[str, Any]] = field(default_factory=list)
    output_description: str = ""


@dataclass
class CaptureFiles:
    """도구 실행 후 ``scan_dir`` 의 산출물을 자동 캡쳐 → MCP content 동봉.

    image_extensions  → MCP ImageContent (base64 inline, Claude Desktop 인라인 렌더)
    text_extensions   → 본문 stdout 에 첨부 (작은 텍스트)
    resource_extensions → ImageContent (SVG) 또는 TextContent 안내 (PDF)

    크기 한도 초과 시 ``attachments_dir`` 로 저장 + URL 만 반환.
    """

    enabled: bool = False
    scan_dir: str = "/work/"
    image_extensions: list[str] = field(
        default_factory=lambda: ["png", "jpg", "jpeg", "gif", "webp"]
    )
    text_extensions: list[str] = field(
        default_factory=lambda: ["txt", "csv", "json", "md"]
    )
    resource_extensions: list[str] = field(default_factory=lambda: ["pdf", "svg"])
    max_inline_mb: int = 5
    max_total_mb: int = 20


@dataclass
class UploadManifest:
    name: str
    description: str
    script: str
    runtime: str = "python"
    python_version: str = "3.12"
    args: list[UploadArg] = field(default_factory=list)
    title: str | None = None
    timeout_sec: int = 60
    args_style: str = "long_flags"
    env_allowlist: list[str] = field(default_factory=list)
    return_format: str = "text"
    restrict_agents: list[str] = field(default_factory=list)
    persist_output: PersistOutput = field(default_factory=PersistOutput)
    llm_hints: LLMHints = field(default_factory=LLMHints)
    capture_files: CaptureFiles = field(default_factory=CaptureFiles)
    # build/runtime 시점 채워짐 (validate 단계에서는 None)
    sif_path: Path | None = None
    bundle_sha: str | None = None


# ---------------------------------------------------------------------------
# 에러 카탈로그 — wave-5 plan §13 의 코드 사용.
# ---------------------------------------------------------------------------
class UploadError(Exception):
    """업로드 파이프라인 표준 에러 — code + 한국어 진단."""

    def __init__(self, code: str, message_ko: str, suggested_action: str = "") -> None:
        self.code = code
        self.message_ko = message_ko
        self.suggested_action = suggested_action
        super().__init__(f"[{code}] {message_ko}")


# ---------------------------------------------------------------------------
# Pre-flight validation (§5)
# ---------------------------------------------------------------------------
def validate_manifest(raw: dict[str, Any]) -> UploadManifest:
    """매니페스트 dict → UploadManifest 검증·정규화. 실패 시 UploadError.

    검사 항목 (wave-5 plan §5 의 일부):
        - INVALID_MANIFEST : name regex, required 필드, type 불일치
        - RESERVED_NAME    : wave-4 + built-in name 충돌
        - NO_SAMPLES       : (별도 — process_upload 가 zip 검사 시 사용)
    """
    if not isinstance(raw, dict):
        raise UploadError("INVALID_MANIFEST", "매니페스트 root 가 매핑이 아님.")

    # ---- name ----
    name = str(raw.get("name") or "").strip()
    if not _NAME_RE.match(name):
        raise UploadError(
            "INVALID_MANIFEST",
            f"name {name!r} 형식 위반 — snake_case (^[a-z][a-z0-9_]{{2,40}}$) 필요.",
            "name 필드를 영문 소문자/숫자/언더스코어로만 구성하세요.",
        )

    if name in _RESERVED_NAMES:
        raise UploadError(
            "RESERVED_NAME",
            f"name {name!r} 은(는) 예약어 — wave-4 도구 또는 built-in MCP tool 과 충돌.",
            "다른 이름을 선택하세요 (예: my_" + name + ").",
        )

    # ---- description / script / runtime ----
    description = str(raw.get("description") or "").strip()
    if not description:
        raise UploadError("INVALID_MANIFEST", "description 누락.")

    script = str(raw.get("script") or "").strip()
    if not script:
        raise UploadError("INVALID_MANIFEST", "script 경로 누락.")

    runtime = str(raw.get("runtime") or "python").strip().lower()
    if runtime not in _VALID_RUNTIMES:
        raise UploadError(
            "INVALID_MANIFEST",
            f"runtime {runtime!r} 미지원 — {sorted(_VALID_RUNTIMES)} 중 선택.",
        )

    # ---- args ----
    args: list[UploadArg] = []
    seen_arg: set[str] = set()
    for a in raw.get("args") or []:
        if not isinstance(a, dict):
            raise UploadError("INVALID_MANIFEST", "args 항목이 매핑이 아님.")
        an = str(a.get("name") or "").strip()
        if not _ARG_NAME_RE.match(an):
            raise UploadError(
                "INVALID_MANIFEST",
                f"arg name {an!r} 형식 위반 — ^[a-z][a-z0-9_]{{0,30}}$ 필요.",
            )
        if an in seen_arg:
            raise UploadError("INVALID_MANIFEST", f"arg name {an!r} 중복.")
        seen_arg.add(an)
        atype = str(a.get("type") or "string").lower()
        if atype not in _PY_TYPE:
            raise UploadError(
                "INVALID_MANIFEST",
                f"arg {an} type {atype!r} 미지원 — string/integer/number/boolean.",
            )
        args.append(
            UploadArg(
                name=an,
                type=atype,
                required=bool(a.get("required") or False),
                default=a.get("default"),
                description=str(a.get("description") or ""),
            )
        )

    # ---- timeout / args_style / env / return ----
    timeout = int(raw.get("timeout_sec") or 60)
    timeout = max(1, min(timeout, _MAX_TIMEOUT))

    args_style = str(raw.get("args_style") or "long_flags").lower()
    if args_style not in ("long_flags", "positional"):
        raise UploadError("INVALID_MANIFEST", "args_style 는 long_flags|positional.")

    env_allow_raw = raw.get("env_allowlist") or []
    if not isinstance(env_allow_raw, list):
        raise UploadError("INVALID_MANIFEST", "env_allowlist 는 list.")
    env_allowlist = [str(e) for e in env_allow_raw if isinstance(e, str)]

    return_raw = raw.get("return") or {}
    return_format = "text"
    capture_files = CaptureFiles()
    if isinstance(return_raw, dict):
        rf = str(return_raw.get("format") or "text").lower()
        if rf in ("text", "json"):
            return_format = rf
        # capture_files — bool 또는 dict 지원
        cap_raw = return_raw.get("capture_files")
        if cap_raw is True:
            capture_files = CaptureFiles(enabled=True)
        elif isinstance(cap_raw, dict):
            capture_files = CaptureFiles(
                enabled=bool(cap_raw.get("enabled", True)),
                scan_dir=str(cap_raw.get("scan_dir") or "/work/"),
                image_extensions=[
                    str(e).lstrip(".").lower()
                    for e in (cap_raw.get("image_extensions") or CaptureFiles().image_extensions)
                ],
                text_extensions=[
                    str(e).lstrip(".").lower()
                    for e in (cap_raw.get("text_extensions") or CaptureFiles().text_extensions)
                ],
                resource_extensions=[
                    str(e).lstrip(".").lower()
                    for e in (cap_raw.get("resource_extensions") or CaptureFiles().resource_extensions)
                ],
                max_inline_mb=int(cap_raw.get("max_inline_mb") or 5),
                max_total_mb=int(cap_raw.get("max_total_mb") or 20),
            )

    # ---- persist_output ----
    po_raw = raw.get("persist_output") or {}
    po = PersistOutput()
    if isinstance(po_raw, dict) and po_raw.get("enabled"):
        dt = str(po_raw.get("data_type") or "").strip().upper()
        if dt and dt not in _VALID_DATA_TYPES:
            raise UploadError(
                "INVALID_MANIFEST",
                f"persist_output.data_type {dt!r} 미지원 — {sorted(_VALID_DATA_TYPES)} 중 선택.",
            )
        po = PersistOutput(
            enabled=True,
            data_type=dt or None,
            team=(str(po_raw.get("team") or "").strip() or None),
            group=(str(po_raw.get("group") or "").strip() or None),
            title_template=str(po_raw.get("title_template") or ""),
            summary_template=str(po_raw.get("summary_template") or ""),
            body_template=str(po_raw.get("body_template") or ""),
            tags=[str(t) for t in (po_raw.get("tags") or []) if isinstance(t, str)],
            dedup_key=str(po_raw.get("dedup_key") or ""),
        )

    # ---- llm_hints ----
    hints_raw = raw.get("llm_hints") or {}
    hints = LLMHints()
    if isinstance(hints_raw, dict):
        hints = LLMHints(
            when_to_use=str(hints_raw.get("when_to_use") or ""),
            not_for=str(hints_raw.get("not_for") or ""),
            example_calls=list(hints_raw.get("example_calls") or []),
            output_description=str(hints_raw.get("output_description") or ""),
        )

    restrict_agents = [
        str(a) for a in (raw.get("restrict_agents") or []) if isinstance(a, str)
    ]

    return UploadManifest(
        capture_files=capture_files,
        name=name,
        description=description,
        script=script,
        runtime=runtime,
        python_version=str(raw.get("python_version") or "3.12"),
        args=args,
        title=(str(raw.get("title") or "") or None),
        timeout_sec=timeout,
        args_style=args_style,
        env_allowlist=env_allowlist,
        return_format=return_format,
        restrict_agents=restrict_agents,
        persist_output=po,
        llm_hints=hints,
    )


# ---------------------------------------------------------------------------
# Placeholder 엔진 — persist_output template 치환 (§6)
# ---------------------------------------------------------------------------
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)\}")


def render_template(template: str, ctx: dict[str, Any]) -> str:
    """``{tool_name}`` / ``{args.X}`` / ``{parsed.Y}`` 치환.

    Escape:
        - ``{{`` → ``{`` , ``}}`` → ``}`` (regex 평가 후 후처리).
    실패 시:
        - 키 부재면 빈 문자열로 치환하고 audit log 에 warning.
    """
    if not template:
        return ""
    # 1) Escape sentinel — `{{` / `}}` 를 임시 토큰으로 보호.
    sentinel_open = "\x00OPEN\x00"
    sentinel_close = "\x00CLOSE\x00"
    work = template.replace("{{", sentinel_open).replace("}}", sentinel_close)

    def _lookup(key: str) -> str:
        # `args.X` / `parsed.Y` 는 2단계, 그 외는 1단계.
        if "." in key:
            head, _, tail = key.partition(".")
            scope = ctx.get(head)
            if isinstance(scope, dict):
                v = scope.get(tail)
                return "" if v is None else str(v)
            return ""
        v = ctx.get(key)
        return "" if v is None else str(v)

    def _sub(m: re.Match[str]) -> str:
        key = m.group(1)
        try:
            return _lookup(key)
        except Exception as e:  # pragma: no cover — defensive
            log.warning("render_template: lookup %s failed — %s", key, e)
            return ""

    result = _PLACEHOLDER_RE.sub(_sub, work)
    return result.replace(sentinel_open, "{").replace(sentinel_close, "}")


# ---------------------------------------------------------------------------
# Upload 파이프라인 — zip 추출 + sha + 매니페스트 검증 + (apptainer 빌드 위임)
# ---------------------------------------------------------------------------
def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """zip slip 방지 추출."""
    dest_real = dest.resolve()
    for member in zf.namelist():
        target = (dest / member).resolve()
        try:
            target.relative_to(dest_real)
        except ValueError as e:
            raise UploadError(
                "INVALID_MANIFEST",
                f"zip 내부 경로 탈출 시도: {member}",
            ) from e
    zf.extractall(dest)


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as e:
        raise UploadError(
            "INVALID_MANIFEST", "서버에 PyYAML 미설치."
        ) from e
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise UploadError("INVALID_MANIFEST", "매니페스트가 매핑이 아님.")
    return data


def _uploads_dir() -> Path:
    d = os.environ.get("AIDH_MCP_UPLOADS_DIR")
    if d:
        return Path(d)
    # default: <repo>/api_server/mcp_uploads/_uploads
    return (Path(__file__).resolve().parents[3] / "mcp_uploads" / "_uploads")


def process_upload(
    zip_path: str | Path,
    uploader: str,
    *,
    sif_dest_dir: Path | None = None,
) -> dict[str, Any]:
    """업로드 zip 처리. 동기 함수 (호출자가 thread executor 로 감싸도 됨).

    Returns:
        {
            "ok": bool,
            "name": str,
            "sha": str,
            "version": int,
            "sif_path": str,
            "smoke": {...},
            "manifest": dict,   # 정규화 후
            "build_log_tail": str,
        }
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise UploadError("INVALID_MANIFEST", f"zip 파일 없음: {zip_path}")

    if zip_path.stat().st_size > _BUNDLE_LIMIT_BYTES:
        raise UploadError(
            "BUNDLE_TOO_LARGE",
            f"zip 크기 {zip_path.stat().st_size} > 한도 {_BUNDLE_LIMIT_BYTES}.",
            "100MB 이하로 압축하거나 대형 자산은 별도 마운트하세요.",
        )

    sha = _compute_sha256(zip_path)
    base_dir = sif_dest_dir or _uploads_dir() / sha
    base_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. zip 추출 ──
    try:
        with zipfile.ZipFile(zip_path) as zf:
            _safe_extract(zf, base_dir)
    except zipfile.BadZipFile as e:
        raise UploadError("INVALID_MANIFEST", f"zip 형식 오류: {e}") from e

    # ── 2. 매니페스트 로드 ──
    manifest_file = base_dir / "manifest.yaml"
    if not manifest_file.exists():
        raise UploadError("INVALID_MANIFEST", "manifest.yaml 누락.")
    manifest = validate_manifest(_load_yaml(manifest_file.read_text(encoding="utf-8")))
    manifest.bundle_sha = sha

    # ── 3. samples 확인 (NO_SAMPLES) ──
    samples_dir = base_dir / "samples"
    sample_files = sorted(samples_dir.glob("*.json")) if samples_dir.exists() else []
    if not sample_files:
        raise UploadError(
            "NO_SAMPLES",
            "samples/ 디렉토리에 *.json 이 없음 — smoke 검증 불가.",
            "samples/ 안에 입력/예상 출력 짝을 1개 이상 포함하세요.",
        )

    # ── 4. uploader 식별 ──
    if not uploader or not uploader.strip():
        raise UploadError(
            "MISSING_UPLOADER",
            "uploader 필드 누락 — 감사 추적 불가.",
        )

    # ── 5. runtime 지원 여부 ──
    if manifest.runtime not in _SUPPORTED_RUNTIMES:
        raise NotImplementedError(
            f"runtime={manifest.runtime} 는 MVP 미지원. 현재는 {sorted(_SUPPORTED_RUNTIMES)} 만."
        )

    # ── 6. apptainer build 위임 ──
    from . import apptainer_build_svc as build_svc

    def_text = build_svc.generate_def(
        {
            "name": manifest.name,
            "runtime": manifest.runtime,
            "python_version": manifest.python_version,
            "script": manifest.script,
        }
    )
    sif_path = build_svc.build_sif(def_text, sha, base_dir)
    manifest.sif_path = sif_path

    # ── 7. samples 별 smoke 실행 ──
    smoke_results: list[dict[str, Any]] = []
    for sf in sample_files:
        try:
            sample = json.loads(sf.read_text(encoding="utf-8"))
        except Exception as e:
            raise UploadError(
                "INVALID_SAMPLE", f"sample {sf.name} JSON 파싱 실패: {e}"
            ) from e
        result = build_svc.smoke_run(sif_path, sample, manifest)
        smoke_results.append({"sample": sf.name, **result})

    smoke_ok = all(r.get("ok") for r in smoke_results)

    return {
        "ok": smoke_ok,
        "name": manifest.name,
        "sha": sha,
        "version": 1,  # process_upload 자체는 version bump 안 함 — 호출자 (DB 레이어) 가 결정.
        "sif_path": str(sif_path),
        "smoke": smoke_results,
        "manifest": _manifest_to_dict(manifest),
        "uploader": uploader,
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }


def _manifest_to_dict(m: UploadManifest) -> dict[str, Any]:
    """UploadManifest → JSONB 저장용 dict (DB 컬럼 manifest)."""
    return {
        "name": m.name,
        "description": m.description,
        "script": m.script,
        "runtime": m.runtime,
        "python_version": m.python_version,
        "args": [
            {
                "name": a.name, "type": a.type, "required": a.required,
                "default": a.default, "description": a.description,
            }
            for a in m.args
        ],
        "title": m.title,
        "timeout_sec": m.timeout_sec,
        "args_style": m.args_style,
        "env_allowlist": list(m.env_allowlist),
        "return_format": m.return_format,
        "restrict_agents": list(m.restrict_agents),
        "persist_output": {
            "enabled": m.persist_output.enabled,
            "data_type": m.persist_output.data_type,
            "team": m.persist_output.team,
            "group": m.persist_output.group,
            "title_template": m.persist_output.title_template,
            "summary_template": m.persist_output.summary_template,
            "body_template": m.persist_output.body_template,
            "tags": list(m.persist_output.tags),
            "dedup_key": m.persist_output.dedup_key,
        },
        "llm_hints": {
            "when_to_use": m.llm_hints.when_to_use,
            "not_for": m.llm_hints.not_for,
            "example_calls": list(m.llm_hints.example_calls),
            "output_description": m.llm_hints.output_description,
        },
        "capture_files": {
            "enabled": m.capture_files.enabled,
            "scan_dir": m.capture_files.scan_dir,
            "image_extensions": list(m.capture_files.image_extensions),
            "text_extensions": list(m.capture_files.text_extensions),
            "resource_extensions": list(m.capture_files.resource_extensions),
            "max_inline_mb": m.capture_files.max_inline_mb,
            "max_total_mb": m.capture_files.max_total_mb,
        },
        "sif_path": str(m.sif_path) if m.sif_path else None,
        "bundle_sha": m.bundle_sha,
    }


def _manifest_from_dict(d: dict[str, Any]) -> UploadManifest:
    """저장된 JSONB dict → UploadManifest (register 시점)."""
    args = [
        UploadArg(
            name=a["name"], type=a.get("type", "string"),
            required=bool(a.get("required") or False),
            default=a.get("default"),
            description=str(a.get("description") or ""),
        )
        for a in d.get("args", [])
    ]
    po_raw = d.get("persist_output") or {}
    po = PersistOutput(
        enabled=bool(po_raw.get("enabled") or False),
        data_type=po_raw.get("data_type"),
        team=po_raw.get("team"),
        group=po_raw.get("group"),
        title_template=str(po_raw.get("title_template") or ""),
        summary_template=str(po_raw.get("summary_template") or ""),
        body_template=str(po_raw.get("body_template") or ""),
        tags=list(po_raw.get("tags") or []),
        dedup_key=str(po_raw.get("dedup_key") or ""),
    )
    hints_raw = d.get("llm_hints") or {}
    hints = LLMHints(
        when_to_use=str(hints_raw.get("when_to_use") or ""),
        not_for=str(hints_raw.get("not_for") or ""),
        example_calls=list(hints_raw.get("example_calls") or []),
        output_description=str(hints_raw.get("output_description") or ""),
    )
    cf_raw = d.get("capture_files") or {}
    cf_def = CaptureFiles()
    cf = CaptureFiles(
        enabled=bool(cf_raw.get("enabled") or False),
        scan_dir=str(cf_raw.get("scan_dir") or cf_def.scan_dir),
        image_extensions=list(cf_raw.get("image_extensions") or cf_def.image_extensions),
        text_extensions=list(cf_raw.get("text_extensions") or cf_def.text_extensions),
        resource_extensions=list(cf_raw.get("resource_extensions") or cf_def.resource_extensions),
        max_inline_mb=int(cf_raw.get("max_inline_mb") or cf_def.max_inline_mb),
        max_total_mb=int(cf_raw.get("max_total_mb") or cf_def.max_total_mb),
    )
    return UploadManifest(
        capture_files=cf,
        name=d["name"],
        description=d.get("description", ""),
        script=d.get("script", ""),
        runtime=d.get("runtime", "python"),
        python_version=d.get("python_version", "3.12"),
        args=args,
        title=d.get("title"),
        timeout_sec=int(d.get("timeout_sec") or 60),
        args_style=d.get("args_style", "long_flags"),
        env_allowlist=list(d.get("env_allowlist") or []),
        return_format=d.get("return_format", "text"),
        restrict_agents=list(d.get("restrict_agents") or []),
        persist_output=po,
        llm_hints=hints,
        sif_path=Path(d["sif_path"]) if d.get("sif_path") else None,
        bundle_sha=d.get("bundle_sha"),
    )


# ---------------------------------------------------------------------------
# 호출 시 dispatch — FastMCP add_tool 의 handler.
# ---------------------------------------------------------------------------
async def dispatch_call(manifest: UploadManifest, args: dict[str, Any]) -> dict[str, Any]:
    """tool 호출 dispatcher. apptainer exec subprocess + persist_output.

    DRYRUN (env ``AIDH_MCP_UPLOADS_DRYRUN=1``):
        실 apptainer 호출 없이 fake stdout 반환 — 단위 테스트용.
    """
    # 필수 인자 검증
    for arg in manifest.args:
        if arg.required and args.get(arg.name) in (None, ""):
            return {
                "ok": False,
                "error": f"missing required arg: {arg.name}",
                "exit_code": -1,
            }

    dryrun = (os.environ.get("AIDH_MCP_UPLOADS_DRYRUN") or "").strip() == "1"

    if dryrun:
        fake_stdout = json.dumps({
            "_dryrun": True,
            "tool_name": manifest.name,
            "args": args,
        })
        result: dict[str, Any] = {
            "ok": True,
            "exit_code": 0,
            "stdout": fake_stdout,
            "stderr": "",
            "dryrun": True,
        }
        if manifest.return_format == "json":
            try:
                result["parsed"] = json.loads(fake_stdout)
            except Exception:
                pass
        # persist_output 처리 (dryrun 이어도 placeholder 검증 가능하도록)
        if manifest.persist_output.enabled:
            result["persist_preview"] = _render_persist_preview(
                manifest, args, result.get("parsed")
            )
        return result

    # 실 apptainer exec 경로 — apptainer_build_svc 위임 (P1.5 에서 확장).
    from . import apptainer_build_svc as build_svc
    if manifest.sif_path is None:
        return {
            "ok": False,
            "error": "manifest.sif_path 미설정 — register_all_uploads 가 정상 동작했는지 확인.",
            "exit_code": -1,
        }
    result = await build_svc.exec_in_container(manifest, args)

    # P1.5 — capture_files: workdir 산출물 자동 캡쳐.
    # exec_in_container 가 결과 dict 에 "workdir" 키로 작업 디렉토리 경로를
    # 반환하면 그걸 스캔. 미반환 시 capture 비활성 (회귀 0).
    workdir_path = result.pop("workdir", None)  # 응답에 노출하지 않음
    if manifest.capture_files.enabled and result.get("ok") and workdir_path:
        try:
            captured = _capture_output_files(Path(workdir_path), manifest.capture_files)
            if captured.get("images") or captured.get("texts") or captured.get("resources"):
                result["captured"] = captured
        except Exception as e:  # pragma: no cover — capture 실패가 tool 응답 막아선 안 됨
            result["capture_error"] = str(e)

    # workdir cleanup — capture 끝났으면 호스트 임시 디렉토리 제거
    if workdir_path:
        try:
            import shutil as _shutil
            _shutil.rmtree(workdir_path, ignore_errors=True)
        except Exception:
            pass

    # persist_output 처리 (실제 records INSERT 는 별도 helper 가 DB 세션 받아 수행)
    if manifest.persist_output.enabled and result.get("ok"):
        result["persist_preview"] = _render_persist_preview(
            manifest, args, result.get("parsed")
        )

    return result


# ---------------------------------------------------------------------------
# P1.5 — Output file capture (PNG / SVG / PDF / text → MCP Content)
# ---------------------------------------------------------------------------
_MIME_BY_EXT = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp",
    "svg": "image/svg+xml", "pdf": "application/pdf",
    "txt": "text/plain", "csv": "text/csv",
    "json": "application/json", "md": "text/markdown",
}


def _capture_output_files(
    workdir: Path,
    cf: CaptureFiles,
    *,
    attachments_dir: Path | None = None,
    record_id: str | None = None,
) -> dict[str, Any]:
    """``workdir`` 의 산출물을 카테고리별로 캡쳐.

    Returns:
        ``{images, texts, resources, attachment_urls, skipped, total_inline_b}``.

    동작:
        - 이미지/리소스 확장자 → ``max_inline_mb`` 이내면 base64 inline.
          초과 + ``attachments_dir`` 제공 시 거기 저장하고 URL 만 반환.
          초과 + 미제공 시 skipped 에 reason 표기.
        - 텍스트 확장자 → ``max_inline_mb`` 이내면 본문 그대로 inline,
          초과는 skipped.
        - 전체 누계가 ``max_total_mb`` 넘어가면 이후 파일 skip.
    """
    import base64 as _b64

    out: dict[str, Any] = {
        "images": [],
        "texts": [],
        "resources": [],
        "attachment_urls": [],
        "skipped": [],
        "total_inline_b": 0,
    }
    if not cf.enabled or not workdir.exists() or not workdir.is_dir():
        return out

    max_inline_b = int(cf.max_inline_mb) * 1024 * 1024
    max_total_b = int(cf.max_total_mb) * 1024 * 1024

    img_set = {e.lstrip(".").lower() for e in cf.image_extensions}
    txt_set = {e.lstrip(".").lower() for e in cf.text_extensions}
    rsc_set = {e.lstrip(".").lower() for e in cf.resource_extensions}

    # workdir 안만 (재귀 X — 첫 구현은 단일 디렉토리)
    files = sorted(p for p in workdir.iterdir() if p.is_file())
    for fp in files:
        ext = fp.suffix.lstrip(".").lower()
        size = fp.stat().st_size

        # 전체 누계 한도
        if out["total_inline_b"] + size > max_total_b:
            out["skipped"].append({
                "path": fp.name, "size_b": size,
                "reason": f"max_total_mb exceeded ({cf.max_total_mb}MB cap)",
            })
            continue

        mime = _MIME_BY_EXT.get(ext)
        if ext in img_set:
            if size <= max_inline_b:
                data = _b64.b64encode(fp.read_bytes()).decode("ascii")
                out["images"].append({
                    "path": fp.name, "mime": mime or "application/octet-stream",
                    "data": data, "size_b": size,
                })
                out["total_inline_b"] += size
            elif attachments_dir is not None and record_id:
                attachments_dir.mkdir(parents=True, exist_ok=True)
                dst = attachments_dir / fp.name
                dst.write_bytes(fp.read_bytes())
                out["attachment_urls"].append(f"/attachments/{record_id}/{fp.name}")
            else:
                out["skipped"].append({
                    "path": fp.name, "size_b": size,
                    "reason": f"max_inline_mb exceeded ({cf.max_inline_mb}MB), no record_id for attachment",
                })
        elif ext in rsc_set:
            if size <= max_inline_b:
                data = _b64.b64encode(fp.read_bytes()).decode("ascii")
                out["resources"].append({
                    "path": fp.name, "mime": mime or "application/octet-stream",
                    "data": data, "size_b": size,
                })
                out["total_inline_b"] += size
            elif attachments_dir is not None and record_id:
                attachments_dir.mkdir(parents=True, exist_ok=True)
                (attachments_dir / fp.name).write_bytes(fp.read_bytes())
                out["attachment_urls"].append(f"/attachments/{record_id}/{fp.name}")
            else:
                out["skipped"].append({
                    "path": fp.name, "size_b": size, "reason": "max_inline_mb exceeded",
                })
        elif ext in txt_set:
            if size <= max_inline_b:
                try:
                    content = fp.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    content = fp.read_bytes().decode("utf-8", errors="replace")
                out["texts"].append({
                    "path": fp.name, "content": content, "size_b": size,
                })
                out["total_inline_b"] += size
            else:
                out["skipped"].append({
                    "path": fp.name, "size_b": size, "reason": "text too large",
                })
        # 그 외 확장자는 무시 (sif 자체 등)

    return out


def _to_mcp_content(result: dict[str, Any]) -> Any:
    """dispatch_call result → MCP Content list 변환.

    captured 에 image 또는 resource (svg) 가 있으면 list[Content] 반환:
        [TextContent(summary JSON), ImageContent...]
    그 외엔 dict 그대로 (FastMCP 가 TextContent 로 wrap).
    """
    captured = (result or {}).get("captured") or {}
    images = captured.get("images") or []
    resources = captured.get("resources") or []
    # SVG 만 ImageContent 로 본다 (PDF 는 TextContent 안내).
    inline_imgs = list(images)
    for r in resources:
        mime = r.get("mime") or ""
        if mime.startswith("image/"):
            inline_imgs.append(r)
    if not inline_imgs:
        return result

    try:
        from mcp.types import ImageContent, TextContent
    except ImportError:  # pragma: no cover — mcp SDK 없을 일 없음
        return result

    summary = {k: v for k, v in result.items() if k != "captured"}
    summary["captured_meta"] = {
        "image_count": len(captured.get("images") or []),
        "text_count": len(captured.get("texts") or []),
        "resource_count": len(captured.get("resources") or []),
        "attachment_urls": captured.get("attachment_urls") or [],
        "skipped": captured.get("skipped") or [],
    }
    content: list[Any] = [
        TextContent(type="text", text=json.dumps(summary, ensure_ascii=False, indent=2))
    ]
    for img in inline_imgs:
        content.append(ImageContent(
            type="image",
            data=img["data"],
            mimeType=img["mime"],
        ))
    return content


def _render_persist_preview(
    manifest: UploadManifest,
    args: dict[str, Any],
    parsed: dict[str, Any] | None,
) -> dict[str, Any]:
    """persist_output 의 placeholder 를 모두 치환한 preview dict."""
    ctx: dict[str, Any] = {
        "tool_name": manifest.name,
        "tool_version": "",  # DB 레이어가 채움
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "args": dict(args),
        "parsed": dict(parsed or {}),
    }
    po = manifest.persist_output
    return {
        "data_type": po.data_type,
        "team": po.team,
        "group": po.group,
        "title": render_template(po.title_template, ctx),
        "summary": render_template(po.summary_template, ctx),
        "body": render_template(po.body_template, ctx),
        "tags": list(po.tags),
        "dedup_key": render_template(po.dedup_key, ctx) if po.dedup_key else "",
    }


# ---------------------------------------------------------------------------
# FastMCP 동적 등록 — wave-4 mcp_scripts.register_all_scripts 패턴 모방.
# ---------------------------------------------------------------------------
def _make_handler(manifest: UploadManifest):
    """매니페스트 → 합성 시그니처 async 함수 (FastMCP 가 introspect)."""
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

    async def handler(**kwargs: Any) -> Any:
        result = await dispatch_call(manifest, kwargs)
        # P1.5 — capture 가 이미지/SVG 를 포함하면 MCP Content list 반환
        # (Claude Desktop 인라인 렌더). 그 외는 dict (FastMCP 가 TextContent 로 wrap).
        return _to_mcp_content(result)

    handler.__name__ = manifest.name
    handler.__signature__ = sig  # type: ignore[attr-defined]
    handler.__annotations__ = annotations
    arg_doc = "\n".join(
        f"  - {a.name} ({a.type}{'*' if a.required else ''}): {a.description}"
        for a in manifest.args
    )
    handler.__doc__ = manifest.description + (
        f"\n\nargs:\n{arg_doc}" if arg_doc else ""
    )
    return handler


def register_all_uploads(mcp: Any) -> list[str]:
    """DB ``mcp_uploads`` 에서 등록된 도구를 FastMCP 에 add_tool.

    env ``AIDH_MCP_UPLOADS=off`` → 즉시 빈 리스트.

    동작:
        - 비동기 DB 조회를 동기 부팅 컨텍스트에서 호출하기 위해
          ``asyncio.run`` 또는 이미 실행 중 loop 가 있으면 thread executor.
        - 에러는 모두 silent skip (서버 부팅을 막지 않음 — 부팅 후 manual reload 가능).
    """
    if (os.environ.get("AIDH_MCP_UPLOADS") or "").lower() == "off":
        log.info("mcp_uploads: disabled by env")
        return []

    try:
        rows = _load_uploads_sync()
    except Exception as e:
        log.warning("mcp_uploads: DB 로드 실패 — %s (서버는 계속)", e)
        return []

    registered: list[str] = []
    for row in rows:
        try:
            manifest = _manifest_from_dict(row["manifest"])
            handler = _make_handler(manifest)
            mcp.add_tool(
                handler,
                name=manifest.name,
                title=manifest.title or manifest.name,
                description=manifest.description,
            )
            registered.append(manifest.name)
            log.info(
                "mcp_uploads: registered %s (sha=%s version=%d)",
                manifest.name,
                row.get("current_sha", "")[:12],
                row.get("current_version", 0),
            )
        except Exception as e:
            log.warning(
                "mcp_uploads: register skip %s — %s", row.get("name", "?"), e
            )
    return registered


def _load_uploads_sync() -> list[dict[str, Any]]:
    """DB 에서 mcp_uploads 행을 동기적으로 읽음 (부팅 시 1회).

    부팅 컨텍스트 = uvicorn import time → 아직 이벤트 루프 없음 — asyncio.run OK.
    이미 루프 있는 컨텍스트면 thread 로 격리.
    """
    async def _fetch() -> list[dict[str, Any]]:
        from sqlalchemy import select
        from ..db.base import SessionLocal
        from ..db.models import MCPUpload
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(MCPUpload).where(MCPUpload.deprecated_at.is_(None))
                )
            ).scalars().all()
            return [
                {
                    "name": r.name,
                    "current_sha": r.current_sha,
                    "current_version": r.current_version,
                    "manifest": dict(r.manifest or {}),
                    "capabilities": dict(r.capabilities or {}),
                }
                for r in rows
            ]

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        return asyncio.run(_fetch())
    # running loop → thread executor 격리
    import concurrent.futures as _f
    with _f.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(lambda: asyncio.run(_fetch()))
        return fut.result(timeout=10)


__all__ = [
    "UploadArg",
    "UploadError",
    "UploadManifest",
    "PersistOutput",
    "LLMHints",
    "dispatch_call",
    "process_upload",
    "register_all_uploads",
    "render_template",
    "validate_manifest",
]
