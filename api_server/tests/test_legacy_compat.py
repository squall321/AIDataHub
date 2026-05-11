"""Legacy 변환기(.docx → JSON 단일 파이프라인) 호환성 회귀 테스트.

기존 `python -m converter <docx> --team ...` 흐름이
DB/스키마 도입 이후에도 깨지지 않는지를 검증한다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TEST_DOCX = Path(r"d:\tmp\iga_guide_test.docx")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
PYTHON = sys.executable


def _run_converter(out_dir: Path) -> subprocess.CompletedProcess[str]:
    cmd = [
        PYTHON,
        "-m",
        "converter",
        str(TEST_DOCX),
        "--team",
        "HE",
        "--group",
        "CAE",
        "--year",
        "2026",
        "--seq",
        "1",
        "--output-dir",
        str(out_dir),
    ]
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_DIR) + (os.pathsep + existing if existing else "")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        cmd, capture_output=True, text=True, env=env, encoding="utf-8", errors="replace"
    )


def test_converter_module_importable() -> None:
    """converter 패키지가 변경 없이 import 되는지."""
    try:
        import converter  # noqa: F401
        from converter import core  # noqa: F401
        from converter.core import Converter, ConverterOptions  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        pytest.fail(f"converter 패키지 import 실패: {exc}")


def test_converter_cli_runs(tmp_path: Path) -> None:
    """CLI 실행 → JSON 산출까지 정상 동작."""
    if not TEST_DOCX.exists():
        pytest.skip(f"테스트용 docx 파일 없음: {TEST_DOCX}")

    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = _run_converter(out_dir)
    assert proc.returncode == 0, (
        f"converter CLI 실패: stdout={proc.stdout[-200:]} stderr={proc.stderr[-400:]}"
    )

    json_files = list(out_dir.rglob("*.json"))
    assert json_files, "JSON 출력 파일이 생성되지 않았습니다."

    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    # 기존 스키마(json_schema_rules.md v1.0) 핵심 필드
    assert "schema_version" in payload
    assert "meta" in payload
    assert "sections" in payload
    assert "figures" in payload
    assert "tables" in payload


def test_converter_meta_doc_id_format(tmp_path: Path) -> None:
    """doc_id 가 'HE-CAE-2026-0000000001' 형식을 유지하는지."""
    if not TEST_DOCX.exists():
        pytest.skip(f"테스트용 docx 파일 없음: {TEST_DOCX}")

    out_dir = tmp_path / "out2"
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = _run_converter(out_dir)
    if proc.returncode != 0:
        pytest.skip(f"converter 실패: {proc.stderr[-200:]}")

    json_files = list(out_dir.rglob("*.json"))
    assert json_files
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    doc_id = payload.get("meta", {}).get("doc_id", "")
    parts = doc_id.split("-")
    # HE-CAE-2026-0000000001 → 4 parts (10-digit seq, v3)
    assert len(parts) == 4, f"예상 형식 'TEAM-GROUP-YEAR-SEQ' 와 다름: {doc_id!r}"
    assert parts[0] == "HE"
    assert parts[1] == "CAE"
    assert parts[2] == "2026"
    assert parts[3].isdigit() and len(parts[3]) == 10


def test_legacy_models_still_match() -> None:
    """converter.models 의 핵심 dataclass 들이 그대로 살아있는지."""
    from converter.models import (
        Block,
        ConversionResult,
        Figure,
        Section,
        Source,
        Table,
    )

    # Block 직렬화
    b = Block(type="paragraph", text="hello")
    d = b.to_dict()
    assert d["type"] == "paragraph"
    assert d["text"] == "hello"

    # Section 직렬화
    s = Section(id="1", level=1, title="개요")
    sd = s.to_dict()
    assert sd["id"] == "1"
    assert sd["blocks"] == []
    assert sd["children"] == []

    # 나머지는 import 만 체크
    assert Figure and Table and Source and ConversionResult
