"""Alembic 환경 스크립트 (async-aware).

- DB URL: `api.config.settings.database_url` (asyncpg 드라이버)에서 읽는다.
- target_metadata: `api.db.base.Base.metadata` (모델 import 보장 위해 `api.db.models`도 import).
- offline 모드: `--sql`로 SQL 문 출력 (DB 접속 불필요).
- online 모드: AsyncEngine + run_sync로 마이그레이션 실행.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ---------------------------------------------------------------------------
# Alembic Config
# ---------------------------------------------------------------------------
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# 모델 메타데이터 로드
#   - `api.db.models`를 import하여 모든 테이블이 Base.metadata에 등록되도록 한다.
#   - `api.config.settings`에서 DB URL을 가져와 alembic config에 주입한다.
# ---------------------------------------------------------------------------
from api.config import settings  # noqa: E402
from api.db.base import Base  # noqa: E402
from api.db import models as _models  # noqa: E402, F401  (메타데이터 등록 트리거)

# settings의 URL을 alembic에 주입 (alembic.ini의 sqlalchemy.url은 비어있음)
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline migration
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    """Offline 모드: DB 접속 없이 SQL 스크립트만 생성."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migration (async)
# ---------------------------------------------------------------------------
def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """AsyncEngine으로 실제 DB 마이그레이션 실행."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
