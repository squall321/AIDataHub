"""임베딩 슬롯 m3 — 네 표에 BGE-M3 halfvec(1024) 열을 나란히 더한다(지금 열은 그대로).

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-16

왜 나란히인가:
    예전 384→768 이행(0013)은 임베딩을 전부 비우고 열 형을 바꾼 뒤 백필했다 — 그동안 의미검색이 빈다.
    절 109만 개를 다시 임베딩하는 동안 그럴 수 없어 새 열을 비어 있는 채로 더하고, 백필이 채운 뒤 설정 한 줄
    (``AIDH_EMBED_ACTIVE=m3``)로 읽기를 옮긴다. 되돌리기는 그 줄이다(``api.services.embed_slots``).

더하는 열(모두 NULL 허용 — 데이터를 다시 쓰지 않으므로 큰 표에서도 카탈로그만 바뀐다):
    - record_sections         : embedding_m3 · embedding_m3_at · embedding_m3_model
    - records                 : signature_embedding_m3 · signature_embedding_m3_at · signature_embedding_m3_model
    - agent_sample_embeddings : embedding_m3 · embedding_m3_at · embedding_m3_model
    - mcp_uploads             : description_embedding_m3 · description_embedding_m3_at · description_embedding_m3_model
    ``*_at`` · ``*_model`` 의 뜻은 record_sections 의 embedded_at · embedding_model 과 같다 — 빈 본문은 시각을
    채우고 모델을 'skipped-empty' 로 적어 백로그에서 뺀다.

색인은 여기서 짓지 않는다:
    빈 열에 HNSW 를 먼저 지으면 백필의 행마다 색인 갱신이 붙어 느려지고, 일반 CREATE INDEX 는 표의 쓰기를
    막는다. 백필이 끝난 뒤 ``python -m api.scripts.embed_index --slot m3`` 가 CONCURRENTLY 로 짓는다.

임베딩만 쓰는 갱신은 updated_at 을 움직이지 않는다:
    records 에는 BEFORE UPDATE 트리거(0002, set_updated_at)가 있어 어떤 UPDATE 든 updated_at 을 now() 로 덮는다.
    그대로면 m3 시그니처 백필이 레코드 5만 9천 건의 updated_at 을 한꺼번에 오늘로 바꾼다 — cae00 merge 는
    updated_at 이 더 최신인 쪽을 고르므로(merge-from-drive.sh) dev 의 모든 레코드가 cae00 의 최근 편집을 덮고,
    updated_at 순 목록(tag_search 등)도 백필 순서로 뒤섞인다. 그래서 트리거 함수가 트랜잭션 설정
    ``aidh.keep_updated_at = 'on'``(SET LOCAL)을 보면 건너뛰게 한다 — 임베딩 백필만 그 설정으로 쓴다.
    다른 모든 UPDATE 는 예전과 같다.

잠금을 오래 기다리지 않는다:
    열을 더하는 ALTER TABLE 은 잠깐이지만 AccessExclusive 잠금을 잡는다. 그 표에 오래 열린 트랜잭션이 있으면(예전 임베딩
    스윕은 절 21만 개를 한 트랜잭션으로 몇 시간 쥐었다) 그것을 기다리는 동안 뒤에 줄 선 검색까지 모두 선다. 그래서
    lock_timeout 10초 — 못 잡으면 이 이행이 실패하고(start_api.sh 는 alembic 오류로 멈추고 옛 API 는 그대로 돈다) 그
    트랜잭션이 끝난 뒤 다시 올린다.

downgrade 는 열을 지우고(색인이 있으면 함께 사라진다) 트리거 함수를 0002 의 것으로 되돌린다. v1 열은 손대지 않는다.
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_M3_DIM = 1024

# 0002 의 set_updated_at() 에 '임베딩만 쓰는 트랜잭션은 건너뛴다' 를 더한 것.
_TRIGGER_FN_KEEP = """
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    IF current_setting('aidh.keep_updated_at', true) = 'on' THEN
        RETURN NEW;
    END IF;
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER_FN_0002 = """
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# (표, 벡터 열) — 시각 · 모델 열 이름은 벡터 열 이름 뒤에 _at · _model 을 붙인다.
_COLUMNS = (
    ("record_sections", "embedding_m3"),
    ("records", "signature_embedding_m3"),
    ("agent_sample_embeddings", "embedding_m3"),
    ("mcp_uploads", "description_embedding_m3"),
)


def _require_halfvec(bind) -> None:
    """halfvec 은 pgvector 0.7 부터다 — 낡은 확장이면 'type halfvec does not exist' 대신 할 일을 말하고 멈춘다."""
    ver = bind.exec_driver_sql("SELECT extversion FROM pg_extension WHERE extname = 'vector'").scalar()
    try:
        ok = ver is not None and tuple(int(x) for x in str(ver).split(".")[:2]) >= (0, 7)
    except ValueError:
        ok = False
    if not ok:
        raise RuntimeError(
            f"pgvector 0.7 이상이 필요하다(halfvec) — 이 DB 의 vector 확장은 {ver!r} 다. 서버 라이브러리가 "
            "0.7 이상이면 그 DB 에서 `ALTER EXTENSION vector UPDATE;` 를 돌린 뒤 다시 올리고, 아니면 postgres "
            "SIF(pgvector/pgvector:pg16)를 갱신한다."
        )


def upgrade() -> None:
    from alembic import op

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # SQLite 시험은 모델에서 create_all 로 짓는다.
    _require_halfvec(bind)
    op.execute("SET LOCAL lock_timeout = '10s'")
    for table, vec in _COLUMNS:
        op.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN IF NOT EXISTS {vec} halfvec({_M3_DIM}), "
            f"ADD COLUMN IF NOT EXISTS {vec}_at timestamptz, "
            f"ADD COLUMN IF NOT EXISTS {vec}_model varchar(100)"
        )
    op.execute(_TRIGGER_FN_KEEP)


def downgrade() -> None:
    from alembic import op

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '10s'")
    for table, vec in _COLUMNS:
        op.execute(
            f"ALTER TABLE {table} "
            f"DROP COLUMN IF EXISTS {vec}_model, "
            f"DROP COLUMN IF EXISTS {vec}_at, "
            f"DROP COLUMN IF EXISTS {vec}"
        )
    op.execute(_TRIGGER_FN_0002)
