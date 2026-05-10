"""``/api/convert/*`` 에러 envelope 통일 검증.

extension_integration_plan.md §5 — 모든 에러 경로가
``{"error": {"code", "message", "details", "request_id"}}`` 형태로 응답해야 한다.
"""
from __future__ import annotations

import pytest


def _assert_envelope(body: dict, expected_code: str) -> None:
    """공통 envelope shape 검증 헬퍼."""
    assert "error" in body, f"missing 'error' wrapper: {body}"
    err = body["error"]
    assert isinstance(err, dict), f"error must be a dict, got {type(err).__name__}"
    assert err.get("code") == expected_code, (
        f"expected code={expected_code}, got {err.get('code')!r}"
    )
    assert isinstance(err.get("message"), str), "error.message must be str"
    # details 키는 항상 존재해야 함 (빈 dict 라도)
    assert "details" in err, "error.details key must always be present"
    # request_id 는 None 일 수 있지만 키는 존재.
    assert "request_id" in err, "error.request_id key must always be present"


@pytest.mark.asyncio
async def test_convert_unsupported_extension_envelope(test_client) -> None:
    """확장자 미지원 → 415 + UNSUPPORTED_FORMAT envelope."""
    files = {"file": ("data.bin", b"\x00\x01\x02\x03", "application/octet-stream")}
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "1",
    }
    resp = await test_client.post("/api/convert/", files=files, data=form)
    assert resp.status_code == 415
    _assert_envelope(resp.json(), "UNSUPPORTED_FORMAT")


@pytest.mark.asyncio
async def test_convert_oversized_envelope(test_client, monkeypatch) -> None:
    """업로드 크기 초과 → 413 + PAYLOAD_TOO_LARGE envelope."""
    from api.config import settings

    monkeypatch.setattr(settings, "max_upload_mb", 1, raising=False)

    big = b"\x00" * (2 * 1024 * 1024)
    files = {"file": ("huge.md", big, "text/markdown")}
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "1",
    }
    resp = await test_client.post("/api/convert/", files=files, data=form)
    assert resp.status_code == 413
    body = resp.json()
    _assert_envelope(body, "PAYLOAD_TOO_LARGE")
    # details 에 max_bytes/received_bytes 가 들어있는지 확인 (라우터 보강 정보)
    details = body["error"]["details"]
    assert details.get("max_bytes") == 1 * 1024 * 1024


@pytest.mark.asyncio
async def test_convert_missing_form_field_envelope(test_client) -> None:
    """필수 폼 필드(team 등) 누락 → 422 + VALIDATION_ERROR envelope."""
    files = {"file": ("demo.md", b"# hello\n", "text/markdown")}
    # team / group / year 모두 누락
    form: dict[str, str] = {}
    resp = await test_client.post("/api/convert/", files=files, data=form)
    assert resp.status_code == 422
    _assert_envelope(resp.json(), "VALIDATION_ERROR")


@pytest.mark.asyncio
async def test_convert_empty_filename_envelope(test_client) -> None:
    """빈 파일명 → 415 (확장자 미지원으로 분류) + envelope.

    공식 검증 케이스는 아니지만 envelope 일관성 보강용.
    """
    files = {"file": ("noext", b"abc", "application/octet-stream")}
    form = {
        "team": "HE",
        "group": "CAE",
        "year": "2026",
        "seq": "1",
    }
    resp = await test_client.post("/api/convert/", files=files, data=form)
    # 확장자 없음 → UNSUPPORTED_FORMAT
    assert resp.status_code == 415
    _assert_envelope(resp.json(), "UNSUPPORTED_FORMAT")
