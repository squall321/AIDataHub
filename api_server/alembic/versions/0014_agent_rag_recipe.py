"""agent RAG recipe — retrieval/response config + system prompt + sample queries

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-11

agent 을 단순 라우팅 태그가 아니라 "RAG 레시피"로 격상한다. 각 agent 가
자신의 검색 동작·응답 스타일·라우팅 힌트를 데이터로 보유하게 되어, LLM 은
agent 선택만 하고 그 뒤 RAG 행동은 서버가 통제한다.

추가 컬럼 (모두 nullable / 빈값 default — 기존 agents 행 무영향):

1. ``agents.retrieval_config``   JSONB default ``'{}'``
   - top_k (int)
   - score_threshold (float, 0.0~1.0)
   - data_type_filter (list[str])
   - tag_boost (dict[str, float])

2. ``agents.system_prompt``      TEXT nullable
   - LLM 에 그대로 주입할 system prompt.
   - 비어있으면 ``recommend_svc.build_system_prompt`` 가 generic 폴백 생성.

3. ``agents.response_config``    JSONB default ``'{}'``
   - max_tokens (int)
   - citation_required (bool)
   - refusal_message (str)
   - refuse_below_score (float) — 임계치 미만 검색 결과면 답변 거절

4. ``agents.sample_queries``     TEXT[] default ``'{}'``
   - 라우팅 정확도 향상용 예시 질문. 추후 ``recommend_agents`` 임베딩
     집계에 합산할 후크 (이 마이그레이션에서는 단순 저장).

검증 정책: warn-only (잘못된 키는 무시). strict 검증은 후속 마이그레이션.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "retrieval_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "agents",
        sa.Column("system_prompt", sa.Text(), nullable=True),
    )
    op.add_column(
        "agents",
        sa.Column(
            "response_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "agents",
        sa.Column(
            "sample_queries",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agents", "sample_queries")
    op.drop_column("agents", "response_config")
    op.drop_column("agents", "system_prompt")
    op.drop_column("agents", "retrieval_config")
