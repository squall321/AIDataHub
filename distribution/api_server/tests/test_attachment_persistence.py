"""S5. Attachment binary persistence test.

``/api/convert/ingest`` 가 변환 산출물의 ``{record_id}/`` 디렉터리를
``settings.attachments_dir`` 로 복사하는지 검증.

실제 파일 유무와 무관하게, 빈 출력 폴더라도 동작이 깨지지 않아야 한다 — 따라서
주된 검증은 응답 페이로드의 ``attachments_persisted`` 키 존재와 0 이상의 정수.
"""
from __future__ import annotations

import io
import textwrap
from pathlib import Path

import pytest


def _make_md_bytes() -> bytes:
    return textwrap.dedent(
        """\
        # 첨부 영속화 테스트

        본문.
        """
    ).encode("utf-8")


@pytest.mark.asyncio
async def test_persist_attachments_default_true(db_client, tmp_path, monkeypatch) -> None:
    from api.config import settings

    monkeypatch.setattr(settings, "attachments_dir", tmp_path / "attachments", raising=False)

    files = {"file": ("att.md", _make_md_bytes(), "text/markdown")}
    form = {"team": "HE", "group": "CAE", "year": "2026", "seq": "0"}
    resp = await db_client.post("/api/convert/ingest", files=files, data=form)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # md 변환은 첨부가 없을 수 있으나 키가 존재해야 한다.
    assert "attachments_persisted" in body
    assert isinstance(body["attachments_persisted"], int)
    assert body["attachments_persisted"] >= 0


@pytest.mark.asyncio
async def test_persist_attachments_disabled(db_client, tmp_path, monkeypatch) -> None:
    from api.config import settings

    monkeypatch.setattr(settings, "attachments_dir", tmp_path / "attachments", raising=False)

    files = {"file": ("att.md", _make_md_bytes(), "text/markdown")}
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "0",
        "persist_attachments": "false",
    }
    resp = await db_client.post("/api/convert/ingest", files=files, data=form)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["attachments_persisted"] == 0
