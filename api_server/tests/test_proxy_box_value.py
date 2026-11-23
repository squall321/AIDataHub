# 사내 프록시는 박스 .env 에서만 받는다 — 공개 리포에 주소를 박지 않고, 없으면 직결하며 그렇다고 알리는지(2026-11-24 결정)
"""왜 — 예전엔 _common.sh(DEFAULT_FALLBACK_PROXY) · bootstrap.sh · .env.example 에 사내 프록시 주소가 하드코딩돼 있었다.
이 리포는 공개라 주소를 걷어 내고, 사내망 박스는 deploy/apptainer/.env(gitignore)의 BUILD_PROXY_HTTPS 로 준다.

스크립트는 임시 폴더의 사본으로 돌린다 — 실 .env · apptainer 에 닿지 않는다. 주소는 문서용 예약 대역(TEST-NET)만 쓴다.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMMON = ROOT / "deploy" / "apptainer" / "_common.sh"
BOOT = ROOT / "bootstrap.sh"
PROXY = "http://198.51.100.20:8080"

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="bash 가 있어야 돈다")


def _env(tmp_path: Path) -> dict:
    # 시험을 돌린 셸의 프록시 변수가 섞이지 않게 깨끗한 환경으로 — apptainer 해석은 /bin/true 로 비킨다
    return {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "AIDH_APPTAINER_BIN": "/bin/true"}


def _export_proxy(tmp_path: Path, env_text: str | None) -> tuple[str, dict]:
    """_common.sh 사본의 load_env → export_proxy 를 돌려 (화면 출력, 내보낸 프록시 변수)를 돌려준다."""
    appt = tmp_path / "deploy" / "apptainer"
    appt.mkdir(parents=True)
    shutil.copy(COMMON, appt / "_common.sh")
    if env_text is None:   # load_env 가 .env.example 을 복사해 쓰는 첫 기동
        shutil.copy(ROOT / "deploy" / "apptainer" / ".env.example", appt / ".env.example")
    else:
        (appt / ".env").write_text(env_text, encoding="utf-8")
    script = (f'. "{appt}/_common.sh"\nload_env\nexport_proxy\n'
              'echo "@@HTTPS=${HTTPS_PROXY-<unset>}"\necho "@@HTTP=${HTTP_PROXY-<unset>}"\n')
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60, env=_env(tmp_path))
    assert r.returncode == 0, r.stderr
    got = dict(re.findall(r"^@@(\w+)=(.*)$", r.stdout, re.M))
    return r.stdout, got


_DIRECT = "[INFO] HTTPS_PROXY 미설정 — 프록시 없이 직결로 진행"


def test_박스_env_에_프록시가_없으면_직결하고_알린다(tmp_path):
    out, got = _export_proxy(tmp_path, "APP_NAME=aidh\n")
    assert got == {"HTTPS": "<unset>", "HTTP": "<unset>"}, "하드코딩 폴백이 다시 생기면 여기에 주소가 실린다"
    assert _DIRECT in out


def test_예시_파일을_복사한_첫_기동도_직결이다(tmp_path):
    """load_env 는 .env 가 없으면 .env.example 을 복사한다 — 그 파일에 자리표시 주소가 살아 있으면 깨진 프록시로 받는다."""
    out, got = _export_proxy(tmp_path, None)
    # 예시 파일은 HTTPS_PROXY= 를 빈 값으로 둔다 — 빈 값도 프록시 없음이다
    assert got["HTTPS"] in ("", "<unset>") and got["HTTP"] in ("", "<unset>") and _DIRECT in out


def test_박스_env_의_BUILD_PROXY_HTTPS_를_쓴다(tmp_path):
    out, got = _export_proxy(tmp_path, f"BUILD_PROXY_HTTPS={PROXY}\nBUILD_PROXY_HTTP={PROXY}\n")
    assert got == {"HTTPS": PROXY, "HTTP": PROXY}
    assert _DIRECT not in out


def test_off_면_알림_없이_직결이다(tmp_path):
    out, got = _export_proxy(tmp_path, "BUILD_PROXY_HTTPS=off\n")
    assert got["HTTPS"] == "<unset>" and "[INFO] HTTPS_PROXY" not in out


# ── bootstrap.sh — root 로 도는 호스트 준비 스크립트의 다운로드 재시도 프록시 ─────────────────────────────
def _boot_block() -> str:
    """fallback 값을 정하는 줄부터 curl_with_proxy_fallback 함수 끝까지(원문 그대로)."""
    src = BOOT.read_text(encoding="utf-8")
    i = src.index("# 사내 프록시 fallback")
    j = src.index("\n}\n", src.index("curl_with_proxy_fallback() {", i)) + 3
    return src[i:j]


def _boot(tmp_path: Path, env_text: str | None, **extra) -> tuple[str, str]:
    """(FALLBACK_PROXY, 첫 curl 이 실패했을 때의 화면 출력)."""
    if env_text is not None:
        (tmp_path / "deploy" / "apptainer").mkdir(parents=True, exist_ok=True)
        (tmp_path / "deploy" / "apptainer" / ".env").write_text(env_text, encoding="utf-8")
    script = ("set -euo pipefail\n"
              f'REPO_ROOT="{tmp_path}"\n'
              'warn() { echo "WARN $*"; }; ok() { echo "OK $*"; }; note() { :; }\n'
              "curl() { return 1; }\n"
              f"{_boot_block()}\n"
              'echo "@@FB=$FALLBACK_PROXY"\n'
              f'curl_with_proxy_fallback "{tmp_path}/out" https://example.invalid/x || true\n')
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                       env={**_env(tmp_path), **extra}, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    return re.search(r"^@@FB=(.*)$", r.stdout, re.M).group(1), r.stdout


def test_bootstrap_은_박스_env_가_없으면_재시도_없이_그렇다고_알린다(tmp_path):
    fb, out = _boot(tmp_path, None)
    assert fb == ""
    assert "fallback 프록시 미설정" in out and "재시도 없음" in out


@pytest.mark.parametrize("line", [f"BUILD_PROXY_HTTPS={PROXY}", f'BUILD_PROXY_HTTPS="{PROXY}"   # 사내',
                                  f"export BUILD_PROXY_HTTPS='{PROXY}'"])
def test_bootstrap_은_박스_env_의_BUILD_PROXY_HTTPS_로_재시도한다(tmp_path, line):
    fb, out = _boot(tmp_path, f"APP_NAME=aidh\n{line}\n")
    assert fb == PROXY, "따옴표 · 뒤 주석을 뗀 값"
    assert f"fallback 프록시로 재시도 {PROXY}" in out


@pytest.mark.parametrize("env_text", ["BUILD_PROXY_HTTPS=off\n", f"# BUILD_PROXY_HTTPS={PROXY}\n"])
def test_bootstrap_은_off_나_주석은_값으로_보지_않는다(tmp_path, env_text):
    assert _boot(tmp_path, env_text)[0] == ""


def test_bootstrap_은_환경변수가_박스_env_를_이긴다(tmp_path):
    fb, _ = _boot(tmp_path, f"BUILD_PROXY_HTTPS={PROXY}\n", AIDH_FALLBACK_PROXY="http://198.51.100.30:3128")
    assert fb == "http://198.51.100.30:3128"


def test_bootstrap_은_박스_env_를_실행하지_않는다(tmp_path):
    """root 로 돈다 — .env 를 source 하면 그 파일의 명령이 root 로 돈다. 글자로만 읽는다."""
    _boot(tmp_path, f'BUILD_PROXY_HTTPS={PROXY}\ntouch "{tmp_path}/ran"\n')
    assert not (tmp_path / "ran").exists()


def test_스크립트에_루프백_말고_IPv4_주소가_없다():
    """공개 리포다 — 사내 주소는 박스 .env 에서 받는다(실패 글에 값을 싣지 않는다)."""
    for p in (COMMON, BOOT, ROOT / "deploy" / "systemd" / "aidh.service.template"):
        found = bool(set(re.findall(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])", p.read_text(encoding="utf-8")))
                     - {"127.0.0.1", "0.0.0.0"})
        assert not found, f"{p.name} 에 IPv4 주소 글자가 있다 — 사내 주소면 박스 .env 로 옮긴다"
