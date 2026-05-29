"""sync_sources + sync_runs — 외부 데이터 소스 정기 pull 동기화 인프라.

Revision ID: 0027
Revises: 0026
Create Date: 2026-05-28

배경:
    SignalForge / MX White Paper 등 외부 시스템의 데이터를 AX Hub 가 주기적으로
    pull 하여 적재. 초기 backfill 은 외부에서 push 하고, 이후 변경분은 우리가
    pull. 두 경로 모두 ``external_id_map`` (alembic 0026) 으로 동일 record 와
    매핑.

테이블:
    sync_sources  : 외부 소스 1행 (URL, key, mapping rules, schedule, cursor)
    sync_runs     : 실행 이력 (모든 run 의 결과 영구 기록)

설계 원칙:
    - 상대 측 변경 최소화: list endpoint 1개만 있으면 동작
    - cursor / updated_at / tombstone 부재해도 자체 폴백
    - rate limit / pii_masked 자체 보호
    - mapping_rules 는 JSONB — 코드 변경 없이 다른 source 추가
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------------- sync_sources
    op.create_table(
        "sync_sources",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=40), nullable=False, unique=True,
                  comment="외부 시스템 식별자 (e.g. 'signalforge', 'mxwp')"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),

        # --- 호출 설정 ---
        sa.Column("base_url", sa.Text(), nullable=False,
                  comment="예: http://signalforge:8000"),
        sa.Column("api_key", sa.Text(), nullable=True,
                  comment="X-API-Key 헤더 값 (secret — secret storage 권장)"),
        sa.Column("auth_header", sa.String(length=40), nullable=False,
                  server_default="X-API-Key"),
        sa.Column("list_endpoint", sa.Text(), nullable=False,
                  comment="예: /api/v1/voc/list"),
        sa.Column("list_method", sa.String(length=8), nullable=False,
                  server_default="GET"),
        sa.Column("detail_endpoint", sa.Text(), nullable=True,
                  comment="상세 보강용 (옵션) 예: /api/v1/voc/{id}"),

        # --- 페이지네이션 파라미터 (상대측 명세 흡수) ---
        sa.Column("cursor_param", sa.String(length=40), nullable=False,
                  server_default="cursor"),
        sa.Column("since_param", sa.String(length=40), nullable=False,
                  server_default="since"),
        sa.Column("limit_param", sa.String(length=40), nullable=False,
                  server_default="limit"),
        sa.Column("page_size", sa.Integer(), nullable=False, server_default="200"),

        # --- 운영 안전장치 (상대측이 헤더 안 줘도 우리 측 자체 보호) ---
        sa.Column("max_rps", sa.Float(), nullable=False, server_default="2.0",
                  comment="우리 측 throttle (req/s) — rate limit 헤더 없을 때 자체 보호"),
        sa.Column("retry_max", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("retry_backoff_sec", sa.Float(), nullable=False, server_default="2.0"),
        sa.Column("trust_pii_masked", sa.Boolean(), nullable=False,
                  server_default=sa.text("FALSE"),
                  comment="상대측 pii_masked=true 보증 신뢰. FALSE 면 record.classification='confidential' 자동"),

        # --- 매핑 + 변환 ---
        sa.Column("mapping_rules", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb"),
                  comment="외부 필드 → AX Hub record 필드 매핑 (jq-like path)"),

        # --- 상태 ---
        sa.Column("cursor", sa.Text(), nullable=True,
                  comment="마지막 sync 의 cursor 값 (없으면 since 기반)"),
        sa.Column("last_sync_at", postgresql.TIMESTAMP(timezone=True), nullable=True,
                  comment="다음 호출의 since 값으로 사용"),
        sa.Column("last_status", sa.String(length=20), nullable=False, server_default="never",
                  comment="never | ok | error | partial"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_fetched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_imported_count", sa.Integer(), nullable=False, server_default="0"),

        # --- 스케줄 (cron 외부에서 실행) ---
        sa.Column("schedule_cron", sa.String(length=40), nullable=True,
                  comment="문서용 (실 worker 는 외부 cron). 예: '*/30 * * * *'"),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("TRUE")),

        # --- 감사 ---
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_sync_sources_enabled", "sync_sources", ["enabled"])

    # ---------------------------------------------------------------- sync_runs
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.BigInteger(),
                  sa.ForeignKey("sync_sources.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running",
                  comment="running | ok | error | partial"),
        sa.Column("trigger", sa.String(length=20), nullable=False, server_default="manual",
                  comment="manual | cron | webhook"),
        sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tombstoned_count", sa.Integer(), nullable=False, server_default="0",
                  comment="삭제 감지로 soft-delete 된 record 수"),
        sa.Column("cursor_before", sa.Text(), nullable=True),
        sa.Column("cursor_after", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("dead_letter", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb"),
                  comment="변환·import 실패 record 들의 원본 + 에러 (재시도용)"),
    )
    op.create_index("idx_sync_runs_source", "sync_runs", ["source_id"])
    op.create_index("idx_sync_runs_started", "sync_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("idx_sync_runs_started", table_name="sync_runs")
    op.drop_index("idx_sync_runs_source", table_name="sync_runs")
    op.drop_table("sync_runs")
    op.drop_index("idx_sync_sources_enabled", table_name="sync_sources")
    op.drop_table("sync_sources")
