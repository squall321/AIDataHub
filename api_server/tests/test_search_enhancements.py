"""Wave-1~3 검색 보강 단위 테스트.

라이브 DB 없이 가능한 부분만 — flatten 의 section_path / chunk_window 계산
과 hybrid_search 의 RRF rank-fusion, rerank no-op 동작.

실 DB 통합 검증은 ``tests/integration/`` (test_full_flow.py) 가 별도로 담당.
"""
from __future__ import annotations

import asyncio
import os

import pytest


# ---------------------------------------------------------------------------
# _flatten_sections — section_path + chunk_window
# ---------------------------------------------------------------------------
def test_flatten_section_path() -> None:
    from api.ingest.db_writer import _flatten_sections

    tree = [
        {
            "id": "1",
            "level": 1,
            "title": "개요",
            "blocks": [{"type": "paragraph", "text": "A"}],
            "children": [
                {
                    "id": "1.1",
                    "level": 2,
                    "title": "배경",
                    "blocks": [{"type": "paragraph", "text": "B"}],
                },
            ],
        }
    ]
    flat = _flatten_sections(tree)
    assert flat[0]["section_id"] == "1"
    assert flat[0]["section_path"] is None  # top-level
    assert flat[1]["section_id"] == "1.1"
    assert flat[1]["section_path"] == "개요"


def test_flatten_chunk_window_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """env 미설정이면 큰 섹션도 분할되지 않음 (회귀 0)."""
    monkeypatch.delenv("AIDH_CHUNK_WINDOW", raising=False)
    from api.ingest.db_writer import _flatten_sections

    big = [
        {
            "id": "X",
            "level": 1,
            "title": "big",
            "blocks": [{"type": "paragraph", "text": "x" * 5000}],
        }
    ]
    flat = _flatten_sections(big)
    assert len(flat) == 1
    assert flat[0]["parent_section_id"] is None
    assert flat[0]["chunk_index"] is None


def test_flatten_chunk_window_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """AIDH_CHUNK_WINDOW=on + 작은 max chars 로 분할 검증."""
    monkeypatch.setenv("AIDH_CHUNK_WINDOW", "on")
    monkeypatch.setenv("AIDH_CHUNK_MAX_CHARS", "100")
    monkeypatch.setenv("AIDH_CHUNK_WIN_CHARS", "60")
    monkeypatch.setenv("AIDH_CHUNK_OVERLAP", "20")

    from api.ingest.db_writer import _flatten_sections

    big = [
        {
            "id": "X",
            "level": 1,
            "title": "big",
            "blocks": [{"type": "paragraph", "text": "가" * 500}],
        }
    ]
    flat = _flatten_sections(big)
    assert len(flat) > 1
    assert flat[0]["section_id"].startswith("X#")
    assert flat[0]["parent_section_id"] == "X"
    assert flat[0]["chunk_index"] == 0
    # 마지막 chunk 의 index 가 단조 증가
    indices = [r["chunk_index"] for r in flat]
    assert indices == sorted(indices)


# ---------------------------------------------------------------------------
# hybrid_search RRF
# ---------------------------------------------------------------------------
def test_hybrid_search_rrf(monkeypatch: pytest.MonkeyPatch) -> None:
    """semantic + fts 의 rank 결합이 1/(k+rank) 공식과 정렬을 만족."""
    from api.services import search_svc

    async def fake_semantic(_s, q, *, top_k, **kw):
        return [
            {"record_id": "r1", "section_id": "A", "score": 0.95, "tags": []},
            {"record_id": "r1", "section_id": "B", "score": 0.92, "tags": []},
            {"record_id": "r2", "section_id": "C", "score": 0.88, "tags": []},
        ][:top_k]

    # record_ids/data_types 는 이제 fts_search 의 1급 인자다(전역 조회 후 파이썬 후필터가
    # 아니라 SQL 술어). 가짜도 같은 계약을 받아야 회귀를 잡는다.
    async def fake_fts(_s, q, *, limit, record_ids=None, data_types=None):
        return (
            [
                {"record_id": "r2", "section_id": "C", "data_type": "DOC", "tags": [], "snippet": ""},
                {"record_id": "r1", "section_id": "A", "data_type": "DOC", "tags": [], "snippet": ""},
                {"record_id": "r3", "section_id": "D", "data_type": "DOC", "tags": [], "snippet": ""},
            ][:limit],
            3,
        )

    monkeypatch.setattr(search_svc, "semantic_search", fake_semantic)
    monkeypatch.setattr(search_svc, "fts_search", fake_fts)

    async def run() -> list[dict]:
        return await search_svc.hybrid_search(object(), "q", top_k=4, rrf_k=60, fetch_multiplier=2)

    out = asyncio.run(run())
    ids = [(h["record_id"], h["section_id"]) for h in out]
    # A 와 C 가 양쪽에 등장 → 둘 다 더 높은 score. B, D 는 한쪽만.
    assert ids[0] in [("r1", "A"), ("r2", "C")]
    assert {("r1", "B"), ("r3", "D")} <= set(ids)
    # score 내림차순
    scores = [h["score"] for h in out]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# rerank — 미활성 시 no-op
# ---------------------------------------------------------------------------
def test_rerank_disabled_returns_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """AIDH_RERANK_PROVIDER 미설정 → 입력 그대로 반환."""
    monkeypatch.delenv("AIDH_RERANK_PROVIDER", raising=False)
    from api.services.rerank import maybe_rerank

    hits = [
        {"record_id": "r1", "section_id": "A", "score": 0.9, "snippet": "x"},
        {"record_id": "r2", "section_id": "B", "score": 0.7, "snippet": "y"},
    ]
    out = maybe_rerank("query", hits)
    assert out is hits  # no copy when disabled


def test_rerank_empty_hits() -> None:
    from api.services.rerank import maybe_rerank

    assert maybe_rerank("q", []) == []


# ---------------------------------------------------------------------------
# 범위 위임 — hybrid 가 record_ids 를 fts_search 에 **넘겨야** 한다
# ---------------------------------------------------------------------------
def test_hybrid_pushes_scope_into_fts(monkeypatch: pytest.MonkeyPatch) -> None:
    """예전엔 전역으로 뽑고 파이썬에서 걸렀다. 그러면 상위 N 이 범위 밖에서 정해져
    범위 안 결과가 거의 항상 0건이 된다 — 느린 것보다 나쁜 정확성 문제였다."""
    from api.services import search_svc

    seen: dict = {}

    async def fake_semantic(_s, q, *, top_k, **kw):
        return []

    async def fake_fts(_s, q, *, limit, record_ids=None, data_types=None):
        seen["record_ids"] = record_ids
        seen["data_types"] = data_types
        return ([], 0)

    monkeypatch.setattr(search_svc, "semantic_search", fake_semantic)
    monkeypatch.setattr(search_svc, "fts_search", fake_fts)

    async def run():
        return await search_svc.hybrid_search(
            object(), "q", top_k=4, record_ids=["r1", "r2"], data_types=["DOC"])

    asyncio.run(run())
    assert seen["record_ids"] == ["r1", "r2"]
    assert seen["data_types"] == ["DOC"]


def test_리랭커_스위치는_두_경로에_모두_걸린다():
    """semantic_search 는 반환 지점이 둘이다(PG 경로 / SQLite 폴백). 한쪽에만 걸면 운영에서
    아무 일도 안 일어난다 — 실측으로 그 함정을 밟았다(켰는데 순위가 그대로였다)."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "api" / "services" / "search_svc.py"
    body = src.read_text(encoding="utf-8")
    assert body.count("_maybe_rerank(query,") == 2, "두 반환 경로 모두에 리랭크가 걸려야 한다"
    assert "_rerank_enabled()" in body, "꺼져 있으면 후보를 넓게 받지 않아야 한다(DB 낭비 방지)"
