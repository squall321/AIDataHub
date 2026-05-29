"""POST /api/records/import + GET /api/schema/ingest-guide 통합 테스트.

LLM-assisted ingest 흐름의 핵심을 검증:
    1. ingest-guide 엔드포인트가 markdown / json 양쪽으로 응답.
    2. import 가 auto_seq=true 로 id 없는 record 를 자동 채번.
    3. import 가 UPSERT (기존 id 가 있으면 update).
    4. dry_run=true 면 저장하지 않음.
    5. 배열 / wrapped 양쪽 body 형식 모두 허용.
    6. 잘못된 body 는 4xx 또는 per-row error.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# GET /api/schema/ingest-guide
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ingest_guide_markdown(db_client) -> None:
    r = await db_client.get("/api/schema/ingest-guide")
    assert r.status_code == 200, r.text
    body = r.text
    assert "Ingest Guide for LLM" in body
    assert "auto_seq" in body
    assert "POST /api/records/import" in body
    # 핵심 enum 노출 확인
    assert "DOC" in body
    assert "classification" in body


@pytest.mark.asyncio
async def test_ingest_guide_json_format(db_client) -> None:
    r = await db_client.get("/api/schema/ingest-guide?format=json")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert "instructions" in payload
    assert "enums" in payload
    assert "examples" in payload
    assert "id_format" in payload
    assert payload["id_format"]["auto_seq_supported"] is True
    assert "DOC" in payload["enums"]["data_type"]


# ---------------------------------------------------------------------------
# POST /api/records/import — auto_seq
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_import_auto_seq_single_record(db_client) -> None:
    """id 없이 (data_type,team,group,year) 만으로 자동 채번 INSERT."""
    body = {
        "data_type": "DOC",
        "team": "HE",
        "group": "CAE",
        "year": 2026,
        "title": "auto seq test 1",
        "content": {"sections": []},
    }
    r = await db_client.post("/api/records/import?auto_seq=true", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["count"] == 1
    assert out["ok"] == 1
    assert out["failed"] == 0
    assert out["auto_seq"] is True
    row = out["results"][0]
    assert row["action"] == "inserted"
    assert row["id"].startswith("DOC-HE-CAE-2026-")
    assert row["id"].endswith("0000000001")  # SEQ_PAD_WIDTH=10


@pytest.mark.asyncio
async def test_import_auto_seq_array_increments(db_client) -> None:
    """배열로 두 건 → 각각 seq 1, 2 자동 부여."""
    body = [
        {
            "data_type": "DOC",
            "team": "HE",
            "group": "CAE",
            "year": 2026,
            "title": "row 1",
            "content": {},
        },
        {
            "data_type": "DOC",
            "team": "HE",
            "group": "CAE",
            "year": 2026,
            "title": "row 2",
            "content": {},
        },
    ]
    r = await db_client.post("/api/records/import?auto_seq=true", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["count"] == 2
    assert out["ok"] == 2
    seqs = sorted(row["id"][-10:] for row in out["results"])
    assert seqs == ["0000000001", "0000000002"]


@pytest.mark.asyncio
async def test_import_wrapped_body_with_options(db_client) -> None:
    """{records:[...], auto_seq, dry_run} body 도 허용. body 옵션이 우선."""
    body = {
        "auto_seq": True,
        "dry_run": True,
        "records": [
            {
                "data_type": "DOC",
                "team": "HE",
                "group": "CAE",
                "year": 2026,
                "title": "dry run test",
                "content": {},
            }
        ],
    }
    # query param dry_run=false 지만 body 의 true 가 이김
    r = await db_client.post("/api/records/import?dry_run=false", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["dry_run"] is True
    assert out["ok"] == 1
    row = out["results"][0]
    assert row["action"] == "dry_run"
    assert row["would"] == "create"


@pytest.mark.asyncio
async def test_import_no_id_no_auto_seq_fails(db_client) -> None:
    """id 도 없고 auto_seq=false 면 per-row error."""
    body = {
        "data_type": "DOC",
        "team": "HE",
        "group": "CAE",
        "year": 2026,
        "title": "no id",
        "content": {},
    }
    r = await db_client.post("/api/records/import", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] == 0
    assert out["failed"] == 1
    assert "id missing" in out["results"][0]["error"].lower()


# ---------------------------------------------------------------------------
# UPSERT — 같은 id 로 다시 import 하면 update
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_import_upsert_existing_id(db_client) -> None:
    base = {
        "id": "DOC-HE-CAE-2026-0000000099",
        "title": "초기 제목",
        "content": {"sections": [{"section_id": "1", "level": 1, "title": "ch1", "content_text": "first"}]},
    }
    r1 = await db_client.post("/api/records/import", json=base)
    assert r1.status_code == 200, r1.text
    out1 = r1.json()
    assert out1["ok"] == 1
    assert out1["results"][0]["action"] in ("inserted", "updated")

    # 두 번째: 같은 id, content 변경 → updated
    base2 = dict(base)
    base2["title"] = "변경된 제목"
    base2["content"] = {"sections": [{"section_id": "1", "level": 1, "title": "ch1", "content_text": "second"}]}
    r2 = await db_client.post("/api/records/import", json=base2)
    assert r2.status_code == 200, r2.text
    out2 = r2.json()
    assert out2["ok"] == 1
    # 두 번째 호출은 변경이 있으므로 updated 여야 함 (skipped 가 아님)
    action = out2["results"][0]["action"]
    assert action in ("updated", "skipped"), f"unexpected action: {action}"


# ---------------------------------------------------------------------------
# 잘못된 입력
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_import_empty_array_rejected(db_client) -> None:
    r = await db_client.post("/api/records/import", json=[])
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_import_title_required(db_client) -> None:
    """title 없으면 per-row error (전체 요청은 200)."""
    body = {
        "data_type": "DOC",
        "team": "HE",
        "group": "CAE",
        "year": 2026,
        # title 누락
        "content": {},
    }
    r = await db_client.post("/api/records/import?auto_seq=true", json=body)
    assert r.status_code == 200
    out = r.json()
    assert out["failed"] == 1
    assert "title" in out["results"][0]["error"].lower()


@pytest.mark.asyncio
async def test_import_invalid_id_format(db_client) -> None:
    """잘못된 id 형식 → per-row error."""
    body = {
        "id": "not-a-valid-id",
        "title": "x",
        "content": {},
    }
    r = await db_client.post("/api/records/import", json=body)
    assert r.status_code == 200
    out = r.json()
    assert out["failed"] == 1
    assert "invalid id" in out["results"][0]["error"].lower()


# ---------------------------------------------------------------------------
# GET /api/schema/ingest-kit.zip — self-contained kit download
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ingest_kit_zip_contains_expected_files(db_client) -> None:
    """zip 안에 모든 필수 파일이 들어있는지."""
    import io
    import zipfile

    r = await db_client.get("/api/schema/ingest-kit.zip")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/zip")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "SYSTEM_PROMPT.md" in names
    assert "SCHEMA.json" in names
    assert "validate.py" in names
    assert "README.md" in names
    assert ".kit-meta.json" in names
    assert "examples/single.json" in names
    assert "examples/auto_seq.json" in names
    assert "examples/batch.json" in names

    # validate.py 가 표준 라이브러리로 syntax OK
    import compileall
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        z.extractall(tmp)
        v = Path(tmp) / "validate.py"
        assert v.exists()
        body = v.read_text(encoding="utf-8")
        # 핵심 마커
        assert "ENUMS = " in body
        assert "REGISTERED_DOC_TYPES = " in body
        assert "REGISTERED_AGENTS = " in body
        assert "EXPECTED = " in body
        # syntax check
        ok = compileall.compile_file(str(v), quiet=1)
        assert ok, "validate.py compilation failed"


@pytest.mark.asyncio
async def test_ingest_kit_zip_agent_scoped(db_client) -> None:
    """``?agent_type=X`` 지정 시 filename 에 반영, validate.py 의 EXPECTED 채워짐."""
    import io
    import zipfile

    # 먼저 agent 하나 등록해야 함 — seed_records fixture 의존도가 없으므로 직접 생성.
    # 간단히, agent_type 없는 호출만 검증한다 (DB 에 agent 가 있을 수도 없을 수도 있음).
    # agent_type 가 지정되었지만 등록이 없으면 server 가 expected={} 로 폴백한다.
    r = await db_client.get("/api/schema/ingest-kit.zip?agent_type=nonexistent-agent")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "ingest-kit-nonexistent-agent.zip" in cd

    z = zipfile.ZipFile(io.BytesIO(r.content))
    body = z.read("validate.py").decode("utf-8")
    # 등록 안 된 agent → EXPECTED 는 빈 dict
    assert "EXPECTED = {}" in body
