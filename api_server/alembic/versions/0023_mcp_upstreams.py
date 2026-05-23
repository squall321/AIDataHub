"""mcp_upstreams + mcp_proxy_calls — Wave-6 P1 MCP federation 인프라.

Revision ID: 0023
Revises: 0021
Create Date: 2026-05-23

스키마:
    - ``mcp_upstreams``    : federation 대상 외부 FastMCP 서버 등록.
    - ``mcp_proxy_calls``  : dispatch 호출별 감사 로그.

설계 노트:
    - auth 의 실토큰은 DB 에 저장하지 않음 — ``{type, env_var}`` 만 기록.
    - 부팅 시 우리 서버가 모든 enabled upstream 에 health check + tools/list.
    - 죽은 upstream 은 tools/list 응답에서 자동 제외 (재연결 시 복구).
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "0023"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_upstreams (
            alias TEXT PRIMARY KEY,
            transport VARCHAR(20) NOT NULL,
            url TEXT,
            command TEXT,
            command_args JSONB,
            auth JSONB,
            description_prefix TEXT NOT NULL DEFAULT '',
            tls_verify BOOLEAN NOT NULL DEFAULT true,
            enabled BOOLEAN NOT NULL DEFAULT true,
            rate_limit_per_min INT NOT NULL DEFAULT 100,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_health_check_at TIMESTAMPTZ,
            last_health_status VARCHAR(40),
            last_tool_count INT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_proxy_calls (
            id BIGSERIAL PRIMARY KEY,
            ts TIMESTAMPTZ NOT NULL DEFAULT now(),
            caller TEXT,
            upstream_alias TEXT NOT NULL,
            raw_tool_name TEXT NOT NULL,
            exposed_tool_name TEXT NOT NULL,
            latency_ms INT NOT NULL,
            status VARCHAR(40) NOT NULL,
            error_code VARCHAR(40),
            client_ip TEXT,
            request_id TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_proxy_calls_alias_ts "
        "ON mcp_proxy_calls (upstream_alias, ts DESC)"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS idx_proxy_calls_alias_ts")
    op.execute("DROP TABLE IF EXISTS mcp_proxy_calls")
    op.execute("DROP TABLE IF EXISTS mcp_upstreams")
