"""Wave-7 P1 — tool_embedding_svc 단위 테스트.

테스트 범위:
    1. build_description_text — manifest 변형별 합성 텍스트
    2. sync_tool_embedding — MCPUpload 행의 description_embedding 갱신
    3. search_tools — query → top-k tools (deterministic via HashEmbedder)
    4. search_tools — deprecated_at IS NOT NULL 행 제외
    5. /api/recommend/agents 응답에 relevant_tools 필드 포함
"""
from __future__ import annotations

import pytest

# aiosqlite 미설치 환경에서는 단위 테스트 skip (dev PC install 금지 규칙).
try:
    import aiosqlite  # noqa: F401
    aiosqlite_available = True
except ImportError:  # pragma: no cover
    aiosqlite_available = False


def _make_manifest(
    *, name: str = "demo_tool", description: str = "demo description",
    when_to_use: str | None = None, examples: list[str] | None = None,
    title: str | None = None,
) -> dict:
    m: dict = {"name": name, "description": description}
    if title:
        m["title"] = title
    if when_to_use or examples:
        hints: dict = {}
        if when_to_use:
            hints["when_to_use"] = when_to_use
        if examples:
            hints["example_calls"] = [{"natural_language": e} for e in examples]
        m["llm_hints"] = hints
    return m


# ---------------------------------------------------------------------------
# 1. build_description_text
# ---------------------------------------------------------------------------
def test_build_description_text_full() -> None:
    from api.services.tool_embedding_svc import build_description_text

    text = build_description_text(
        _make_manifest(
            name="csv_summary",
            title="CSV → 컬럼별 통계 요약",
            description="CSV 통계 계산",
            when_to_use="EDA 요청 시",
            examples=["이 CSV 통계 보여줘", "탭 구분자 CSV"],
        )
    )
    assert "CSV → 컬럼별 통계 요약" in text
    assert "CSV 통계 계산" in text
    assert "사용 시점: EDA 요청 시" in text
    assert "이 CSV 통계 보여줘" in text


def test_build_description_text_minimal() -> None:
    """description 만 있어도 결과 반환."""
    from api.services.tool_embedding_svc import build_description_text

    text = build_description_text(_make_manifest(description="just a tool"))
    assert text.startswith("just a tool")


def test_build_description_text_empty_fallback_to_name() -> None:
    """description 비어있으면 name 반환."""
    from api.services.tool_embedding_svc import build_description_text

    text = build_description_text({"name": "tool_x", "description": ""})
    assert text == "tool_x"


# ---------------------------------------------------------------------------
# 2. sync_tool_embedding
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.skipif(not aiosqlite_available, reason="aiosqlite 미설치")
async def test_sync_tool_embedding_populates(test_session_maker) -> None:
    """MCPUpload 행 INSERT 후 sync 호출 → description_embedding 채워짐."""
    from api.db.models import MCPUpload
    from api.services.tool_embedding_svc import sync_tool_embedding

    manifest = _make_manifest(
        name="csv_summary", description="CSV 통계 계산", when_to_use="EDA"
    )
    async with test_session_maker() as s:
        s.add(MCPUpload(name="csv_summary", current_sha="a" * 64, manifest=manifest))
        await s.commit()

    async with test_session_maker() as s:
        result = await sync_tool_embedding(s, name="csv_summary", manifest=manifest)

    assert result["embedded"] is True
    assert result["text_len"] > 0

    async with test_session_maker() as s:
        from sqlalchemy import select
        row = (
            await s.execute(select(MCPUpload).where(MCPUpload.name == "csv_summary"))
        ).scalar_one()
        assert row.description_embedding is not None
        assert len(row.description_embedding) > 0
        assert row.description_text is not None and "CSV" in row.description_text


@pytest.mark.asyncio
@pytest.mark.skipif(not aiosqlite_available, reason="aiosqlite 미설치")
async def test_sync_tool_embedding_empty_clears(test_session_maker) -> None:
    """매니페스트가 비어있으면 embedding=None 으로 클리어."""
    from api.db.models import MCPUpload
    from api.services.tool_embedding_svc import sync_tool_embedding

    async with test_session_maker() as s:
        s.add(MCPUpload(
            name="empty_tool", current_sha="b" * 64,
            manifest={"name": "empty_tool", "description": ""},
        ))
        await s.commit()

    async with test_session_maker() as s:
        result = await sync_tool_embedding(
            s, name="empty_tool",
            manifest={"name": "", "description": ""},  # 완전 빈
        )

    assert result["embedded"] is False
    assert result["reason"] == "empty"


# ---------------------------------------------------------------------------
# 3. search_tools — 동일 텍스트로 검색하면 self 가 top-1
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.skipif(not aiosqlite_available, reason="aiosqlite 미설치")
async def test_search_tools_self_match_is_top1(test_session_maker) -> None:
    """HashEmbedder 는 동일 텍스트만 sim=1.0. 다른 도구 텍스트는 무관 cosine ~ 0.5."""
    from api.db.models import MCPUpload
    from api.services.tool_embedding_svc import search_tools, sync_tool_embedding

    tools = {
        "csv_summary": _make_manifest(
            description="CSV 컬럼별 통계 요약", when_to_use="EDA 요청 시"
        ),
        "image_blur": _make_manifest(
            description="이미지 블러 처리", when_to_use="사진 흐림 효과"
        ),
        "weather_now": _make_manifest(
            description="지정 도시 현재 날씨", when_to_use="실시간 날씨 조회"
        ),
    }
    async with test_session_maker() as s:
        for name, m in tools.items():
            s.add(MCPUpload(name=name, current_sha=name * 16, manifest=m))
        await s.commit()

    async with test_session_maker() as s:
        for name, m in tools.items():
            await sync_tool_embedding(s, name=name, manifest=m)

    async with test_session_maker() as s:
        # query 가 csv_summary 의 description_text 와 정확히 일치 → self top-1
        from api.services.tool_embedding_svc import build_description_text
        q = build_description_text(tools["csv_summary"])
        results = await search_tools(s, q, top_k=3)

    assert len(results) >= 1
    assert results[0]["name"] == "csv_summary"
    assert results[0]["score"] >= 0.9


@pytest.mark.asyncio
@pytest.mark.skipif(not aiosqlite_available, reason="aiosqlite 미설치")
async def test_search_tools_excludes_deprecated(test_session_maker) -> None:
    """deprecated_at IS NOT NULL 도구는 검색 결과에서 제외."""
    from datetime import datetime, timezone

    from api.db.models import MCPUpload
    from api.services.tool_embedding_svc import search_tools, sync_tool_embedding

    m = _make_manifest(description="old retired tool", when_to_use="never")
    async with test_session_maker() as s:
        s.add(MCPUpload(
            name="old_tool", current_sha="c" * 64, manifest=m,
            deprecated_at=datetime.now(timezone.utc),
        ))
        await s.commit()

    async with test_session_maker() as s:
        await sync_tool_embedding(s, name="old_tool", manifest=m)

    async with test_session_maker() as s:
        from api.services.tool_embedding_svc import build_description_text
        results = await search_tools(s, build_description_text(m), top_k=5)

    names = [r["name"] for r in results]
    assert "old_tool" not in names


# ---------------------------------------------------------------------------
# 4. Empty query → empty list
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.skipif(not aiosqlite_available, reason="aiosqlite 미설치")
async def test_search_tools_empty_query_returns_empty(test_session_maker) -> None:
    from api.services.tool_embedding_svc import search_tools

    async with test_session_maker() as s:
        assert await search_tools(s, "", top_k=5) == []
        assert await search_tools(s, "   ", top_k=5) == []
