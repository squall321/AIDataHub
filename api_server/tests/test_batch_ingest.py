"""S2. Batch ingest CLI tests."""
from __future__ import annotations

import io
import textwrap
from pathlib import Path

import pytest


def _make_md(text: str) -> bytes:
    return text.encode("utf-8")


def _make_xlsx_bytes() -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "데이터"
    ws["A1"] = "x"
    ws["B1"] = "y"
    ws["A2"] = 1
    ws["B2"] = 2
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _seed_dir(tmp_path: Path) -> Path:
    base = tmp_path / "samples"
    base.mkdir(parents=True, exist_ok=True)
    (base / "a.md").write_bytes(_make_md("# A\nbody-A\n"))
    (base / "b.md").write_bytes(_make_md("# B\nbody-B\n"))
    sub = base / "sub"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "c.md").write_bytes(_make_md("# C\nbody-C\n"))
    (base / "data.xlsx").write_bytes(_make_xlsx_bytes())
    (base / "ignored.txt").write_text("skip me", encoding="utf-8")
    return base


def test_discover_files(tmp_path) -> None:
    from api.ingest.batch import SUPPORTED_EXTS, discover_files

    base = _seed_dir(tmp_path)
    files = discover_files(base)
    names = sorted(p.name for p in files)
    assert "a.md" in names
    assert "b.md" in names
    assert "c.md" in names
    assert "data.xlsx" in names
    assert "ignored.txt" not in names
    for p in files:
        assert p.suffix.lower() in SUPPORTED_EXTS


def test_dry_run_main(tmp_path, capsys) -> None:
    from api.ingest.batch import main

    base = _seed_dir(tmp_path)
    rc = main([str(base), "--dry-run", "--workers", "2"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ok=" in out
    assert "Batch ingest" in out


@pytest.mark.asyncio
async def test_run_batch_dry_run(tmp_path) -> None:
    from api.ingest.batch import discover_files, run_batch

    base = _seed_dir(tmp_path)
    files = discover_files(base)
    results = await run_batch(
        files,
        workers=2,
        division="HE",
        team="CAE",
        year=2026,
        start_seq=1,
        dry_run=True,
        persist_attachments=False,
        output_root=tmp_path / "out",
    )
    assert len(results) == len(files)
    # 모두 ok 또는 failed (failed 는 dry-run 에서 충분한 메타가 없을 때).
    for r in results:
        assert r.status in ("ok", "failed", "skipped")
