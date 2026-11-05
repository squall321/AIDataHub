# merge-from-drive.sh 의 표본 임베딩 병합을 폐기용 PostgreSQL 에서 — 이 박스의 행을 덮지 않고 순번을 앞에 두는지(10-30 요청 AD-21 후속), '운영이 더 새롭다' 셈과 그중 조회 몫(FU-D7), 이 박스의 조회 수 · 조회 시각을 덮지 않는지(10-31 검토 후속)
"""예전 병합은 agent_sample_embeddings 를 dev 의 대리 키(id)로 맞췄다(ON CONFLICT (id) DO UPDATE). 그래서 이 박스의 다른 agent 행을
덮고(he-x 의 표본이 he-y 로 바뀐다), dev 의 id 를 그대로 넣으면서 순번은 올리지 않아 이 박스의 다음 표본 INSERT 가 겹친 id 로
거절됐다 — cae00 의 '저장 뒤 500' · 표본 색인 실패(AD-21)의 원인 후보다. 이 파일은 진짜 스크립트를 진짜 PostgreSQL 에 대고 돌린다.
레코드 병합이 '운영이 더 새롭다' 며 건너뛴 행 가운데 content_hash 가 덤프와 다른 것을 세어 끝 요약에 알리는지(셈만 — 병합 결과는
그대로), 그중 이 박스의 예전 조회(2026-10-31 고침 전)가 updated_at 을 올린 것을 따로 세는지, 고친 뒤의 조회는 updated_at 을 올리지
않아 dev 의 편집을 막지도 조회 몫을 늘리지도 않는지도 여기서 본다(HWAXPortal followups-1017 FU-D7 · HWAX 10-31 사용자 결정).
그렇게 dev 판으로 덮이는 행도 조회 수 · 조회 시각은 이 박스 것으로 남는지(새 행만 dev 값)도 본다(같은 결정의 검토 후속).

opt-in: ``AIDH_TEST_PG_BIN`` — initdb · pg_ctl · psql · pg_dump 가 있는 폴더(PostgreSQL 14 이상, 그 서버에 pgvector 0.7 이상).
없으면 skip 이다.

    AIDH_TEST_PG_BIN=/usr/lib/postgresql/14/bin .venv/bin/python -m pytest tests/test_merge_samples_pg.py

- 클러스터는 모듈마다 새로 띄운다(initdb, pytest 임시 폴더). 듣는 곳은 리눅스 추상 소켓 하나뿐이다(TCP 없음 · peer 인증). 끝나면 내린다.
- 스키마는 모델에서 뽑는다(conftest pg_engine 의 create_all 과 같은 출처) + 이행 0002 · 0030 · 0032 의 updated_at 트리거.
- apptainer 는 대역이다(``exec instance://… psql|pg_dump|rm`` 을 이 클러스터의 클라이언트로 바로 돌린다). 스크립트는 실경로
  (/tmp · /data)를 시험 폴더로 바꾼 사본을 돌린다 — 박스의 /tmp · /data 에 쓰지 않는다.
"""
from __future__ import annotations

import gzip
import os
import pwd
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "apptainer" / "merge-from-drive.sh"
_PORT = "5432"  # 소켓 이름에만 쓰인다 — 소켓 이름이 클러스터마다 새것이라 겹치지 않는다

# 0032 판 set_updated_at() 과 두 트리거 — 모델 DDL 에는 없다.
_TRIGGERS = """
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    IF current_setting('aidh.keep_updated_at', true) = 'on' THEN
        RETURN NEW;
    END IF;
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_records_updated_at BEFORE UPDATE ON records FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_agents_updated_at BEFORE UPDATE ON agents FOR EACH ROW EXECUTE FUNCTION set_updated_at();
"""

# 박스(DB)와 같은 실 클라이언트로 — `apptainer exec instance://X <prog> args`, -h 만 이 클러스터의 소켓 폴더로 바꾼다.
_FAKE_APPTAINER = """#!/usr/bin/env bash
set -euo pipefail
[ "${1:-}" = exec ] && [[ "${2:-}" == instance://* ]] || { echo "fake apptainer: $*" >&2; exit 2; }
shift 2
prog="$1"; shift
case "$prog" in
  rm) exec rm "$@" ;;
  psql|pg_dump) ;;
  *) echo "fake apptainer: $prog" >&2; exit 2 ;;
esac
args=()
while [ $# -gt 0 ]; do
  if [ "$1" = -h ]; then args+=(-h "$FAKE_PG_SOCK"); shift 2; else args+=("$1"); shift; fi
done
exec "$FAKE_PG_BIN/$prog" "${args[@]}"
"""

# 표본 행의 출처는 embedding_m3_model 에 적는다(dev · box) — 덮였는지를 글뿐 아니라 벡터 행째로 본다.
_ROWS = "SELECT {cols} FROM (VALUES {values}) v(o, a, s) ORDER BY v.o"
_VEC = "(array_fill(0.5::real, ARRAY[1024])::vector)::halfvec"


def _sample_rows(origin: str, rows: list[tuple[str, str]]) -> str:
    values = ", ".join(f"({i}, '{a}', '{s}')" for i, (a, s) in enumerate(rows, 1))
    cols = f"v.a, v.s, {_VEC}, now(), '{origin}'"
    return ("INSERT INTO agent_sample_embeddings (agent_type, sample_text, embedding_m3, embedding_m3_at, embedding_m3_model) "
            + _ROWS.format(cols=cols, values=values) + ";")


# dev 원본 — 표본 id 1..9.
_DEV = """
INSERT INTO agents (agent_type, name, sample_queries, updated_at) VALUES
 ('he-y','Y',ARRAY['y1','y2'],'2026-10-01'), ('he-a','A',ARRAY['a-new1','a-new2'],'2026-10-20'),
 ('he-c','C',ARRAY['c1','c2','c3'],'2026-10-05'), ('he-e','E',ARRAY['e1'],'2026-10-02'), ('he-d','D',ARRAY['d1'],'2026-10-03');
""" + _sample_rows("dev", [("he-y", "y1"), ("he-y", "y2"), ("he-a", "a-new1"), ("he-a", "a-new2"), ("he-c", "c1"),
                           ("he-c", "c2"), ("he-c", "c3"), ("he-e", "e1"), ("he-d", "d1")])

# 박스 — 표본 id 1..7. he-x 는 이 박스에만 있다. he-a 는 dev 가 더 새롭다(병합이 dev 판으로 바꾼다). he-e 는 이 박스에서 표본을
# 비웠고 더 새롭다. he-d 는 같은 글이 두 줄이다(예전 병합의 흔적).
_BOX = """
INSERT INTO agents (agent_type, name, sample_queries, updated_at) VALUES
 ('he-x','X',ARRAY['x1','x2'],'2026-10-15'), ('he-a','A',ARRAY['a-old1'],'2026-10-10'), ('he-y','Y',ARRAY['y1','y2'],'2026-10-01'),
 ('he-e','E','{}','2026-10-25'), ('he-d','D',ARRAY['d1'],'2026-10-03');
""" + _sample_rows("box", [("he-x", "x1"), ("he-x", "x2"), ("he-a", "a-old1"), ("he-y", "y1"), ("he-y", "y2"),
                           ("he-d", "d1"), ("he-d", "d1")])

# 예전 병합(ON CONFLICT (id))을 한 번 거친 박스 — dev 의 id 1..9 가 그대로 들어와 he-x 의 두 행은 he-y 로 바뀌었고, he-e 는 정의가
# 비었는데 dev 의 행을 받았으며, 순번은 이 박스가 마지막으로 뽑은 7 에 머물러 있다.
_BOX_AFTER_OLD_MERGE = """
INSERT INTO agents (agent_type, name, sample_queries, updated_at) VALUES
 ('he-x','X',ARRAY['x1','x2'],'2026-10-15'), ('he-a','A',ARRAY['a-new1','a-new2'],'2026-10-20'), ('he-y','Y',ARRAY['y1','y2'],'2026-10-01'),
 ('he-e','E','{}','2026-10-25'), ('he-d','D',ARRAY['d1'],'2026-10-03'), ('he-c','C',ARRAY['c1','c2','c3'],'2026-10-05');
INSERT INTO agent_sample_embeddings (id, agent_type, sample_text, embedding_m3_model) VALUES
 (1,'he-y','y1','dev'), (2,'he-y','y2','dev'), (3,'he-a','a-new1','dev'), (4,'he-a','a-new2','dev'), (5,'he-c','c1','dev'),
 (6,'he-c','c2','dev'), (7,'he-c','c3','dev'), (8,'he-e','e1','dev'), (9,'he-d','d1','dev');
SELECT setval('agent_sample_embeddings_id_seq', 7);
"""

# 이 박스에서 he-x 를 다시 저장 — sample_embedding_svc.sync_agent_samples 와 같은 꼴(그 agent 행을 지우고 순번으로 새로 넣는다).
_BOX_SAVE = ("BEGIN; DELETE FROM agent_sample_embeddings WHERE agent_type = 'he-x'; "
             "INSERT INTO agent_sample_embeddings (agent_type, sample_text) VALUES ('he-x','x1'), ('he-x','x2'); COMMIT;")


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    bindir = os.environ.get("AIDH_TEST_PG_BIN")
    if not bindir:
        pytest.skip("AIDH_TEST_PG_BIN 미설정 — 폐기용 PostgreSQL 병합 시험 skip")
    bindir = Path(bindir)
    if not all((bindir / p).is_file() for p in ("initdb", "pg_ctl", "psql", "pg_dump")) or not shutil.which("bash"):
        pytest.skip(f"{bindir} 에 initdb · pg_ctl · psql · pg_dump 가 없다")
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable

    import api.db.models  # noqa: F401  (메타데이터 등록)
    from api.db.base import Base

    dialect = postgresql.dialect()
    ddl = ["CREATE EXTENSION IF NOT EXISTS vector;"]
    for table in Base.metadata.sorted_tables:
        ddl.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        ddl += [str(CreateIndex(ix).compile(dialect=dialect)).strip() + ";" for ix in table.indexes]
    schema = "\n".join(ddl) + _TRIGGERS

    root = tmp_path_factory.mktemp("pgmerge")
    data = root / "data"
    # 리눅스 추상 소켓(@이름, PostgreSQL 14 이상) — 파일 경로가 없어 108바이트 소켓 경로 한도에 걸리지 않고 박스의 /tmp 에도
    # 쓰지 않는다. 파일 권한이 없는 소켓이라 인증은 peer 로만 받는다(이 OS 사용자 = DB 역할) — 다른 사용자는 붙지 못한다.
    user = pwd.getpwuid(os.getuid()).pw_name
    sock = f"@aidh-merge-test-{uuid.uuid4().hex[:12]}"
    run = lambda *a, **kw: subprocess.run([str(x) for x in a], capture_output=True, text=True, timeout=120, **kw)  # noqa: E731
    r = run(bindir / "initdb", "-D", data, "-U", user, "--auth-local=peer", "--auth-host=reject", "-E", "UTF8", "--locale=C")
    if r.returncode:
        pytest.skip(f"initdb 실패: {r.stderr[-300:]}")
    opts = f"-c listen_addresses='' -c unix_socket_directories='{sock}' -p {_PORT}"
    r = run(bindir / "pg_ctl", "-D", data, "-l", root / "server.log", "-w", "-o", opts, "start")
    if r.returncode:
        pytest.skip(f"pg_ctl start 실패(추상 소켓은 PostgreSQL 14 이상): {r.stderr[-300:]}")
    try:
        probe = run(bindir / "psql", "-h", sock, "-p", _PORT, "-U", user, "-d", "postgres", "-qtAc", "CREATE EXTENSION vector;")
        if probe.returncode:
            pytest.skip(f"pgvector 를 못 올린다: {probe.stderr[-300:]}")
        yield {"bin": bindir, "sock": sock, "user": user, "schema": schema, "run": run}
    finally:
        run(bindir / "pg_ctl", "-D", data, "-m", "fast", "-w", "stop")


def _psql(pg, db: str, sql: str) -> subprocess.CompletedProcess:
    return pg["run"](pg["bin"] / "psql", "-h", pg["sock"], "-p", _PORT, "-U", pg["user"], "-d", db,
                     "-v", "ON_ERROR_STOP=1", "-qtA", "-c", sql)


def _ok(r: subprocess.CompletedProcess) -> str:
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.fixture()
def boxes(pg, tmp_path):
    """box(aidh) · dev(aidh_dev) DB 를 스키마 + 씨앗(dev 는 주지 않으면 _DEV)으로 짓고 dev 덤프를 만든 뒤, 병합 사본을 돌리는 함수를 준다."""

    def build(box_seed: str, dev_seed: str = _DEV) -> Path:
        for db, seed in (("aidh", box_seed), ("aidh_dev", dev_seed)):
            _ok(_psql(pg, "postgres", f"DROP DATABASE IF EXISTS {db} WITH (FORCE);"))
            _ok(_psql(pg, "postgres", f"CREATE DATABASE {db};"))
            _ok(_psql(pg, db, pg["schema"]))
            _ok(_psql(pg, db, seed))
        r = subprocess.run([str(pg["bin"] / "pg_dump"), "-h", pg["sock"], "-p", _PORT, "-U", pg["user"], "-d", "aidh_dev",
                            "--no-owner", "--no-acl", "-Fp"], capture_output=True, timeout=120)
        assert r.returncode == 0, r.stderr
        dump = tmp_path / "aidh-db-dev.sql.gz"
        dump.write_bytes(gzip.compress(r.stdout))
        return dump

    def merge(dump: Path) -> subprocess.CompletedProcess:
        work = Path(tempfile.mkdtemp(dir=tmp_path, prefix="run-"))
        (work / "appt").mkdir()
        body = SCRIPT.read_text(encoding="utf-8")
        for real, fake in (("/tmp/aidh-merge-load.log", work / "load.log"), ("/tmp/aidh-merge.log", work / "merge.log"),
                           ("/tmp/aidh_mg.bin", work / "mg.bin"), ("/tmp/aidh_mg_rec.bin", work / "mg_rec.bin"),
                           ("/tmp/aidh_bf_", f"{work}/bf_"), ("/tmp/aidh_nk_", f"{work}/nk_"),
                           ("/data/backups/aidh/", f"{work}/backups/")):
            body = body.replace(real, str(fake))
        assert "/tmp/aidh" not in body and "/data/" not in body, "새 실경로가 생겼다 — 위 치환에 더한다"
        (work / "appt" / "merge-from-drive.sh").write_text(body, encoding="utf-8")
        (work / "appt" / "_common.sh").write_text(
            f"load_env() {{ :; }}\nPOSTGRES_PORT={_PORT}\nPOSTGRES_USER={pg['user']}\nPOSTGRES_DB=aidh\nINST_POSTGRES=stubpg\n", encoding="utf-8")
        fakebin = work / "bin"
        fakebin.mkdir()
        (fakebin / "apptainer").write_text(_FAKE_APPTAINER, encoding="utf-8")
        (fakebin / "apptainer").chmod(0o755)
        env = {"PATH": f"{fakebin}:/usr/bin:/bin", "HOME": str(work), "AIDH_MERGE_DUMP": str(dump),
               "FAKE_PG_BIN": str(pg["bin"]), "FAKE_PG_SOCK": pg["sock"]}
        return subprocess.run(["bash", str(work / "appt" / "merge-from-drive.sh")], capture_output=True, text=True,
                              timeout=300, env=env, cwd=work)

    return build, merge


def _samples(pg) -> dict[str, list[tuple[int, str, str]]]:
    out: dict[str, list[tuple[int, str, str]]] = {}
    for line in _ok(_psql(pg, "aidh", "SELECT agent_type, id, sample_text, coalesce(embedding_m3_model, '') "
                                      "FROM agent_sample_embeddings ORDER BY id;")).splitlines():
        agent, rid, text, origin = line.split("|")
        out.setdefault(agent, []).append((int(rid), text, origin))
    return out


def _texts(rows) -> list[tuple[str, str]]:
    return sorted((text, origin) for _, text, origin in rows)


def test_이_박스의_표본은_그대로고_색인이_어긋난_agent_만_덤프_판으로_바꾼다(pg, boxes):
    build, merge = boxes
    dump = build(_BOX)
    r = merge(dump)
    assert r.returncode == 0 and "✓ merge 완료" in r.stdout, r.stdout + r.stderr
    s = _samples(pg)
    # 이 박스에만 있는 agent 와 양쪽이 같은 agent 는 id · 벡터째 그대로다(예전 병합은 he-x 의 두 행을 he-y 로 덮었다).
    assert s["he-x"] == [(1, "x1", "box"), (2, "x2", "box")], s
    assert s["he-y"] == [(4, "y1", "box"), (5, "y2", "box")], s
    # 병합으로 정의가 바뀐 agent · 새 agent · 같은 글이 두 줄인 agent 는 덤프의 행으로(id 는 이 박스 순번).
    assert _texts(s["he-a"]) == [("a-new1", "dev"), ("a-new2", "dev")], s
    assert _texts(s["he-c"]) == [("c1", "dev"), ("c2", "dev"), ("c3", "dev")], s
    assert _texts(s["he-d"]) == [("d1", "dev")], s
    # 이 박스에서 표본을 비운 agent(이 박스 판이 이겼다)는 dev 의 행을 받지 않는다.
    assert "he-e" not in s, s
    assert "덤프 판으로 다시 지은 agent 3 · 지우기만 0" in r.stdout and "⚠ agent_sample_embeddings" not in r.stdout, r.stdout

    # 같은 덤프로 다시 — 바꿀 것이 없다(멱등).
    r2 = merge(dump)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "덤프 판으로 다시 지은 agent 0 · 지우기만 0" in r2.stdout, r2.stdout
    assert _samples(pg) == s

    # 이 박스의 다음 저장이 겹친 id 로 거절되지 않는다(예전 병합 뒤에는 duplicate key 였다).
    _ok(_psql(pg, "aidh", _BOX_SAVE))


def test_예전_병합이_남긴_겹침을_걷고_덤프로_못_고친_agent_는_알린다(pg, boxes):
    build, merge = boxes
    r = merge(build(_BOX_AFTER_OLD_MERGE))
    assert r.returncode == 0 and "✓ merge 완료" in r.stdout, r.stdout + r.stderr
    s = _samples(pg)
    # 정의가 빈 he-e 의 행(8)만 걷는다 — 덤프에 없는 he-x 는 손대지 못하고, 글이 맞는 agent 의 행은 id 째 그대로다.
    assert "he-e" not in s and "he-x" not in s, s
    assert sorted(rid for rows in s.values() for rid, _, _ in rows) == [1, 2, 3, 4, 5, 6, 7, 9], s
    # 순번이 표의 가장 큰 id(9) 뒤로 갔다 — 예전 병합 뒤 이 저장은 'Key (id)=(8) already exists' 였다.
    _ok(_psql(pg, "aidh", _BOX_SAVE))
    assert [rid for rid, _, _ in _samples(pg)["he-x"]] == [10, 11]
    # 덤프로 못 고친 he-x 는 이름째 알리고(병합 실패는 아니다), 다시 지을 길을 적는다.
    assert "지우기만 1 · 순번을 9 로 올림" in r.stdout, r.stdout
    warn = next(line for line in r.stdout.splitlines() if "⚠ agent_sample_embeddings" in line)
    assert "agent 1개" in warn and "he-x" in warn and "/api/agents/resync-samples-all" in warn, warn


def _records(rows: list[tuple[str, int, str | None, str]]) -> str:
    """(id, seq, content_hash, updated_at 날짜) → records INSERT. 본문(content)은 해시에서 지어 해시가 같으면 본문도 같다."""
    q = lambda h: "NULL::text" if h is None else f"'{h}'"  # noqa: E731
    values = ", ".join(f"('{rid}', 'report', 'HE', 'G1', 2026, {seq}, '{rid}', jsonb_build_object('h', {q(h)}), {q(h)}, "
                       f"'{day} 00:00+00')" for rid, seq, h, day in rows)
    return ('INSERT INTO records (id, data_type, team, "group", year, seq, title, content, content_hash, updated_at) '
            f"VALUES {values};")


# 레코드 — '운영이 더 새롭다' 셈(FU-D7). 같은 id 가 박스 · dev 에서 이렇게 갈린다.
#   r1 박스가 더 새롭고 내용이 다르다(센다)        r2 박스가 더 새롭고 내용이 같다
#   r3 dev 가 더 새롭다(병합이 dev 판으로)          r4 시각이 같고 내용이 다르다(더 새롭지는 않다)
#   r5 박스에만 · r6 dev 에만                     r7 박스가 더 새롭고 박스의 해시가 비었다(센다 — IS DISTINCT FROM)
_DEV_RECORDS = _records([("r1", 1, "h-dev-1", "2026-10-10"), ("r2", 2, "h-2", "2026-10-10"), ("r3", 3, "h-dev-3", "2026-10-20"),
                         ("r4", 4, "h-dev-4", "2026-10-10"), ("r6", 6, "h-dev-6", "2026-10-10"), ("r7", 7, "h-dev-7", "2026-10-10")])
_BOX_RECORDS = _records([("r1", 1, "h-box-1", "2026-10-20"), ("r2", 2, "h-2", "2026-10-20"), ("r3", 3, "h-box-3", "2026-10-10"),
                         ("r4", 4, "h-box-4", "2026-10-10"), ("r5", 5, "h-box-5", "2026-10-10"), ("r7", 7, None, "2026-10-20")])


def _record_rows(pg) -> dict[str, tuple[str, str, str]]:
    out = _ok(_psql(pg, "aidh", "SELECT id, coalesce(content_hash, ''), to_char(updated_at AT TIME ZONE 'UTC', 'MM-DD'), xmin::text "
                                "FROM records ORDER BY id;"))
    return {rid: (h, day, xmin) for rid, h, day, xmin in (line.split("|") for line in out.splitlines())}


def test_운영이_더_새롭다며_건너뛴_레코드_중_내용이_다른_수를_세고_병합은_그대로다(pg, boxes):
    build, merge = boxes
    dump = build(_BOX_RECORDS, _DEV_RECORDS)
    r = merge(dump)
    assert r.returncode == 0 and "✓ merge 완료" in r.stdout, r.stdout + r.stderr
    # 병합의 판단은 예전 그대로다 — 박스가 더 새로우면(r1 · r2 · r7) 또는 시각이 같으면(r4) 박스 판, dev 가 더 새로우면 dev 판(시각째).
    rows = _record_rows(pg)
    assert {rid: (h, day) for rid, (h, day, _) in rows.items()} == {
        "r1": ("h-box-1", "10-20"), "r2": ("h-2", "10-20"), "r3": ("h-dev-3", "10-20"), "r4": ("h-box-4", "10-10"),
        "r5": ("h-box-5", "10-10"), "r6": ("h-dev-6", "10-10"), "r7": ("", "10-20")}, rows
    # 박스가 더 새로운데 내용이 다른 것은 r1 · r7 둘이다 — 끝 요약에 한 줄.
    summary = [line for line in r.stdout.splitlines() if "content_hash 가 덤프와 다른 것" in line]
    assert len(summary) == 1 and "records 2건(그중 조회로 올라간 것 0건)" in summary[0], r.stdout

    # 같은 덤프로 다시 — 건너뛴 둘은 그대로 건너뛰어 수도 그대로고, 셈은 읽기만이라 한 행도 다시 쓰지 않는다(xmin 같음).
    r2 = merge(dump)
    assert r2.returncode == 0 and "content_hash 가 덤프와 다른 것 — records 2건" in r2.stdout, r2.stdout + r2.stderr
    assert _record_rows(pg) == rows


class _Captured:
    """세션 대역 — PostgreSQL 이라고 알려 주고(sql_compat.is_postgres 가 get_bind 로 본다) 조회 수 갱신이 실제로 만드는 문장을 받아 둔다."""

    def __init__(self):
        from types import SimpleNamespace

        from sqlalchemy.dialects import postgresql

        self.stmts = []
        self._bind = SimpleNamespace(dialect=postgresql.dialect())

    def get_bind(self):
        return self._bind

    async def execute(self, stmt):
        self.stmts.append(stmt)


def _view_sql(rid: str) -> str:
    """조회 한 번(api/routes/records.py _bump_usage_in_session)이 지금 보내는 문장들 — 한 트랜잭션으로 묶는다(앱의 세션과 같다).
    updated_at 은 그대로 두고 read_count · last_accessed_at 만 올린다(2026-10-31 고침)."""
    import asyncio

    from sqlalchemy.dialects import postgresql

    from api.routes.records import _bump_usage_in_session

    cap = _Captured()
    asyncio.run(_bump_usage_in_session(cap, rid))
    assert len(cap.stmts) == 3, cap.stmts      # 설정 켬 · UPDATE · 설정 끔
    body = "; ".join(str(s.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})) for s in cap.stmts)
    return f"BEGIN; {body}; COMMIT;"


def _old_view_sql(rid: str) -> str:
    """고침 전 조회(2026-10-31 전 _bump_usage_in_session)가 보내던 UPDATE 를 그대로 — 앱 시각(last_accessed_at)을 리터럴로 굳히고
    updated_at 은 열의 onupdate 가 now() 로 붙였다(트리거도 올린다). psql 이 그 뒤에 트랜잭션을 열므로 /api/data?agent= 의 백그라운드
    갱신과 같은 꼴이다(updated_at 이 몇 ms 뒤). 이 문장이 남긴 행은 고친 뒤에도 그대로 남는다(데이터는 고치지 않았다)."""
    from datetime import UTC, datetime

    seen = datetime.now(UTC).isoformat(sep=" ")
    return (f"UPDATE records SET read_count=(records.read_count + 1), last_accessed_at='{seen}', updated_at=now() "
            f"WHERE records.id = '{rid}';")


# 조회 몫 — 박스가 더 새롭고 내용이 다른 다섯을 센다. 그중 updated_at 을 마지막으로 올린 것이 조회인 것은 v1 · v2 둘이다.
#   v1 박스는 dev 보다 옛것이었는데 이 박스에서 고침 전에 한 번 조회돼(그때의 갱신 문장) 더 새로워졌다 — dev 의 편집이 건너뛰어진다
#   v2 고침 전 get_record 꼴의 조회가 마지막(last_accessed_at 이 updated_at 3ms 뒤)
#   e1 조회 한 시간 뒤의 편집이 마지막 · e2 조회된 적 없다(편집이거나 예전 병합)
#   n2 이 박스의 편집이 마지막인데 고친 뒤에 조회됐다 — last_accessed_at 만 앞으로 가 조회로 세지 않는다(고침 전의 한쪽 가름은 셌다).
#      고침 전 조회로 올라간 행이 고친 뒤 다시 조회돼도 이 꼴이 된다 — 조회 몫에서 빠지고 전체 수에는 남는다
#   n1 박스는 dev 보다 옛것이고 고친 뒤에 조회됐다 — updated_at 이 그대로라 dev 판이 온다(세지 않는다)
#   c1 조회 꼴이지만 내용이 같다(세지 않는다) · c2 조회 꼴이지만 dev 가 더 새롭다(dev 판으로 — 세지 않는다)
_DEV_SEEN = _records([("v1", 11, "h-v1-dev", "2026-10-20"), ("v2", 12, "h-v2-dev", "2026-10-20"), ("e1", 13, "h-e1-dev", "2026-10-20"),
                      ("e2", 14, "h-e2-dev", "2026-10-20"), ("c1", 15, "h-c1", "2026-10-20"), ("c2", 16, "h-c2-dev", "2026-10-25"),
                      ("n1", 17, "h-n1-dev", "2026-10-20"), ("n2", 18, "h-n2-dev", "2026-10-20")])
_BOX_SEEN = _records([("v1", 11, "h-v1-box", "2026-10-10"), ("v2", 12, "h-v2-box", "2026-10-21"), ("e1", 13, "h-e1-box", "2026-10-21"),
                      ("e2", 14, "h-e2-box", "2026-10-21"), ("c1", 15, "h-c1", "2026-10-21"), ("c2", 16, "h-c2-box", "2026-10-21"),
                      ("n1", 17, "h-n1-box", "2026-10-10"), ("n2", 18, "h-n2-box", "2026-10-21")])
# 조회 시각만 덧댄다 — keep_updated_at 이라 트리거가 updated_at 을 그대로 둔다.
_BOX_SEEN_AT = """SET aidh.keep_updated_at = 'on';
UPDATE records SET last_accessed_at = updated_at + interval '3 milliseconds' WHERE id = 'v2';
UPDATE records SET last_accessed_at = updated_at - interval '1 hour' WHERE id = 'e1';
UPDATE records SET last_accessed_at = updated_at - interval '5 milliseconds' WHERE id = 'c1';
UPDATE records SET last_accessed_at = updated_at + interval '2 milliseconds' WHERE id = 'c2';"""


def test_운영이_더_새롭다며_건너뛴_것_중_조회가_updated_at_을_올린_것을_따로_센다(pg, boxes):
    build, merge = boxes
    dump = build(_BOX_SEEN, _DEV_SEEN)
    _ok(_psql(pg, "aidh", _BOX_SEEN_AT))
    _ok(_psql(pg, "aidh", _old_view_sql("v1")))
    # 고침 전 조회가 updated_at 을 오늘로 올렸고, 그 값은 last_accessed_at 보다 몇 ms 뒤다 — last_accessed_at >= updated_at 로는 못 센다.
    gap = float(_ok(_psql(pg, "aidh", "SELECT extract(epoch FROM updated_at - last_accessed_at) FROM records "
                                      "WHERE id = 'v1' AND updated_at > '2026-10-20' AND read_count = 1;")).strip() or "nan")
    assert 0 < gap < 1, gap
    # 고친 뒤의 조회 — read_count · last_accessed_at 만 오르고 updated_at 은 씨앗 그대로다.
    for rid in ("n1", "n2"):
        _ok(_psql(pg, "aidh", _view_sql(rid)))
    seen = _ok(_psql(pg, "aidh", "SELECT id, read_count, to_char(updated_at AT TIME ZONE 'UTC', 'MM-DD'), last_accessed_at > now() - interval '1 hour' "
                                 "FROM records WHERE id IN ('n1', 'n2') ORDER BY id;")).split()
    assert seen == ["n1|1|10-10|t", "n2|1|10-21|t"], seen

    r = merge(dump)
    assert r.returncode == 0 and "✓ merge 완료" in r.stdout, r.stdout + r.stderr
    hashes = {rid: h for rid, (h, _, _) in _record_rows(pg).items()}
    # 판단은 예전 그대로다 — 고침 전 조회로 더 새로워진 v1 은 박스 판으로 남는다(dev 의 편집 h-v1-dev 가 오지 않는다). dev 가 더 새로운
    # c2 와, 고친 뒤에 조회만 된 n1(예전이면 조회가 박스를 더 새롭게 만들어 dev 의 편집을 막았다)은 dev 판.
    assert hashes == {"v1": "h-v1-box", "v2": "h-v2-box", "e1": "h-e1-box", "e2": "h-e2-box", "c1": "h-c1", "c2": "h-c2-dev",
                      "n1": "h-n1-dev", "n2": "h-n2-box"}, hashes
    line = next(x for x in r.stdout.splitlines() if "content_hash 가 덤프와 다른 것" in x)
    assert "— records 5건(그중 조회로 올라간 것 2건)." in line, line


# 이 박스의 사용 통계 — 병합이 내용을 dev 판으로 바꿔도 조회 수 · 조회 시각(read_count · last_accessed_at)은 이 박스 것을 둔다
# (HWAX 10-31 사용자 결정의 검토 후속). 고친 조회는 updated_at 을 그대로 두므로 이 박스에서 조회된 행도 dev 가 더 새로우면 덮인다.
# 예전 조회는 박스를 더 새롭게 만들어 그 행을 건너뛰게 했고(내용은 낡은 채) 조회 수는 남았다. 열을 통째로 덮으면 dev 값이 든다.
#   k1 박스 10-10 편집 · 여기서 고친 조회 세 번, dev 10-20 편집 · dev 에서 일곱 번 조회 — 내용은 dev 판, 조회 수 · 조회 시각은 박스 것
#   k2 dev 에만 있다 — 새 행은 dev 의 조회 수 · 조회 시각을 받는다(예전 그대로)
#   k3 여기서 조회된 적 없고 dev 가 더 새롭다 — 이 박스의 0 · 빈 칸 그대로다(dev 의 조회는 이 박스의 통계가 아니다)
_DEV_USAGE = _records([("k1", 21, "h-k1-dev", "2026-10-20"), ("k2", 22, "h-k2-dev", "2026-10-20"),
                       ("k3", 23, "h-k3-dev", "2026-10-20")]) + """
SET aidh.keep_updated_at = 'on';
UPDATE records SET read_count = 7, last_accessed_at = '2026-10-21 00:00+00' WHERE id = 'k1';
UPDATE records SET read_count = 2, last_accessed_at = '2026-10-22 00:00+00' WHERE id = 'k2';
UPDATE records SET read_count = 4, last_accessed_at = '2026-10-24 00:00+00' WHERE id = 'k3';"""
_BOX_USAGE = _records([("k1", 21, "h-k1-box", "2026-10-10"), ("k3", 23, "h-k3-box", "2026-10-10")])


def _usage(pg) -> dict[str, tuple[str, str, str, str]]:
    """id → (content_hash, read_count, updated_at 날짜, last_accessed_at — UTC 마이크로초까지, 없으면 빈 글)."""
    out = _ok(_psql(pg, "aidh", "SELECT id, content_hash, read_count, to_char(updated_at AT TIME ZONE 'UTC', 'MM-DD'), "
                                "coalesce(to_char(last_accessed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US'), '') "
                                "FROM records ORDER BY id;"))
    return {rid: (h, n, day, seen) for rid, h, n, day, seen in (line.split("|") for line in out.splitlines())}


def test_dev_판으로_덮는_행도_이_박스의_조회_수와_조회_시각은_그대로_둔다(pg, boxes):
    build, merge = boxes
    dump = build(_BOX_USAGE, _DEV_USAGE)
    for _ in range(3):
        _ok(_psql(pg, "aidh", _view_sql("k1")))
    h, n, day, seen = _usage(pg)["k1"]
    assert (h, n, day) == ("h-k1-box", "3", "10-10") and seen, (h, n, day, seen)   # 고친 조회 — updated_at 은 씨앗 그대로

    r = merge(dump)
    assert r.returncode == 0 and "✓ merge 완료" in r.stdout, r.stdout + r.stderr
    after = _usage(pg)
    assert after == {"k1": ("h-k1-dev", "3", "10-20", seen),                          # 덮였다 — 조회 수 · 조회 시각만 이 박스 것
                     "k2": ("h-k2-dev", "2", "10-20", "2026-10-22 00:00:00.000000"),  # 새 행은 dev 값
                     "k3": ("h-k3-dev", "0", "10-20", "")}, after                     # dev 의 조회는 오지 않는다
