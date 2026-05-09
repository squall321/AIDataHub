"""scalability features placeholder (Agent 32)

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-08

이 리비전은 의도적으로 no-op 이다. Agent 32 (S3 비동기 잡 큐) 는 인-메모리
구조만 사용하므로 영속 테이블이 필요하지 않다. 다만 마이그레이션 체인을
선형으로 유지하기 위해 ``0009`` 슬롯을 reserve 한다.

향후 잡 영속화가 필요해지면(`AUTO_EMBED_ON_INSERT` 가 production 워크로드
대상이 되면) 다음 단계 마이그레이션에서 ``jobs`` 테이블을 추가한다 — 본
revision 에는 변경을 추가하지 않는다 (불변).
"""
from __future__ import annotations

from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op."""
    pass


def downgrade() -> None:
    """No-op."""
    pass
