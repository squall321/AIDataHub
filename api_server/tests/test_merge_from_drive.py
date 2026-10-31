# merge-from-drive.sh — 운영에 없는 열을 알리고(3), m3 슬롯은 updated_at 과 상관없이 비어 있을 때 채우는지(10-17 cae00 리뷰 H1 · H1-b), 표본은 id 로 덮지 않는지(10-30 AD-21 후속), '운영이 더 새롭다' 며 건너뛴 레코드 중 내용이 다른 수와 그중 조회로 올라간 수를 끝 요약에 알리는지(FU-D7)
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "apptainer" / "merge-from-drive.sh"

pytestmark = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("gzip") and SCRIPT.is_file()),
    reason="bash·gzip 과 deploy/apptainer/merge-from-drive.sh 가 있어야 돈다",
)

# 실제 _common.sh 대신 읽히는 대역 — 실 .env 를 읽지 않는다.
_COMMON_STUB = """\
load_env() { :; }
POSTGRES_PORT=1
POSTGRES_USER=stub
POSTGRES_DB=aidh
INST_POSTGRES=stubpg
"""

# 대역 apptainer — `exec instance://… psql|pg_dump|rm …` 만 받는다. psql 은 진짜처럼 -tA 한 칸 한 줄로 답하고,
# -c 없이 표준입력으로 온 SQL(병합 세션)은 그대로 적는다 — 세션에 '운영이 더 새롭다' 셈이 있으면 FAKE_NEWER_DIFF(기본 0 — 조회 몫까지
# 세면 '7|2' 처럼 -tA 의 한 줄)를 답한다.
# 열 목록은 FAKE_SCHEMA(JSON: prod/stage → 표 → 열)에서 읽는다.
_FAKE_APPTAINER = r'''
import json, os, re, sys

args = sys.argv[1:]
assert args[:1] == ["exec"] and args[1].startswith("instance://"), args
prog, rest = args[2], args[3:]
if prog in ("rm", "pg_dump"):
    sys.exit(0)
assert prog == "psql", prog
db = rest[rest.index("-d") + 1] if "-d" in rest else ""
sql = rest[rest.index("-c") + 1] if "-c" in rest else None
schema = json.load(open(os.environ["FAKE_SCHEMA"]))
side = "stage" if db.endswith("_mergestage") else "prod"
with open(os.environ["FAKE_LOG"], "a") as log:
    if sql is None:
        body = sys.stdin.read()
        log.write(json.dumps({"db": db, "stdin": body}) + "\n")
        if "FROM _mg m JOIN public." in body:
            print(os.environ.get("FAKE_NEWER_DIFF", "0"))
        m = re.search(r"INSERT INTO public\.(\w+)", body)
        if m and m.group(1) == os.environ.get("FAKE_FAIL_TABLE"):
            sys.exit(3)
        sys.exit(0)
    log.write(json.dumps({"db": db, "sql": sql}) + "\n")


def table():
    return (re.search(r"table_name='(\w+)'", sql) or re.search(r"public\.(\w+)", sql)).group(1)


def cols(side_):
    return schema[side_].get(table(), [])


if db == "postgres" or sql.startswith(("CREATE EXTENSION", "COPY ")):
    sys.exit(0)
m = re.search(r"to_regclass\('public\.(\w+)'\)", sql)
if m:
    print("t" if m.group(1) in schema[side] else "f")
elif "string_agg(quote_ident(column_name)||'=EXCLUDED.'" in sql:
    skip = re.search(r"string_to_array\('([^']*)'", sql).group(1).split(",")
    print(", ".join(f"{c}=EXCLUDED.{c}" for c in cols("prod") if c not in skip))
elif "string_agg(quote_ident(column_name),','" in sql:
    drop = re.search(r"string_to_array\('([^']*)'", sql).group(1).split(",")
    print(",".join(c for c in cols("prod") if c not in drop))
elif sql.startswith("SELECT column_name FROM information_schema.columns"):
    print("\n".join(cols(side)))
elif sql.startswith("SELECT count(*) FROM"):
    print("3")
elif sql.startswith("SELECT pg_get_serial_sequence"):
    print(f"public.{table()}_id_seq")
elif sql.startswith("WITH want AS"):
    pass  # 표본 색인이 sample_queries 와 다른 agent 없음
else:
    print("unexpected sql: " + sql, file=sys.stderr)
    sys.exit(9)
'''

_RECORDS = ["id", "title", "content_hash", "updated_at", "last_accessed_at", "signature_embedding", "embedded_at"]
_RECORDS_M3 = ["signature_embedding_m3", "signature_embedding_m3_at", "signature_embedding_m3_model"]
_SECTIONS = ["id", "record_id", "section_id", "content_text", "embedding"]
_SECTIONS_M3 = ["embedding_m3", "embedding_m3_at", "embedding_m3_model"]


@pytest.fixture()
def merge(tmp_path):
    appt = tmp_path / "apptainer-dir"
    appt.mkdir()
    body = SCRIPT.read_text(encoding="utf-8")
    # 박힌 실경로(로그 · 롤백 스냅샷)를 임시 폴더로 — 시험이 박스의 /tmp · /data 에 쓰지 않게.
    for real, fake in (("/tmp/aidh-merge-load.log", tmp_path / "load.log"), ("/tmp/aidh-merge.log", tmp_path / "merge.log"),
                       ("/data/backups/aidh/", f"{tmp_path}/backups/")):
        body = body.replace(real, str(fake))
    assert "/tmp/aidh-merge" not in body and "/data/" not in body, "새 실경로가 생겼다 — 위 치환에 더한다"
    (appt / "merge-from-drive.sh").write_text(body, encoding="utf-8")
    (appt / "_common.sh").write_text(_COMMON_STUB, encoding="utf-8")
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    (fakebin / "apptainer").write_text(f"#!{sys.executable}\n{_FAKE_APPTAINER}", encoding="utf-8")
    (fakebin / "apptainer").chmod(0o755)
    dump = tmp_path / "aidh-db-test.sql.gz"
    subprocess.run(["gzip", "-c"], input=b"-- dump\n", stdout=dump.open("wb"), check=True)
    log = tmp_path / "psql.jsonl"

    def run(prod: dict, stage: dict, **env):
        (tmp_path / "schema.json").write_text(json.dumps({"prod": prod, "stage": stage}))
        log.write_text("")
        full = {"PATH": f"{fakebin}:/usr/bin:/bin", "HOME": str(tmp_path), "AIDH_MERGE_DUMP": str(dump),
                "FAKE_SCHEMA": str(tmp_path / "schema.json"), "FAKE_LOG": str(log), **env}
        r = subprocess.run(["bash", str(appt / "merge-from-drive.sh")], capture_output=True, text=True,
                           timeout=120, env=full, cwd=tmp_path)
        calls = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
        sessions = {}
        for c in calls:
            m = "stdin" in c and re.search(r"INSERT INTO public\.(\w+)", c["stdin"])
            if m:
                sessions[m.group(1)] = c["stdin"]
        return r, sessions
    return run


def test_운영에_없는_열은_이름째_알리고_3으로_끝난다(merge):
    """10-17 cae00 — 0032 가 잠금으로 늦은 채 병합이 돌아 m3 열이 ✓ 로 빠졌다. 다음 update-all 은 같은 덤프라 건너뛰었다."""
    r, sessions = merge({"records": _RECORDS}, {"records": _RECORDS + _RECORDS_M3})
    assert r.returncode == 3, r.stdout + r.stderr
    warn = next(line for line in r.stdout.splitlines() if "⚠ records:" in line)
    assert all(c in warn for c in _RECORDS_M3), warn
    assert "✓ merge 완료" not in r.stdout and "merge-from-drive.sh 를 한 번 더" in r.stdout
    assert "INSERT INTO public.records" in sessions["records"], "있는 열은 그대로 옮긴다"


def test_m3_슬롯은_운영이_비었으면_updated_at_과_상관없이_채운다(merge):
    """예전 병합이 운영 updated_at 을 병합 시각으로 올려 둬 dev 의 레코드 m3 시그니처 59,032건이 한 건도 오지 않았다(H1-b)."""
    r, sessions = merge({"records": _RECORDS + _RECORDS_M3}, {"records": _RECORDS + _RECORDS_M3})
    assert r.returncode == 0 and "✓ merge 완료" in r.stdout, r.stdout + r.stderr
    s = sessions["records"]
    assert s.startswith("SET aidh.keep_updated_at = 'on';"), "병합이 운영 updated_at 을 병합 시각으로 올린다"
    assert "WHERE records.updated_at IS NULL OR EXCLUDED.updated_at > records.updated_at" in s, "같은 행을 매번 다시 쓴다"
    fill = ("UPDATE public.records AS r SET signature_embedding_m3 = m.signature_embedding_m3, "
            "signature_embedding_m3_at = m.signature_embedding_m3_at, "
            "signature_embedding_m3_model = m.signature_embedding_m3_model FROM _mg m\n"
            " WHERE r.id = m.id AND r.title IS NOT DISTINCT FROM m.title AND r.content_hash IS NOT DISTINCT FROM m.content_hash"
            " AND r.signature_embedding_m3 IS NULL AND m.signature_embedding_m3 IS NOT NULL;")
    assert fill in s, s
    assert s.index("INSERT INTO public.records") < s.index(fill) < s.index("DROP TABLE _mg;")


def test_같아야_할_열을_안_준_표는_슬롯을_따로_채우지_않는다(merge):
    """채움은 그 표의 시그니처 재료가 양쪽에서 같을 때만 맞다 — 무엇이 재료인지 모르는 표는 채우지 않는다(운영에서 고친 행에 dev 의
    옛 내용 벡터를 넣으면 그 박스의 스윕이 다시 계산하지 않는다)."""
    cols = ["agent_type", "updated_at", "desc_m3", "desc_m3_at", "desc_m3_model"]
    r, sessions = merge({"agents": cols}, {"agents": cols})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "UPDATE public.agents" not in sessions["agents"], sessions["agents"]


def test_updated_at_조건이_없는_표는_슬롯을_따로_채우지_않는다(merge):
    """절 · 표본은 조건 없이 전부 덮으므로 m3 도 이미 온다 — 덧붙인 UPDATE 는 같은 행을 한 번 더 쓰기만 한다."""
    r, sessions = merge({"record_sections": _SECTIONS + _SECTIONS_M3}, {"record_sections": _SECTIONS + _SECTIONS_M3})
    assert r.returncode == 0, r.stdout + r.stderr
    s = sessions["record_sections"]
    assert "UPDATE public.record_sections" not in s and " WHERE " not in s.split("ON CONFLICT", 1)[1], s


def test_표본은_id_로_덮지_않고_agent_단위로_맞춘다(merge):
    """dev 의 대리 키(id)로 맞추면(ON CONFLICT (id)) 이 박스의 다른 agent 행을 덮고 순번이 뒤처져 다음 표본 저장이 겹친 id 로
    거절된다(10-30 AD-21 후속). 동작은 실 PostgreSQL 시험(test_merge_samples_pg, opt-in)이 본다 — 여기서는 기본 시험으로 꼴만 지킨다."""
    agents = ["agent_type", "sample_queries", "updated_at"]
    samples = ["id", "agent_type", "sample_text", "embedding", "created_at"]
    r, sessions = merge({"agents": agents, "agent_sample_embeddings": samples},
                        {"agents": agents, "agent_sample_embeddings": samples})
    assert r.returncode == 0, r.stdout + r.stderr
    s = sessions["agent_sample_embeddings"]
    assert "ON CONFLICT" not in s, s
    assert "setval('public.agent_sample_embeddings_id_seq'" in s, s
    assert "INSERT INTO public.agent_sample_embeddings (agent_type,sample_text,embedding,created_at) SELECT" in s, s


def test_병합_실패는_열_빠짐보다_먼저_1로_끝난다(merge):
    r, _ = merge({"records": _RECORDS, "agents": ["agent_type", "updated_at"]},
                 {"records": _RECORDS + _RECORDS_M3, "agents": ["agent_type", "updated_at"]},
                 FAKE_FAIL_TABLE="agents")
    assert r.returncode == 1 and "merge 일부 실패: agents" in r.stdout, r.stdout + r.stderr


_NEWER_COUNT = "FROM _mg m JOIN public."   # '운영이 더 새롭다' 셈에만 있는 꼴
# 조회 몫은 updated_at 과 last_accessed_at 이 양쪽 1초 안인 것 — 한쪽(updated_at - last_accessed_at < 1초)만 보면 2026-10-31 고침 뒤의
# 조회가 편집된 행의 last_accessed_at 만 앞으로 밀어 그 행을 조회로 센다.
_NEWER_DIFF = ("SELECT count(*), count(*) FILTER (WHERE r.updated_at - r.last_accessed_at < interval '1 second'"
               " AND r.last_accessed_at - r.updated_at < interval '1 second')"
               " FROM _mg m JOIN public.records r ON r.id = m.id"
               " WHERE r.updated_at > m.updated_at AND r.content_hash IS DISTINCT FROM m.content_hash;")


def test_운영이_더_새롭다며_건너뛴_레코드_중_내용이_다른_수를_끝_요약에_한_줄로_알린다(merge):
    """예전 병합이 운영 updated_at 을 병합 시각으로 올려 둔 레코드, 그리고 이 박스에서 예전에 조회된 레코드(2026-10-31 고침 전에는
    조회 수 갱신이 updated_at 도 올렸다)는 dev 에서 다시 바뀌어도 '운영이 더 새롭다' 로 건너뛰어진다(HWAXPortal followups-1017 FU-D7).
    고치지 않고 크기를 세며 그중 updated_at 을 마지막으로 올린 것이 조회인 수를 따로 싣는다 — upsert 앞의 읽기 한 줄이고 upsert 의
    조건은 그대로다. 셈의 값은 실 PostgreSQL 시험(test_merge_samples_pg, opt-in)이 본다 — 여기서는 꼴과 끝 요약의 자리를 지킨다."""
    agents = ["agent_type", "updated_at"]
    r, sessions = merge({"records": _RECORDS, "agents": agents}, {"records": _RECORDS, "agents": agents},
                        FAKE_NEWER_DIFF="7|2")
    assert r.returncode == 0 and "✓ merge 완료" in r.stdout, r.stdout + r.stderr
    s = sessions["records"]
    assert _NEWER_DIFF in s, s
    assert s.index("COPY _mg") < s.index(_NEWER_DIFF) < s.index("INSERT INTO public.records"), "upsert 가 행을 바꾸기 전에 센다"
    assert "WHERE records.updated_at IS NULL OR EXCLUDED.updated_at > records.updated_at" in s, "병합 판단이 바뀌었다"
    assert _NEWER_COUNT not in sessions["agents"], "content_hash 가 없는 표는 세지 않는다"
    lines = r.stdout.splitlines()
    summary = [i for i, line in enumerate(lines) if "content_hash 가 덤프와 다른 것 — records 7건(그중 조회로 올라간 것 2건)" in line]
    assert len(summary) == 1, r.stdout
    assert summary[0] == len(lines) - 2 and lines[-1].startswith("✓ merge 완료"), "끝 요약(판정 바로 앞)에 한 줄"
    assert "이 박스의 예전 조회" in lines[summary[0]], "까닭에 조회를 적는다 — 편집 · 예전 병합만이 아니다"
    assert "고친 판의 조회는 올리지 않는다" in lines[summary[0]], "새 조회는 updated_at 을 올리지 않는다고 적는다"


def test_운영_표에_last_accessed_at_이_없으면_조회_몫은_세지_않고_병합은_그대로다(merge):
    """조회 몫은 운영 표의 last_accessed_at 으로 가른다. 그 열이 없는 표에서 부르면 ON_ERROR_STOP 으로 그 표의 병합이 멈춘다 —
    그때는 전체 수만 센다."""
    no_seen = [c for c in _RECORDS if c != "last_accessed_at"]
    r, sessions = merge({"records": no_seen}, {"records": no_seen}, FAKE_NEWER_DIFF="5")
    assert r.returncode == 0 and "✓ merge 완료" in r.stdout, r.stdout + r.stderr
    s = sessions["records"]
    assert ("SELECT count(*) FROM _mg m JOIN public.records r ON r.id = m.id"
            " WHERE r.updated_at > m.updated_at AND r.content_hash IS DISTINCT FROM m.content_hash;") in s, s
    assert "last_accessed_at" not in s, s
    line = next(x for x in r.stdout.splitlines() if "content_hash 가 덤프와 다른 것" in x)
    assert "— records 5건." in line and "그중" not in line, line


def test_내용_해시가_한쪽에만_있으면_세지_않고_요약도_없다(merge):
    """셈은 양쪽 덤프에 content_hash 가 있을 때만 뜻이 있다 — 한쪽에 없으면 세지 않고 요약 줄도 찍지 않는다."""
    no_hash = [c for c in _RECORDS if c != "content_hash"]
    r, sessions = merge({"records": no_hash}, {"records": _RECORDS})
    assert r.returncode == 3, r.stdout + r.stderr       # dev 에만 있는 열(content_hash) — 그대로 알린다
    assert _NEWER_COUNT not in sessions["records"], sessions["records"]
    assert "content_hash 가 덤프와 다른" not in r.stdout, r.stdout
