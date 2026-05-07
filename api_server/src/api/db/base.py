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
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
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
