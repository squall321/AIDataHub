# 조회 수 갱신(_bump_usage_in_session)이 records.updated_at 을 움직이지 않는 문장을 보내는지 — 꼴만 본다(DB 없이 기본으로 돈다)
"""조회는 내용을 바꾸지 않는다 — read_count · last_accessed_at 만 오르고 updated_at 은 그대로다(HWAX 2026-10-31 사용자 결정).

예전 갱신은 열의 onupdate(now())와 PG 트리거(set_updated_at)가 updated_at 도 조회 시각으로 올려, 박스 사이 병합
(deploy/apptainer/merge-from-drive.sh — updated_at 이 더 새 쪽을 고른다)에서 조회만 된 판이 다른 박스의 더 새 편집을 이겼다.
여기서는 보낸 문장을 받아 방언별로 컴파일해 본다. 실제 PostgreSQL 트리거와 두 조회 길(GET /api/records/{id} ·
/api/data?agent=)은 test_view_keeps_updated_at_pg(opt-in ``AIDH_TEST_PG_BIN``)가 본다.
"""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.dialects import postgresql, sqlite

_RID = "DOC-HE-CAE-2026-0000000001"


class _Session:
    """세션 대역 — 방언만 알려 주고(sql_compat.is_postgres 가 get_bind 로 본다) 보낸 문장을 받아 둔다."""

    def __init__(self, dialect):
        self.stmts = []
        self._bind = SimpleNamespace(dialect=dialect)

    def get_bind(self):
        return self._bind

    async def execute(self, stmt):
        self.stmts.append(stmt)


async def _bump(dialect) -> list[str]:
    from api.routes.records import _bump_usage_in_session

    s = _Session(dialect)
    await _bump_usage_in_session(s, _RID)
    return [str(x.compile(dialect=dialect, compile_kwargs={"literal_binds": True})).strip() for x in s.stmts]


async def test_PostgreSQL_은_updated_at_을_자기_값으로_두고_트리거를_이_문장에만_건너뛴다():
    """열의 onupdate 는 자기 값(updated_at=records.updated_at)으로 막고, 트리거는 aidh.keep_updated_at 을 이 UPDATE 앞에서 켜고 뒤에서
    끈다 — 같은 트랜잭션의 뒤 편집은 예전처럼 updated_at 을 올린다(그 동작은 실 PG 시험이 본다)."""
    stmts = await _bump(postgresql.dialect())
    assert len(stmts) == 3, stmts
    on, upd, off = stmts
    assert on == "SET LOCAL aidh.keep_updated_at = 'on'", stmts
    assert off == "SET LOCAL aidh.keep_updated_at = 'off'", stmts
    assert upd.startswith("UPDATE records SET read_count=(records.read_count + 1), last_accessed_at="), upd
    assert "updated_at=records.updated_at" in upd and "now()" not in upd, upd
    assert upd.endswith(f"WHERE records.id = '{_RID}'"), upd


async def test_SQLite_도_updated_at_을_자기_값으로_둔다():
    """SQLite(시험 DB)에는 트리거 · 설정이 없다 — 열의 onupdate(CURRENT_TIMESTAMP)만 막는다."""
    stmts = await _bump(sqlite.dialect())
    assert len(stmts) == 1, stmts
    assert "updated_at=records.updated_at" in stmts[0] and "CURRENT_TIMESTAMP" not in stmts[0], stmts
