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


def test_구어를_현장_용어로_넓힌다():
    """'배터리가 부풀어 올랐어요' 는 pwr-swelling 을 못 찾는데 '배터리 스웰링' 은 1위로 찾는다
    (실측). 인덱스가 아니라 어휘가 어긋난 것이라 후보 수·리랭커로는 안 고쳐진다."""
    from api.services.recommend_svc import expand_query
    out = expand_query("배터리가 부풀어 올랐어요")
    assert "스웰링" in out and "배터리가 부풀어 올랐어요" in out, "원문은 남기고 용어만 덧붙인다"
    assert "swelling" in out
    assert expand_query("떨어뜨렸을 때 화면이 깨지는 문제").count("낙하") >= 1
    # 걸리는 게 없으면 손대지 않는다 — 잘 되던 질의를 망가뜨리지 않는 것이 더 중요하다.
    assert expand_query("ABD 행렬 계산") == "ABD 행렬 계산"
    assert expand_query("") == ""


def test_현장표현_사전은_코퍼스에_있는_말만_쓴다():
    """확장어가 인덱스에 없는 말이면 덧붙여도 아무 효과가 없다 — 사전을 늘릴 때 가장 쉬운 실수라
    파일 형식과 '치환 아님' 규칙만이라도 코드가 지킨다(어휘 대조는 build 시 수동)."""
    import json
    from pathlib import Path

    from api.services.recommend_svc import _field_terms, expand_query

    path = Path(__file__).resolve().parents[1] / "config" / "field_terms.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["terms"], "사전이 비면 구어 질의가 종전처럼 빗나간다"
    for k, v in raw["terms"].items():
        assert k.strip() and v.strip(), f"빈 항목: {k!r}"
        assert k not in v.split(), f"{k}: 자기 자신을 확장어로 넣지 마라"
    assert len(_field_terms()) == len(raw["terms"])
    # 원문 보존 — 치환하면 잘 되던 전문용어 질의가 망가진다.
    q = "액정에 잔상이 남아요"
    assert q in expand_query(q) and "번인" in expand_query(q)


def test_agent_search_도_현장표현을_넓힌다_단_tag_는_예외():
    """구어로 물으면 전문가가 **거절**했다(실측: pwr-swelling 에 '배터리가 부풀어 올랐어요' →
    hits 0 · refused=true, 같은 사람에게 '배터리 스웰링' 은 히트가 난다). 자료가 없어서가 아니라
    말이 안 맞아 점수가 임계 밑으로 떨어진 것이다. 단 tag 모드는 콤마 구분 **정확 태그**라
    확장하면 매칭이 깨진다."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "api" / "mcp_runtime.py"
    body = src.read_text(encoding="utf-8")
    assert 'search_q = q if mode == "tag" else expand_query(q)' in body
    head = body[body.index("async def agent_search("):]
    head = head[: head.index("\nasync def ", 10)]
    assert "session, q," not in head and "\n                q,\n" not in head, (
        "agent_search 안의 검색 호출은 확장 질의(search_q)를 써야 한다"
    )
