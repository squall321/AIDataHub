"""mcp_uploads + mcp_uploads_history — Wave-5 P1 도구 업로드 메타.

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-23

스키마:
    - ``mcp_uploads``         : 업로드된 도구의 현재(최신) 상태. PK = name.
    - ``mcp_uploads_history`` : 업로드 시도별 감사 기록 (성공/실패 모두).

설계 노트:
    - sha256 캐시 hit 검사 = ``current_sha == request.sha`` 비교 1회.
    - version bump = ``current_version += 1`` + 이전 row 를 archived_versions
      JSONB 배열에 push.
    - 등록 거절(smoke fail/build fail) 도 history 에는 ``registered=false`` 로
      기록 — 감사 추적.
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_uploads (
            name TEXT PRIMARY KEY,
            current_sha CHAR(64) NOT NULL,
            current_version INT NOT NULL DEFAULT 1,
            manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
            capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
            archived_versions JSONB NOT NULL DEFAULT '[]'::jsonb,
            registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            registered_by TEXT,
            deprecated_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_uploads_history (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            sha CHAR(64) NOT NULL,
            version INT NOT NULL,
            uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            uploaded_by TEXT,
            smoke_result JSONB,
            build_log_path TEXT,
            sif_path TEXT,
            registered BOOLEAN NOT NULL DEFAULT false,
            archived_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_mcp_uploads_history_name "
        "ON mcp_uploads_history (name, version)"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS idx_mcp_uploads_history_name")
    op.execute("DROP TABLE IF EXISTS mcp_uploads_history")
    op.execute("DROP TABLE IF EXISTS mcp_uploads")
