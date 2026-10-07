"""SQLAlchemy 2.0 비동기 엔진/세션/Declarative Base.

- `Base`        : DeclarativeBase + AsyncAttrs (async lazy-loading 지원)
- `engine`      : asyncpg 기반 AsyncEngine
- `SessionLocal`: async_sessionmaker(AsyncSession)
- `get_session` : FastAPI 의존성 주입용 async generator

기존 `api.database` 모듈은 이 모듈에서 심볼을 재익스포트하여 하위 호환을 유지한다.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import AsyncAdaptedQueuePool

from ..config import settings

# ---------------------------------------------------------------------------
# Engine & SessionMaker
# ---------------------------------------------------------------------------
class KnobNamingQueuePool(AsyncAdaptedQueuePool):
    """풀 대기 만료 문구에 손잡이 이름을 붙인다. 풀의 동작은 그대로다.

    SQLAlchemy 의 원문('QueuePool limit of size 12 overflow 8 reached, connection timed out,
    timeout 60.00')은 내부 표현이라, 그 문구를 도구 오류로 받는 쪽(심의 엔진·게이트웨이)이 어느
    설정을 봐야 하는지 알 수 없었다. 원문은 뒤에 그대로 남긴다 — 로그 검색이 그 문자열로 이뤄진다.
    """

    def connect(self):
        try:
            return super().connect()
        except sa_exc.TimeoutError as err:
            raise sa_exc.TimeoutError(
                f"커넥션 풀이 {self.timeout():g}초 동안 차 있었다(DB_POOL_TIMEOUT, DB_POOL_SIZE)"
                f" — {err.args[0]}",
                code=err.code,
            ) from None


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
        "poolclass": KnobNamingQueuePool,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
    }
)

# 새 연결을 맺는 한도 — 안 주면 asyncpg 기본값 60초가 걸린다. 풀 대기(DB_POOL_TIMEOUT 60초)와 같은
# 시각에 터져 'PG 가 죽었다' 와 '풀이 찼다' 가 구별되지 않았다. 느린 일을 재는 값이 아니라 죽은 상대를
# 재는 값이라 짧게 둔다 — 로컬 PG 는 정상이면 밀리초에 붙는다. 안쪽이 바깥보다 작아야 하므로
# DB_POOL_TIMEOUT 보다 작게 유지한다.
# SQLite 에는 주지 않는다 — aiosqlite 의 timeout 은 다른 뜻(잠금 대기)이다.
_connect_kwargs = (
    {}
    if settings.database_url.startswith("sqlite")
    else {"connect_args": {"timeout": settings.aidh_db_connect_timeout_s}}
)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    **_pool_kwargs,
    **_connect_kwargs,
)

if _connect_kwargs:

    @event.listens_for(engine.sync_engine, "do_connect")
    def _name_connect_timeout(dialect, _conn_rec, cargs, cparams):
        """연결 시간 초과에 손잡이 이름을 붙인다.

        asyncpg 는 메시지가 빈 TimeoutError 를 던지고 SQLAlchemy 는 그것을 감싸지 않는다. 그대로
        두면 도구 오류가 'Error executing tool agent_search: ' 로 끝나 무엇이 판정했는지 알 수 없다.
        """
        try:
            return dialect.connect(*cargs, **cparams)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"PG 에 {settings.aidh_db_connect_timeout_s:g}초 안에 연결하지 못했다"
                "(AIDH_DB_CONNECT_TIMEOUT_S)"
            ) from exc

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
