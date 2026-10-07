"""SQLAlchemy 2.0 비동기 엔진/세션/Declarative Base.

- `Base`        : DeclarativeBase + AsyncAttrs (async lazy-loading 지원)
- `engine`      : asyncpg 기반 AsyncEngine
- `SessionLocal`: async_sessionmaker(AsyncSession)
- `get_session` : FastAPI 의존성 주입용 async generator

기존 `api.database` 모듈은 이 모듈에서 심볼을 재익스포트하여 하위 호환을 유지한다.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..config import settings

# ---------------------------------------------------------------------------
# Engine & SessionMaker
# ---------------------------------------------------------------------------
# 풀 인자를 명시한다 — 안 주면 SQLAlchemy 기본값(5 + 10, 대기 30초)이 걸린다. 심의 한 건이
# agent_search 를 17~20개 한꺼번에 쏘고(호출 하나가 검색이 끝날 때까지 세션 하나를 쥔다) 두 건이
# 겹치면 34개가 필요한데, 15개에서 막혀 'QueuePool limit of size 5 overflow 10 reached' 로
# 떨어졌다(S26U 심사 실사용 피드백, 2026-10-07). 층끼리도 어긋나 있었다 — 부르는 쪽(심의 엔진)은
# 지식카드 조회를 120초 기다리는데 풀은 30초에 먼저 포기했다.
# 합 20 이 34 를 한꺼번에 받지는 못한다 — 넘치는 호출은 60초까지 줄을 선다. 더 올리지 않은 것은
# PG max_connections 가 기본 100 이고, EXTERNAL_POSTGRES=1 박스에서는 다른 앱과 한 인스턴스를
# 나눠 쓰기 때문이다. 올릴 때는 그 인스턴스를 쓰는 앱들의 합을 같이 본다.
# SQLite(스모크·시험)에는 주지 않는다 — :memory: 는 StaticPool 이라 이 인자를 TypeError 로 거절한다.
_pool_kwargs = (
    {}
    if settings.database_url.startswith("sqlite")
    else {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
    }
)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    **_pool_kwargs,
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


# ---------------------------------------------------------------------------
# Declarative Base
# ---------------------------------------------------------------------------
class Base(AsyncAttrs, DeclarativeBase):
    """SQLAlchemy 2.0 모델 베이스.

    - `AsyncAttrs`: 비동기 컨텍스트에서 lazy-loaded 관계를 `await obj.awaitable_attrs.x`로 접근 가능.
    - 하위 클래스는 `Mapped[...] = mapped_column(...)` 스타일로 컬럼을 정의해야 한다.
    """


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 의존성 주입용 세션 생성기.

    Usage:
        @router.get(...)
        async def handler(session: AsyncSession = Depends(get_session)):
            ...
    """
    async with SessionLocal() as session:
        yield session


__all__ = [
    "Base",
    "SessionLocal",
    "engine",
    "get_session",
]
