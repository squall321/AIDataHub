"""``/api/convert`` 라우트 테스트.

httpx ASGITransport + 메모리 SQLite 세션을 사용한다.
"""
from __future__ import annotations

import io
import os
import textwrap
from pathlib import Path

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# 입력 파일 생성 헬퍼
# ---------------------------------------------------------------------------
def _make_docx_bytes() -> bytes:
    """제목 + 한 단락이 들어있는 작은 .docx 를 메모리에 생성."""
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    # Word Heading 스타일 적용
    h = doc.add_paragraph("개요", style="Heading 1")
    assert h is not None
    doc.add_paragraph("이 문서는 변환 라우트 검증용 더미 본문입니다.")
    doc.add_paragraph("두 번째 단락.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_xlsx_bytes() -> bytes:
    """3행짜리 단일 시트 .xlsx 를 메모리에 생성."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "측정"
    ws["A1"] = "시간"
    ws["B1"] = "하중"
    ws["A2"] = 0.0
    ws["B2"] = 0.0
    ws["A3"] = 0.1
    ws["B3"] = 12.5
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_md_bytes() -> bytes:
    text = textwrap.dedent(
        """\
        # 변환 테스트

        본문 한 줄.

        ## 하위 섹션

        하위 본문.
        """
    )
    return text.encode("utf-8")


# ---------------------------------------------------------------------------
# 라우트 등록 검증
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_convert_routes_registered(test_client) -> None:
    # OpenAPI 에서 /api/convert/ 와 /api/convert/ingest 둘 다 보여야 한다.
    resp = await test_client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    paths = set(data.get("paths", {}).keys())
    assert any(p.startswith("/api/convert") for p in paths)
    assert "/api/convert/ingest" in paths


# ---------------------------------------------------------------------------
# 확장자/크기 검증
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_convert_unsupported_extension(test_client) -> None:
    files = {"file": ("data.bin", b"\x00\x01\x02\x03", "application/octet-stream")}
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "1",
    }
    resp = await test_client.post("/api/convert/", files=files, data=form)
    assert resp.status_code == 415
    body = resp.json()
    assert body["error"]["code"] == "UNSUPPORTED_FORMAT"


@pytest.mark.asyncio
async def test_convert_oversized_file(test_client, monkeypatch) -> None:
    """``max_upload_mb`` 를 1MB 로 줄이고 2MB 더미를 보내 413 응답을 확인."""
    from api.config import settings

    monkeypatch.setattr(settings, "max_upload_mb", 1, raising=False)

    big = b"\x00" * (2 * 1024 * 1024)
    files = {
        "file": (
            "huge.md",
            big,
            "text/markdown",
        )
    }
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "1",
    }
    resp = await test_client.post("/api/convert/", files=files, data=form)
    assert resp.status_code == 413
    body = resp.json()
    assert body["error"]["code"] == "PAYLOAD_TOO_LARGE"


# ---------------------------------------------------------------------------
# 변환만
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_convert_docx(test_client) -> None:
    pytest.importorskip("docx")
    files = {
        "file": (
            "demo.docx",
            _make_docx_bytes(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    }
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "1",
        "tags": "demo,convert",
        "agents": "iga-analyst",
    }
    resp = await test_client.post("/api/convert/", files=files, data=form)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("data_type") == "DOC"
    assert "sections" in body
    assert isinstance(body["sections"], list)
    # tags / agents 폼이 머지되었는지.
    assert "demo" in body.get("tags", [])
    assert "iga-analyst" in body.get("agents", [])


@pytest.mark.asyncio
async def test_convert_xlsx(test_client) -> None:
    pytest.importorskip("openpyxl")
    files = {
        "file": (
            "demo.xlsx",
            _make_xlsx_bytes(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "1",
    }
    resp = await test_client.post("/api/convert/", files=files, data=form)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 단일 시트 → headers / rows 가 그대로 노출됨.
    assert body.get("data_type") in ("DATA", "DATA_BUNDLE")
    if body.get("data_type") == "DATA":
        assert "headers" in body
        assert body["headers"][0:2] == ["시간", "하중"]
        assert body.get("row_count", 0) >= 2


@pytest.mark.asyncio
async def test_convert_md(test_client) -> None:
    files = {
        "file": (
            "demo.md",
            _make_md_bytes(),
            "text/markdown",
        )
    }
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "1",
    }
    resp = await test_client.post("/api/convert/", files=files, data=form)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("data_type") == "DOC"
    sections = body.get("sections") or []
    assert sections, "Markdown 변환 결과에 섹션이 비어있다"
    assert sections[0]["title"] == "변환 테스트"


# ---------------------------------------------------------------------------
# 변환 + 적재
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_convert_then_ingest_inserts_record(db_client) -> None:
    files = {
        "file": (
            "ingest_demo.md",
            _make_md_bytes(),
            "text/markdown",
        )
    }
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "100",
        "tags": "ingest,demo",
        "agents": "iga-analyst",
    }
    resp = await db_client.post("/api/convert/ingest", files=files, data=form)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "inserted"
    assert body["record_id"].startswith("DOC-HE-CAE-2026-")
    rec = body["record"]
    assert rec["data_type"] == "DOC"
    assert "ingest" in rec["tags"]
    assert "iga-analyst" in rec["agents"]


@pytest.mark.asyncio
async def test_convert_idempotency_via_ingest(db_client) -> None:
    """동일 파일을 두 번 업로드해도 중복 행이 생기지 않는다.

    첫 번째: ``inserted``. 두 번째: ``skipped`` (content_hash 동일).
    """
    payload_bytes = _make_md_bytes()
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "200",
    }

    files1 = {"file": ("dup.md", payload_bytes, "text/markdown")}
    r1 = await db_client.post("/api/convert/ingest", files=files1, data=form)
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["status"] == "inserted"
    rid = body1["record_id"]

    files2 = {"file": ("dup.md", payload_bytes, "text/markdown")}
    r2 = await db_client.post("/api/convert/ingest", files=files2, data=form)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["record_id"] == rid
    assert body2["status"] == "skipped"


# ---------------------------------------------------------------------------
# 확장 메타 폼 필드 (extension_integration_plan §4)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ingest_with_full_metadata_overrides(
    db_client, test_session_maker
) -> None:
    """status / language / subject_keywords / derivation / quality_score /
    valid_from / valid_until / title_override / summary_override 가
    DB record 에 정확히 반영되는지."""
    from api.db.models import Record
    from sqlalchemy import select

    files = {"file": ("meta_full.md", _make_md_bytes(), "text/markdown")}
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "300",
        "tags": "iga,offset",
        "agents": "iga-analyst",
        "classification": "internal",
        "domain": "battery",
        "status": "review",
        "language": "en",
        "subject_keywords": "shell,offset",
        "derivation": "translated",
        "quality_score": "70",
        "valid_from": "2026-05-08",
        "valid_until": "2027-05-08",
        "title_override": "Custom Title",
        "summary_override": "Custom summary text.",
    }
    resp = await db_client.post("/api/convert/ingest", files=files, data=form)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "inserted"
    rid = body["record_id"]

    # DB 검증
    async with test_session_maker() as s:
        rec = (
            await s.execute(select(Record).where(Record.id == rid))
        ).scalar_one()
        assert rec.status == "review"
        assert rec.language == "en"
        assert "shell" in (rec.subject_keywords or [])
        assert "offset" in (rec.subject_keywords or [])
        assert rec.derivation == "translated"
        assert rec.quality_score == 70
        assert rec.valid_from is not None and str(rec.valid_from) == "2026-05-08"
        assert rec.valid_until is not None and str(rec.valid_until) == "2027-05-08"
        assert rec.title == "Custom Title"
        assert rec.summary == "Custom summary text."


@pytest.mark.asyncio
async def test_ingest_with_invalid_status_returns_422(db_client) -> None:
    files = {"file": ("bad_status.md", _make_md_bytes(), "text/markdown")}
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "301",
        "status": "totally-bogus",
    }
    resp = await db_client.post("/api/convert/ingest", files=files, data=form)
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_ingest_with_invalid_date_returns_422(db_client) -> None:
    files = {"file": ("bad_date.md", _make_md_bytes(), "text/markdown")}
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "302",
        "valid_from": "2026/05/08",  # ISO 형식 아님
    }
    resp = await db_client.post("/api/convert/ingest", files=files, data=form)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_ingest_with_quality_score_out_of_range_returns_422(
    db_client,
) -> None:
    files = {"file": ("bad_q.md", _make_md_bytes(), "text/markdown")}
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "303",
        "quality_score": "150",
    }
    resp = await db_client.post("/api/convert/ingest", files=files, data=form)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_ingest_with_partial_overrides(
    db_client, test_session_maker
) -> None:
    """부분적인 override — 비어있는 필드는 normalizer 결과/기본값 유지."""
    from api.db.models import Record
    from sqlalchemy import select

    files = {"file": ("partial.md", _make_md_bytes(), "text/markdown")}
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "304",
        # status 만 지정. language/derivation 등은 기본값.
        "status": "approved",
    }
    resp = await db_client.post("/api/convert/ingest", files=files, data=form)
    assert resp.status_code == 200, resp.text
    rid = resp.json()["record_id"]

    async with test_session_maker() as s:
        rec = (
            await s.execute(select(Record).where(Record.id == rid))
        ).scalar_one()
        assert rec.status == "approved"
        # 기본값 유지 — language 는 "ko".
        assert rec.language == "ko"
        # derivation 기본값 "original".
        assert rec.derivation == "original"
        # subject_keywords 미지정 → 빈 리스트.
        assert list(rec.subject_keywords or []) == []
        # quality_score 미지정 → None.
        assert rec.quality_score is None


@pytest.mark.asyncio
async def test_ingest_title_override_replaces_extracted_title(
    db_client, test_session_maker
) -> None:
    """``title_override`` 가 비어있지 않으면 변환기 추출 title 을 덮어쓴다."""
    from api.db.models import Record
    from sqlalchemy import select

    files = {"file": ("over_title.md", _make_md_bytes(), "text/markdown")}
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "305",
        "title_override": "Manually Curated Title",
    }
    resp = await db_client.post("/api/convert/ingest", files=files, data=form)
    assert resp.status_code == 200, resp.text
    rid = resp.json()["record_id"]

    async with test_session_maker() as s:
        rec = (
            await s.execute(select(Record).where(Record.id == rid))
        ).scalar_one()
        # 변환기는 보통 첫 H1("변환 테스트") 을 title 로 추출하지만,
        # title_override 가 우선해야 한다.
        assert rec.title == "Manually Curated Title"


@pytest.mark.asyncio
async def test_ingest_invalid_valid_range_returns_422(db_client) -> None:
    """``valid_from > valid_until`` → 422."""
    files = {"file": ("range.md", _make_md_bytes(), "text/markdown")}
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "306",
        "valid_from": "2027-05-08",
        "valid_until": "2026-05-08",
    }
    resp = await db_client.post("/api/convert/ingest", files=files, data=form)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
