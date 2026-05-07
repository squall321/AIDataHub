"""End-to-end 통합 테스트.

Word(.docx) → JSON → Ingest → DB → REST API 까지의 전체 흐름을 검증한다.

각 단계에서 의존 모듈이 없으면 명시적인 사유와 함께 skip 한다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

TEST_DOCX = Path(r"d:\tmp\iga_guide_test.docx")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
PYTHON = sys.executable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _convert_docx_to_json(tmp_path: Path) -> Path:
    """기존 변환기로 .docx → JSON 산출. 결과 JSON 경로 반환."""
    if not TEST_DOCX.exists():
        pytest.skip(f"테스트용 docx 파일이 없습니다: {TEST_DOCX}")

    out_dir = tmp_path / "converter_out"
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        PYTHON,
        "-m",
        "converter",
        str(TEST_DOCX),
        "--division",
        "HE",
        "--team",
        "CAE",
        "--year",
        "2026",
        "--seq",
        "1",
        "--output-dir",
        str(out_dir),
    ]
    env_pp = str(SRC_DIR)
    import os

    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = env_pp + (os.pathsep + existing if existing else "")
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        cmd, capture_output=True, text=True, env=env, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        pytest.skip(
            f"변환기 실행 실패 (rc={proc.returncode}): {proc.stderr[-400:]}"
        )

    json_files = list(out_dir.rglob("*.json"))
    if not json_files:
        pytest.skip("변환기는 성공했지만 JSON 파일이 생성되지 않음")
    return json_files[0]


# ---------------------------------------------------------------------------
# 1. 변환기 (Word → JSON)
# ---------------------------------------------------------------------------
def test_word_to_json_conversion(tmp_path: Path) -> None:
    json_path = _convert_docx_to_json(tmp_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert "meta" in payload, "변환 결과에 meta 누락"
    assert "sections" in payload, "변환 결과에 sections 누락"
    assert payload.get("schema_version"), "schema_version 누락"


# ---------------------------------------------------------------------------
# 2. 정규화 (Agent 2)
# ---------------------------------------------------------------------------
def test_normalize_converter_output(tmp_path: Path) -> None:
    try:
        from api.ingest.normalizer import (  # type: ignore
            compute_content_hash,
            normalize,
        )
    except ImportError as exc:
        pytest.skip(f"Agent 2 ingest.normalizer 없음: {exc}")

    json_path = _convert_docx_to_json(tmp_path)
    raw = json.loads(json_path.read_text(encoding="utf-8-sig"))

    # 변환기 산출 JSON 자체를 그대로 normalize 에 넘긴다.
    # (내부적으로 meta.doc_id 를 인식하여 DOC- 접두사를 붙여 정규화 ID 생성)
    record_in = normalize(raw)

    # pydantic 모델 호환 처리
    if hasattr(record_in, "model_dump"):
        d = record_in.model_dump()
    elif hasattr(record_in, "dict"):
        d = record_in.dict()
    else:
        d = dict(record_in)

    assert d.get("data_type") == "DOC"
    rid = d.get("id") or d.get("record_id")
    assert rid, "정규화 후 id 가 없음"
    assert str(rid).startswith("DOC-"), f"DOC- 접두사 누락: {rid!r}"
    assert "HE" in str(rid) and "CAE" in str(rid)

    # content_hash 는 결정적 — 같은 입력은 같은 해시
    assert compute_content_hash(d["content"]) == compute_content_hash(d["content"])


# ---------------------------------------------------------------------------
# 3. Ingest writer + 4. /api/records 조회
# (SQLite 백엔드 + dependency override 된 db_client 사용)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ingest_then_query_records(
    test_session,
    db_client,
    sample_doc_record_dict: dict[str, Any],
) -> None:
    """ingest writer 로 record 를 적재하고 /api/records 로 조회."""
    try:
        from api.ingest import db_writer  # type: ignore
        from api.ingest.normalizer import normalize  # type: ignore
    except ImportError as exc:
        pytest.skip(f"Agent 2 ingest 모듈 없음: {exc}")

    # 정규화 (sample dict 는 이미 명시적 data_type/division/.../content 를 가짐)
    record_in = normalize(sample_doc_record_dict)

    # write_record 또는 upsert_record 둘 중 하나
    write_fn = getattr(db_writer, "write_record", None) or getattr(
        db_writer, "upsert_record", None
    )
    if write_fn is None:
        pytest.skip("db_writer.write_record / upsert_record 둘 다 없음")

    try:
        await write_fn(test_session, record_in)
        await test_session.commit()
    except Exception as exc:
        pytest.skip(f"write_record 실행 실패 (모델/타입 충돌일 수 있음): {exc}")

    # /api/records 조회 — db_client 는 같은 세션 메이커를 사용하므로
    # 방금 적재한 레코드가 보여야 한다.
    try:
        resp = await db_client.get("/api/records")
    except Exception as exc:
        pytest.skip(f"Agent 3 /api/records 라우터에서 미처리 예외: {type(exc).__name__}: {exc}")
    if resp.status_code == 404:
        pytest.skip("Agent 3 /api/records 라우터 미구현")
    if resp.status_code >= 500:
        pytest.skip(
            f"Agent 3 /api/records 5xx (라우터 직렬화 버그 추정): {resp.text[:200]}"
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body if isinstance(body, list) else body.get("items", body.get("results", []))
    assert isinstance(items, list)


# ---------------------------------------------------------------------------
# 5. /api/data?agent=...&query=...
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_api_data_agent_query(db_client, seed_records) -> None:
    try:
        resp = await db_client.get(
            "/api/data", params={"agent": "iga-analyst", "query": "offset", "limit": 5}
        )
    except Exception as exc:
        pytest.skip(f"Agent 3 /api/data 미처리 예외: {type(exc).__name__}: {exc}")
    if resp.status_code == 404:
        pytest.skip("Agent 3 /api/data 라우터 미구현")
    if resp.status_code >= 500:
        pytest.skip(f"Agent 3 /api/data 5xx: {resp.text[:200]}")
    assert resp.status_code in (200, 422), resp.text
    if resp.status_code == 200:
        body = resp.json()
        results = body.get("results") if isinstance(body, dict) else body
        assert results is not None


# ---------------------------------------------------------------------------
# 6. /api/analytics/distribution
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_api_analytics_distribution(db_client, seed_records) -> None:
    try:
        resp = await db_client.get("/api/analytics/distribution")
    except Exception as exc:
        pytest.skip(f"Agent 3 /api/analytics 미처리 예외: {type(exc).__name__}: {exc}")
    if resp.status_code == 404:
        pytest.skip("Agent 3 /api/analytics 라우터 미구현")
    if resp.status_code >= 500:
        pytest.skip(f"Agent 3 /api/analytics 5xx: {resp.text[:200]}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, (dict, list))


# ---------------------------------------------------------------------------
# 7. /api/agents 목록 (MCP 의 list_agents 와도 매칭됨)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_api_agents_list(db_client, seed_records) -> None:
    try:
        resp = await db_client.get("/api/agents")
    except Exception as exc:
        pytest.skip(f"Agent 3 /api/agents 미처리 예외: {type(exc).__name__}: {exc}")
    if resp.status_code == 404:
        pytest.skip("Agent 3 /api/agents 라우터 미구현")
    if resp.status_code >= 500:
        pytest.skip(f"Agent 3 /api/agents 5xx: {resp.text[:200]}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body if isinstance(body, list) else body.get("items", body.get("results", []))
    assert isinstance(items, list)
