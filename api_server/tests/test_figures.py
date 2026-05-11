"""``/figures`` 정적 마운트 + ingest 그림 복사 테스트.

- mount 가 등록되어 있는지 (route 존재)
- 임시 figures_dir 에 파일을 두면 GET /figures/... 로 받을 수 있는지
- ingest.loader.copy_figures 가 동봉된 figures 폴더를 복사하는지
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_figures_mount_registered() -> None:
    """``/figures`` 마운트가 앱에 등록돼 있어야 한다."""
    try:
        from api.main import app
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"FastAPI app not importable: {exc}")

    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/figures" in paths, (
        f"/figures mount not found. routes={sorted(paths)}"
    )


@pytest.mark.asyncio
async def test_figure_file_served(tmp_path: Path, monkeypatch) -> None:
    """임시 figures_dir 의 PNG 가 정적으로 서빙되는지."""
    try:
        from fastapi import FastAPI
        from fastapi.staticfiles import StaticFiles
        from httpx import ASGITransport, AsyncClient
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"deps not available: {exc}")

    # 단독 마운트로 검증 (글로벌 settings 변경 없이도 정적 마운트 동작 확인)
    figures_dir = tmp_path / "figures"
    doc_dir = figures_dir / "DOC-HE-CAE-2026-0000000001"
    doc_dir.mkdir(parents=True)
    # 최소 PNG 시그니처 (8바이트) — content-type 추론에 충분
    png_bytes = b"\x89PNG\r\n\x1a\n"
    sample = doc_dir / "F001.png"
    sample.write_bytes(png_bytes)

    app = FastAPI()
    app.mount(
        "/figures",
        StaticFiles(directory=str(figures_dir)),
        name="figures",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/figures/DOC-HE-CAE-2026-0000000001/F001.png"
        )
    assert resp.status_code == 200
    ctype = resp.headers.get("content-type", "")
    assert "image/png" in ctype or "image" in ctype, (
        f"unexpected content-type: {ctype!r}"
    )
    assert resp.content == png_bytes


def test_ingest_copies_figures(tmp_path: Path) -> None:
    """copy_figures 가 ``{src}/{doc_id}/F001.png`` 를
    ``{figures_dir}/{doc_id}/F001.png`` 로 복사한다."""
    try:
        from api.ingest.loader import copy_figures
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"ingest module not importable: {exc}")

    doc_id = "DOC-HE-CAE-2026-0000000001"

    # 입력: src_root/{doc_id}/F001.png + 형식 보존용 JSON 동봉
    src_root = tmp_path / "src"
    (src_root / doc_id).mkdir(parents=True)
    payload = b"\x89PNG\r\n\x1a\n--FAKE-PNG--"
    (src_root / doc_id / "F001.png").write_bytes(payload)
    # 동봉 JSON (실제 ingest 시 사용되는 형태) — copy_figures 자체는 JSON 무관
    (src_root / f"{doc_id}.json").write_text(
        json.dumps({"id": doc_id, "data_type": "DOC"}), encoding="utf-8"
    )

    figures_dir = tmp_path / "figures_out"

    n = copy_figures(doc_id, source_root=src_root, figures_dir=figures_dir)
    assert n >= 1, f"expected ≥1 file copied, got {n}"

    dst = figures_dir / doc_id / "F001.png"
    assert dst.is_file(), f"copied file missing: {dst}"
    assert dst.read_bytes() == payload

    # 멱등 — 한 번 더 복사해도 깨지지 않음
    n2 = copy_figures(doc_id, source_root=src_root, figures_dir=figures_dir)
    assert n2 == n
    assert dst.read_bytes() == payload


def test_copy_figures_missing_source_returns_zero(tmp_path: Path) -> None:
    """source 가 없으면 조용히 0 을 반환한다."""
    try:
        from api.ingest.loader import copy_figures
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"ingest module not importable: {exc}")

    n = copy_figures(
        "DOC-HE-CAE-2026-0000999999",
        source_root=tmp_path / "no_such",
        figures_dir=tmp_path / "out",
    )
    assert n == 0
