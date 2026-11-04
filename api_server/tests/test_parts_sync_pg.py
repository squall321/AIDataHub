# 조각 스냅숏을 폐기용 PostgreSQL 에서 끝에서 끝까지 — 전체 덤프 병합과 같은 결과 · 바뀐 조각만 · 같은 데이터면 같은 바이트 · 되돌리기(HWAXPortal docs/aidh-parts-sync)
"""dev 의 parts-to-drive.sh(내기 · 올리기) → cae00 의 fetch-parts.sh(받기) → merge-from-drive.sh 조각 길(병합) → rollback-parts.sh(되돌리기)를
진짜 스크립트 · 진짜 PostgreSQL · 진짜 rclone 으로 돌린다(Drive 자리는 시험 폴더). 클러스터 · apptainer 대역은 test_merge_samples_pg 의
것을 쓴다 — 대역에는 cat 만 더한다(앞 모습을 컨테이너 밖으로 옮긴다).

opt-in: ``AIDH_TEST_PG_BIN``(test_merge_samples_pg 와 같다) + rclone. 없으면 skip 이다.

    AIDH_TEST_PG_BIN=/usr/lib/postgresql/16/bin .venv/bin/python -m pytest tests/test_parts_sync_pg.py

- 두 세계(전체 덤프 병합 · 조각 병합)는 같은 씨앗 DB 의 복제(CREATE DATABASE … TEMPLATE)라 만든 시각까지 같다.
- 조각 수는 8 이다(운영 64). 시험 레코드 r05 · r07 · r08 이 한 조각(5)이다 — 같은 조각의 다른 줄을 다시 쓰지 않는지 본다.
"""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from tests.test_merge_samples_pg import _BOX, _DEV, _FAKE_APPTAINER, _PORT, _VEC, _ok, _psql, pg  # noqa: F401  (pg 는 픽스처)

APPT = Path(__file__).resolve().parents[2] / "deploy" / "apptainer"
N = 8

# 앞 모습(rollback-parts.sh 의 재료)을 컨테이너 안 /tmp 에서 박스로 옮기는 cat 을 더한다.
_FAKE_APPTAINER_CAT = _FAKE_APPTAINER.replace('  rm) exec rm "$@" ;;', '  rm) exec rm "$@" ;;\n  cat) exec cat "$@" ;;')
assert _FAKE_APPTAINER_CAT != _FAKE_APPTAINER

_T0 = "'2026-09-01 00:00+00'"   # 만든 시각 — now() 를 쓰지 않는다


def _part(rid: str) -> int:
    """조각 번호의 독립 정의(aidh_parts.part_of 와 같아야 한다)."""
    return int(hashlib.md5(rid.encode()).hexdigest(), 16) % N


def _records(rows) -> str:
    """(id, seq, content_hash, updated_at 날짜, 시그니처 m3 모델 | None, 조회 수, 조회 날짜 | None) → records INSERT."""
    vals = []
    for rid, seq, h, day, m3, reads, seen in rows:
        m3sql = f"{_VEC}, '2026-10-02 00:00+00', '{m3}'" if m3 else "NULL, NULL, NULL"
        seensql = f"'{seen} 00:00+00'" if seen else "NULL"
        vals.append(f"('{rid}', 'report', 'HE', 'G1', 2026, {seq}, 'T-{rid}', jsonb_build_object('h', '{h}'), '{h}', "
                    f"'{day} 00:00+00', {_T0}, {reads}, {seensql}, {m3sql})")
    return ('INSERT INTO records (id, data_type, team, "group", year, seq, title, content, content_hash, updated_at, created_at, '
            'read_count, last_accessed_at, signature_embedding_m3, signature_embedding_m3_at, signature_embedding_m3_model) VALUES '
            + ", ".join(vals) + ";")


def _sections(rows) -> str:
    """(record_id, section_id, 본문, m3 모델 | None) → record_sections INSERT(id 는 그 DB 의 순번)."""
    vals = []
    for rid, sid, body, m3 in rows:
        m3sql = f"{_VEC}, '2026-10-02 00:00+00', '{m3}'" if m3 else "NULL, NULL, NULL"
        vals.append(f"('{rid}', '{sid}', 1, 'S{sid}', '{body}', {m3sql})")
    return ("INSERT INTO record_sections (record_id, section_id, level, title, content_text, embedding_m3, embedding_m3_at, "
            "embedding_m3_model) VALUES " + ", ".join(vals) + ";")


_SAME = [(f"r{i:02d}", i, f"h{i}", "2026-10-10", None, 0, None) for i in range(8, 13)]
_DEV_IDS = ["r01", "r02", "r03", "r04", "r05", "r06", "r07"] + [x[0] for x in _SAME]
# dev — r02 는 dev 가 더 새롭다 · r03 은 박스가 더 새롭다 · r04 는 dev 에만 시그니처 m3 · r06 · r07 은 dev 에만(r06 은 dev 에서 7번 조회).
_DEV_WORLD = _DEV + f"""
INSERT INTO org_teams (code, name, created_at, updated_at) VALUES ('HE','HE팀',{_T0},'2026-10-01'), ('SW','SW팀',{_T0},'2026-10-02');
INSERT INTO org_groups (team_code, code, name, created_at, updated_at) VALUES
 ('HE','G1','1그룹',{_T0},'2026-10-01'), ('HE','G2','2그룹(새 이름)',{_T0},'2026-10-05');
INSERT INTO doc_types (code, name, expected_sections, created_at) VALUES ('report','보고서',ARRAY['1','2'],{_T0}), ('memo','메모',ARRAY['1'],{_T0});
""" + _records([("r01", 1, "h1", "2026-10-10", None, 0, None), ("r02", 2, "h2-dev", "2026-10-20", None, 0, None),
                ("r03", 3, "h3-dev", "2026-10-10", None, 0, None), ("r04", 4, "h4", "2026-10-10", "m3-dev", 0, None),
                ("r05", 5, "h5", "2026-10-10", None, 0, None), ("r06", 6, "h6", "2026-10-10", None, 7, "2026-10-21"),
                ("r07", 7, "h7", "2026-10-10", None, 0, None)] + _SAME) + _sections(
    [(r, "1", f"{r} 1절" + ("(dev)" if r == "r02" else ""), "m3-dev" if r == "r04" else None) for r in _DEV_IDS]
    + [(r, "2", f"{r} 2절", None) for r in _DEV_IDS]) + f"""
INSERT INTO agent_records (agent_type, record_id, priority) VALUES ('he-a','r01',1), ('he-a','r02',1), ('he-c','r06',1);
INSERT INTO record_attachments (id, record_id, number, kind, caption, created_at) VALUES
 ('att-r01-1','r01',1,'figure','그림 1(dev)',{_T0}), ('att-r06-1','r06',1,'table','표 1',{_T0});
INSERT INTO external_id_map (source, external_id, record_id, created_at, updated_at) VALUES
 ('plm','E1','r01',{_T0},'2026-10-01'), ('plm','E6','r06',{_T0},'2026-10-01');
INSERT INTO mcp_upstreams (alias, transport, url, created_at, last_health_check_at, last_health_status, last_tool_count) VALUES
 ('up1','http','http://up1.dev',{_T0},'2026-10-30','ok',10), ('up2','http','http://up2',{_T0},'2026-10-30','ok',4);
"""

# 박스 — r01 은 여기서 3번 조회 · r01 의 3절과 r20 은 이 박스에만 · 절은 거꾸로 넣어 대리 id 가 dev 와 다르다.
_BOX_SECTIONS = ([(r, s, f"{r} {s}절" + ("(box)" if r in ("r02", "r03") else ""), None)
                  for r in ["r01", "r02", "r03", "r04", "r05", "r20"] + [x[0] for x in _SAME] for s in ("1", "2")]
                 + [("r01", "3", "r01 3절(이 박스에만)", None)])
_BOX_WORLD = _BOX + f"""
INSERT INTO org_teams (code, name, created_at, updated_at) VALUES ('HE','HE팀',{_T0},'2026-10-01');
INSERT INTO org_groups (team_code, code, name, created_at, updated_at) VALUES ('HE','G1','1그룹',{_T0},'2026-10-01'), ('HE','G2','2그룹',{_T0},'2026-10-01');
INSERT INTO doc_types (code, name, expected_sections, created_at) VALUES ('report','보고서',ARRAY['1','2'],{_T0});
""" + _records([("r01", 1, "h1", "2026-10-10", None, 3, "2026-10-25"), ("r02", 2, "h2-box", "2026-10-10", None, 0, None),
                ("r03", 3, "h3-box", "2026-10-20", None, 0, None), ("r04", 4, "h4", "2026-10-10", None, 0, None),
                ("r05", 5, "h5", "2026-10-10", None, 0, None), ("r20", 20, "h20", "2026-10-10", None, 0, None)] + _SAME) \
    + _sections(list(reversed(_BOX_SECTIONS))) + f"""
INSERT INTO agent_records (agent_type, record_id, priority) VALUES ('he-a','r01',2), ('he-x','r20',1);
INSERT INTO record_attachments (id, record_id, number, kind, caption, created_at) VALUES ('att-r01-1','r01',1,'figure','그림 1',{_T0});
INSERT INTO external_id_map (source, external_id, record_id, created_at, updated_at) VALUES
 ('plm','E20','r20',{_T0},'2026-10-01'), ('plm','E1','r01',{_T0},'2026-10-01');
INSERT INTO mcp_upstreams (alias, transport, url, created_at, last_health_check_at, last_health_status, last_tool_count) VALUES
 ('up1','http','http://up1.box',{_T0},'2026-11-01','down',3);
"""

# 표 → 자연키(차례). None 은 모든 열로 차례를 맺는다(표본 — 자연키가 없다).
_KEYS = {"org_teams": "code", "org_groups": "team_code, code", "doc_types": "code", "agents": "agent_type", "records": "id",
         "agent_records": "agent_type, record_id", "record_sections": "record_id, section_id", "record_attachments": "id",
         "agent_sample_embeddings": None, "external_id_map": "source, external_id", "mcp_upstreams": "alias"}
_SURROGATE = {"record_sections": {"id"}, "external_id_map": {"id"}, "agent_sample_embeddings": {"id"}}
_BOX_OWN = {"records": {"read_count", "last_accessed_at"},
            "mcp_upstreams": {"last_health_check_at", "last_health_status", "last_tool_count"}}


def _state(pg, db: str, *, ids: bool = False, own: bool = False) -> dict[str, list[str]]:
    """표마다 줄 전부(글로) — 대리 id · 이 박스 몫 열(조회 통계 · 상류 점검)은 고를 때만."""
    out = {}
    for t, key in _KEYS.items():
        cols = _ok(_psql(pg, db, "SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
                                 f"AND table_name='{t}' ORDER BY ordinal_position;")).split()
        cols = [c for c in cols if (ids or c not in _SURROGATE.get(t, ())) and (own or c not in _BOX_OWN.get(t, ()))]
        sel = ", ".join(f"coalesce(\"{c}\"::text, '∅')" for c in cols)
        order = key or ", ".join(str(i) for i in range(1, len(cols) + 1))
        out[t] = _ok(_psql(pg, db, f"SELECT {sel} FROM public.{t} ORDER BY {order};")).splitlines()
    return out


def _own(pg, db: str) -> dict[str, str]:
    """이 박스 몫 열 — 레코드 조회 수 · 조회 시각과 상류 점검 칸."""
    day = "coalesce(to_char({} AT TIME ZONE 'UTC', 'YYYY-MM-DD'), '∅')"
    rows = _ok(_psql(pg, db, f"SELECT 'rec:' || id, read_count || '/' || {day.format('last_accessed_at')} FROM records "
                             "UNION ALL SELECT 'up:' || alias, coalesce(last_health_status, '∅') || '/' || "
                             f"coalesce(last_tool_count::text, '∅') || '/' || {day.format('last_health_check_at')} FROM mcp_upstreams;"))
    return dict(line.split("|", 1) for line in rows.splitlines())


def _xmins(pg, db: str) -> dict[str, str]:
    rows = _ok(_psql(pg, db, "SELECT 'rec:' || id, xmin::text FROM records "
                             "UNION ALL SELECT 'sec:' || record_id || '/' || section_id, xmin::text FROM record_sections;"))
    return dict(line.split("|", 1) for line in rows.splitlines())


def _next_second() -> None:
    """목록 이름(manifest-<초>)이 겹치지 않게 다음 초까지 기다린다."""
    time.sleep(1.02 - time.time() % 1)


class _World:
    """dev(aidh_dev) · 씨앗 박스(aidh_seed)와 그 복제들, Drive 자리(drive/) · cae00 조각 캐시(cache/) · dev 로컬 조각(dev-parts/)."""

    def __init__(self, pg, root: Path):
        self.pg, self.root = pg, root
        self.remote, self.cache, self.out = root / "drive", root / "cache", root / "dev-parts"
        self.fakebin = root / "bin"
        self.fakebin.mkdir()
        (self.fakebin / "apptainer").write_text(_FAKE_APPTAINER_CAT, encoding="utf-8")
        (self.fakebin / "apptainer").chmod(0o755)
        # rclone 은 진짜를 부르되 부른 인자를 적는다 — Drive 에서 무엇을 받는지(조각 전부를 받는 실수)를 본다.
        (self.fakebin / "rclone").write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$RCLONE_LOG"\nexec {shutil.which("rclone")} "$@"\n',
                                             encoding="utf-8")
        (self.fakebin / "rclone").chmod(0o755)
        self.rclone_log = root / "rclone.log"
        (root / "rclone.conf").write_text("", encoding="utf-8")

    def sql(self, db: str, sql: str) -> str:
        return _ok(_psql(self.pg, db, sql))

    def seed(self, box: str = _BOX_WORLD, dev: str = _DEV_WORLD) -> None:
        for db, seed in (("aidh_seed", box), ("aidh_dev", dev)):
            self.sql("postgres", f"DROP DATABASE IF EXISTS {db} WITH (FORCE);")
            self.sql("postgres", f"CREATE DATABASE {db};")
            self.sql(db, self.pg["schema"])
            self.sql(db, seed)

    def clone(self, name: str) -> str:
        self.sql("postgres", f"DROP DATABASE IF EXISTS {name} WITH (FORCE);")
        self.sql("postgres", f"CREATE DATABASE {name} TEMPLATE aidh_seed;")
        return name

    def _appt(self, db: str, script: str) -> Path:
        work = Path(tempfile.mkdtemp(dir=self.root, prefix="run-"))
        appt = work / "appt"
        appt.mkdir()
        body = (APPT / script).read_text(encoding="utf-8")
        for real, fake in (("/tmp/aidh-merge-load.log", work / "load.log"), ("/tmp/aidh-merge.log", work / "merge.log"),
                           ("/tmp/aidh_mg.bin", work / "mg.bin"), ("/tmp/aidh_bf_", f"{work}/bf_"), ("/tmp/aidh_nk_", f"{work}/nk_"),
                           ("/data/backups/aidh/", f"{work}/backups/")):
            body = body.replace(real, str(fake))
        assert "/tmp/aidh" not in body and "/data/" not in body, f"{script} 에 새 실경로가 생겼다 — 위 치환에 더한다"
        (appt / script).write_text(body, encoding="utf-8")
        shutil.copy(APPT / "aidh_parts.py", appt / "aidh_parts.py")
        (appt / "_common.sh").write_text(f"load_env() {{ :; }}\nPOSTGRES_PORT={_PORT}\nPOSTGRES_USER={self.pg['user']}\n"
                                         f"POSTGRES_DB={db}\nINST_POSTGRES=stubpg\n", encoding="utf-8")
        return appt

    def _run(self, db: str, script: str, *args: str, **env: str) -> subprocess.CompletedProcess:
        appt = self._appt(db, script)
        full = {"PATH": f"{self.fakebin}:/usr/bin:/bin", "HOME": str(appt.parent), "FAKE_PG_BIN": str(self.pg["bin"]),
                "FAKE_PG_SOCK": self.pg["sock"], "RCLONE_CONFIG": str(self.root / "rclone.conf"), "RCLONE_LOG": str(self.rclone_log),
                "AIDH_PARTS_REMOTE": str(self.remote), "AIDH_PARTS_CACHE": str(self.cache), **env}
        return subprocess.run(["bash", str(appt / script), *args], capture_output=True, text=True, timeout=300, env=full,
                              cwd=appt.parent)

    def export(self, **env: str) -> subprocess.CompletedProcess:
        _next_second()
        r = self._run("aidh_dev", "parts-to-drive.sh", **{"AIDH_PARTS_DIR": str(self.out), "AIDH_PARTS_N": str(N), **env})
        assert r.returncode == 0 and "✓ 조각 스냅숏" in r.stdout, r.stdout + r.stderr
        return r

    def fetch(self, *args: str) -> subprocess.CompletedProcess:
        return self._run("aidh_seed", "fetch-parts.sh", *args)

    def merge_parts(self, db: str, **env: str) -> subprocess.CompletedProcess:
        """update-all 처럼 AIDH_MERGE_PARTS=1(계획은 fetch-parts.sh 와 같은 캐시 자리) — 계획 파일을 직접 줄 수도 있다."""
        return self._run(db, "merge-from-drive.sh", **({"AIDH_MERGE_PARTS": "1"} | env))

    def merge_full(self, db: str) -> subprocess.CompletedProcess:
        r = subprocess.run([str(self.pg["bin"] / "pg_dump"), "-h", self.pg["sock"], "-p", _PORT, "-U", self.pg["user"], "-d", "aidh_dev",
                            "--no-owner", "--no-acl", "-Fp"], capture_output=True, timeout=120)
        assert r.returncode == 0, r.stderr
        dump = self.root / "aidh-db-dev.sql.gz"
        dump.write_bytes(gzip.compress(r.stdout))
        return self._run(db, "merge-from-drive.sh", AIDH_MERGE_DUMP=str(dump))

    def rollback(self, db: str, before: str) -> subprocess.CompletedProcess:
        return self._run(db, "rollback-parts.sh", before)

    def plan(self) -> dict[str, list[int]]:
        doc = json.loads((self.cache / "plan.json").read_text(encoding="utf-8"))
        return {t["name"]: [p["k"] for p in t["parts"]] for t in doc["tables"]}

    def sync(self, db: str) -> subprocess.CompletedProcess:
        """받기 → 병합 → 적기(update-all §3 의 조각 길과 같은 차례). 병합 결과를 돌려준다."""
        f = self.fetch()
        assert f.returncode == 0, f.stdout + f.stderr
        m = self.merge_parts(db)
        assert m.returncode == 0 and "✓ merge 완료(조각 길)" in m.stdout, m.stdout + m.stderr
        c = self.fetch("--commit")
        assert c.returncode == 0, c.stdout + c.stderr
        return m

    def downloads(self) -> list[str]:
        """Drive 에서 받은 것(copyto 의 원천 · copy 의 원천) — 지금까지 적힌 rclone 호출에서."""
        out = []
        for line in self.rclone_log.read_text(encoding="utf-8").splitlines() if self.rclone_log.exists() else []:
            verb, *args = line.split()
            if verb in ("copyto", "copy") and args and args[0].startswith(str(self.remote)):
                out.append(args[0][len(str(self.remote)):].lstrip("/") + ("" if verb == "copyto" else " (폴더)"))
        return out

    def manifests(self) -> list[dict]:
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.remote.glob("manifest-*.json"))]


@pytest.fixture()
def world(pg, tmp_path):
    if not shutil.which("rclone"):
        pytest.skip("rclone 없음 — 조각 스냅숏 시험 skip")
    w = _World(pg, tmp_path)
    w.seed()
    return w


def _before_dir(out: str) -> str:
    line = next(x for x in out.splitlines() if "되돌리기: rollback-parts.sh " in x)
    return line.split("되돌리기: rollback-parts.sh ", 1)[1].strip()


def _newer_line(out: str) -> str:
    return next((x for x in out.splitlines() if "content_hash 가 덤프와 다른 것" in x), "")


def test_조각_병합은_전체_덤프_병합과_같은_결과다(world, pg):
    full, parts = world.clone("aidh_full"), world.clone("aidh_parts")
    rf = world.merge_full(full)
    assert rf.returncode == 0 and "✓ merge 완료 —" in rf.stdout, rf.stdout + rf.stderr

    world.export()
    f = world.fetch()
    assert f.returncode == 0 and "✓ 조각 계획" in f.stdout, f.stdout + f.stderr
    # 처음은 목록이 없어(표식 없음) 표마다 조각 전부다.
    assert world.plan() == {t: list(range(N)) if t in ("records", "agent_records", "record_sections", "record_attachments") else [0]
                            for t in _KEYS}, world.plan()
    rp = world.merge_parts(parts)
    assert rp.returncode == 0 and "✓ merge 완료(조각 길)" in rp.stdout, rp.stdout + rp.stderr
    assert f"staging 에 조각 {4 * N + 7}개를 실었다" in rp.stdout, rp.stdout

    # 이 박스 몫 열을 뺀 모든 표 · 모든 줄이 같다.
    a, b = _state(pg, full), _state(pg, parts)
    assert a == b, next(f"{t}: 전체 {a[t]} ≠ 조각 {b[t]}" for t in _KEYS if a[t] != b[t])
    # 같다고 해서 아무 일도 안 한 것은 아니다 — 병합 규칙이 그대로 돌았다.
    rec = {line.split("|")[0]: line for line in b["records"]}
    assert "h2-dev" in rec["r02"] and "h3-box" in rec["r03"] and "m3-dev" in rec["r04"] and {"r06", "r07", "r20"} <= rec.keys(), b["records"]
    secs = b["record_sections"]
    assert any(s.startswith("r01|3|") for s in secs) and any("r02 1절(dev)" in s for s in secs), secs
    assert any(s.startswith("he-c|") for s in b["agents"]) and any("http://up1.dev" in s for s in b["mcp_upstreams"]), b
    assert "덤프 판으로 다시 지은 agent 3 · 지우기만 0" in rp.stdout and "덤프 판으로 다시 지은 agent 3 · 지우기만 0" in rf.stdout
    # '운영이 더 새롭다' 셈도 같다(처음은 조각 전부를 실었다).
    assert _newer_line(rp.stdout) and _newer_line(rp.stdout) == _newer_line(rf.stdout), (rp.stdout, rf.stdout)

    # 이 박스 몫 열 — 있던 줄은 두 길 모두 이 박스 것이다. 새 줄은 전체 길이 dev 값, 조각 길은 기본값이다(내지 않는 열 — context-notes AP-3).
    oa, ob = _own(pg, full), _own(pg, parts)
    new = {"rec:r06", "rec:r07", "up:up2"}
    assert {k: v for k, v in oa.items() if k not in new} == {k: v for k, v in ob.items() if k not in new}, (oa, ob)
    assert oa["rec:r01"] == "3/2026-10-25" and oa["up:up1"] == "down/3/2026-11-01", oa
    assert oa["rec:r06"] == "7/2026-10-21" and ob["rec:r06"] == "0/∅", (oa, ob)
    assert oa["up:up2"] == "ok/4/2026-10-30" and ob["up:up2"] == "∅/∅/∅", (oa, ob)


def test_바뀐_것이_없으면_목록만_받고_끝나며_조각은_같은_바이트다(world, pg):
    db = world.clone("aidh_b")
    world.export()
    world.sync(db)
    first = world.manifests()[-1]
    assert not list((world.cache / "files").iterdir()), "병합을 적은 뒤에는 캐시를 비운다"

    world.rclone_log.unlink()
    world.export()
    second = world.manifests()[-1]
    assert second["created"] != first["created"]
    assert world.downloads() == [f"manifest-{first['created']}.json", f"manifest-{second['created']}.json"], world.downloads()
    parts = lambda m: {t["name"]: [(p["k"], p["file"], p["sha256"]) for p in t["parts"]] for t in m["tables"]}  # noqa: E731
    assert parts(second) == parts(first)
    before = _xmins(pg, db)
    world.rclone_log.unlink()
    f = world.fetch()
    assert f.returncode == 10 and "조각 변화 없음" in f.stdout, f.stdout + f.stderr
    # Drive 에서 받은 것은 목록 하나뿐이다 — 내기 쪽 정리(목록이 KEEP 판보다 적을 때)도 조각 폴더를 받지 않는다.
    assert world.downloads() == [f"manifest-{second['created']}.json"], world.downloads()
    assert not list((world.cache / "files").iterdir()), "바뀐 것이 없으면 조각을 받지 않는다"
    assert _xmins(pg, db) == before

    # 다른 폴더에 다시 내도 조각 파일은 바이트째 같다(이름 = 내용 해시).
    other = world.root / "dev-parts-2"
    world.export(AIDH_PARTS_DIR=str(other), AIDH_PARTS_UPLOAD="0")
    names = {p.name for p in (world.out / "files").glob("*.bin.gz")} & {p.name for p in (other / "files").glob("*.bin.gz")}
    assert len(names) == 4 * N + 7, sorted(names)
    assert all((world.out / "files" / n).read_bytes() == (other / "files" / n).read_bytes() for n in names)


def test_레코드_하나가_바뀌면_그_조각만_받고_다른_줄은_다시_쓰지_않는다(world, pg):
    db = world.clone("aidh_b")
    world.export()
    world.sync(db)
    assert {_part("r05"), _part("r07"), _part("r08")} == {5}, "시험 전제 — r05 · r07 · r08 이 한 조각"
    before = _xmins(pg, db)

    world.sql("aidh_dev", "UPDATE records SET content = jsonb_build_object('h', 'h5-new'), content_hash = 'h5-new', "
                          "updated_at = '2026-11-01 00:00+00' WHERE id = 'r05';"
                          "UPDATE record_sections SET content_text = 'r05 2절(고침)' WHERE record_id = 'r05' AND section_id = '2';")
    world.export()
    f = world.fetch()
    assert f.returncode == 0, f.stdout + f.stderr
    assert world.plan() == {"records": [5], "record_sections": [5]}, world.plan()
    world.sync(db)

    after = _xmins(pg, db)
    changed = {k for k in after if after[k] != before.get(k)}
    assert changed == {"rec:r05", "sec:r05/2"}, changed      # 같은 조각의 r07 · r08 · r05 1절도 다시 쓰지 않았다
    got = world.sql(db, "SELECT content_hash || '|' || (SELECT content_text FROM record_sections WHERE record_id = 'r05' AND section_id = '2') "
                        "FROM records WHERE id = 'r05';").strip()
    assert got == "h5-new|r05 2절(고침)", got


def test_전문가가_바뀌면_표본을_함께_싣는다(world, pg):
    db = world.clone("aidh_b")
    world.export()
    world.sync(db)
    world.sql("aidh_dev", "UPDATE agents SET description = '고친 설명', updated_at = '2026-11-01 00:00+00' WHERE agent_type = 'he-y';")
    world.export()
    assert world.fetch().returncode == 0
    assert world.plan() == {"agents": [0], "agent_sample_embeddings": [0]}, world.plan()
    m = world.sync(db)
    assert "덤프 판으로 다시 지은 agent 0 · 지우기만 0" in m.stdout, m.stdout
    assert world.sql(db, "SELECT description FROM agents WHERE agent_type = 'he-y';").strip() == "고친 설명"

    # 표본만 바뀌면 표본만 싣는다 — 병합은 이 박스의 sample_queries 와 견준다.
    world.sql("aidh_dev", "UPDATE agent_sample_embeddings SET embedding_m3_model = 'dev2' WHERE agent_type = 'he-c';")
    world.export()
    assert world.fetch().returncode == 0
    assert world.plan() == {"agent_sample_embeddings": [0]}, world.plan()
    m = world.sync(db)
    assert "✓ agent_sample_embeddings" in m.stdout and "✓ agents" not in m.stdout, m.stdout


def test_dev_에만_있는_열이면_3이고_다시_받으면_같은_계획이다(world, pg):
    db = world.clone("aidh_b")
    world.export()
    world.sync(db)
    world.sql("aidh_dev", "ALTER TABLE records ADD COLUMN review_note text;")
    world.export()
    assert world.fetch().returncode == 0
    # 열 목록이 바뀐 표는 조각 전부다(이진 COPY 는 열 꼴이 같아야 실린다).
    assert world.plan() == {"records": list(range(N))}, world.plan()
    plan1 = (world.cache / "plan.json").read_text(encoding="utf-8")
    m = world.merge_parts(db, AIDH_MERGE_PARTS="0", AIDH_MERGE_PARTS_PLAN=str(world.cache / "plan.json"))   # 계획 파일을 직접 주는 꼴
    assert m.returncode == 3 and "⚠ records: dev 에만 있는 열 — review_note" in m.stdout, m.stdout + m.stderr
    # 적지 않았으니(update-all 은 0 일 때만 --commit) 다시 받으면 같은 계획이다.
    f = world.fetch()
    assert f.returncode == 0 and (world.cache / "plan.json").read_text(encoding="utf-8") == plan1, f.stdout + f.stderr


def test_되돌리기는_조각_병합_앞으로_돌린다(world, pg):
    db = world.clone("aidh_b")
    before = _state(pg, db, ids=True, own=True)
    world.export()
    assert world.fetch().returncode == 0
    m = world.merge_parts(db)
    assert m.returncode == 0, m.stdout + m.stderr
    assert _state(pg, db, ids=True, own=True) != before
    bdir = _before_dir(m.stdout)
    assert Path(bdir, "records.before.bin.gz").is_file() and Path(bdir, "agent_sample_embeddings.agents.csv.gz").is_file(), bdir

    r = world.rollback(db, bdir)
    assert r.returncode == 0 and "✓ 되돌림" in r.stdout, r.stdout + r.stderr
    after = _state(pg, db, ids=True, own=True)
    assert after == before, next(f"{t}: {before[t]} → {after[t]}" for t in _KEYS if after[t] != before[t])
    # 표본 순번은 뒤로 가지 않는다 — 이 박스의 다음 저장이 겹친 id 로 거절되지 않는다.
    world.sql(db, "BEGIN; DELETE FROM agent_sample_embeddings WHERE agent_type = 'he-x'; "
                  "INSERT INTO agent_sample_embeddings (agent_type, sample_text) VALUES ('he-x','x1'); COMMIT;")


def test_목록이_없으면_4_받은_조각의_해시가_다르면_멈춘다(world):
    f = world.fetch()
    assert f.returncode == 4 and "전체 덤프 길로" in f.stdout, f.stdout + f.stderr   # update-all 은 4 를 전체 병합으로 읽는다
    # 조각 폴더만 있고 목록이 없어도 4 다 — 폴더(files/)를 목록으로 읽지 않는다.
    (world.remote / "files").mkdir(parents=True)
    (world.remote / "files" / "orphan.bin.gz").write_bytes(b"x")
    f = world.fetch()
    assert f.returncode == 4, f.stdout + f.stderr
    world.export()
    m = world.manifests()[-1]
    bad = next(p["file"] for t in m["tables"] if t["name"] == "records" for p in t["parts"])
    (world.cache / "files").mkdir(parents=True, exist_ok=True)
    (world.cache / "files" / bad).write_bytes(b"not the part")
    f = world.fetch()
    assert f.returncode == 1 and "sha256 이 목록과 다르다" in f.stdout and bad in f.stderr, f.stdout + f.stderr


def test_Drive_에는_목록_KEEP_판과_그것이_가리키는_조각만_남는다(world):
    for i in range(3):
        world.sql("aidh_dev", f"UPDATE records SET content_hash = 'h1-v{i}', updated_at = '2026-11-0{i + 1} 00:00+00' WHERE id = 'r01';")
        world.export(AIDH_PARTS_KEEP="2")
    mans = world.manifests()
    assert len(mans) == 2, [m["created"] for m in mans]
    live = {m["schema"]["file"] for m in mans} | {p["file"] for m in mans for t in m["tables"] for p in t["parts"]}
    assert {p.name for p in (world.remote / "files").iterdir()} == live
    assert {p.name for p in (world.out / "files").iterdir() if p.suffix == ".gz"} == live
    assert len(list(world.out.glob("manifest-*.json"))) == 2


def test_내기는_한_시점이다(world, pg, monkeypatch, tmp_path):
    """조각마다 따로 여는 psql 이 한 시점을 빌린다 — 내는 도중 든 레코드는 레코드 목록에도 조각에도 없다(전체 덤프와 같은 일관성)."""
    spec = importlib.util.spec_from_file_location("aidh_parts_under_test", APPT / "aidh_parts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("AIDH_PSQL_CMD", json.dumps([str(pg["bin"] / "psql"), "-h", pg["sock"], "-p", _PORT, "-U", pg["user"],
                                                    "-d", "aidh_dev"]))
    out = tmp_path / "snap-parts"
    out.mkdir()
    with mod._Snapshot() as snap:
        world.sql("aidh_dev", _records([("r30", 30, "h30", "2026-10-10", None, 0, None)]))
        assert mod._psql_rows("SELECT count(*) FROM records", snap) == [["12"]]
        assert mod._psql_rows("SELECT count(*) FROM records") == [["13"]]      # 시점 밖에서는 보인다
        _, seen, _ = mod._write_part("SELECT id FROM public.records WHERE id = 'r30'", out, "a", snap)
        _, empty, _ = mod._write_part("SELECT id FROM public.records WHERE false", out, "b", snap)
        assert seen == empty
