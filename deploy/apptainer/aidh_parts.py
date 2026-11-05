#!/usr/bin/env python3
# AI Data Hub 조각 스냅숏 도우미 — dev 내기(표를 조각으로 이진 COPY · 내용 해시 이름 · 목록) · cae00 받을 조각 계획 · 조각 캐시 정리
"""조각 스냅숏(HWAXPortal docs/aidh-parts-sync) — 표준 라이브러리만 쓴다(dev · cae00 의 시스템 python3).

  aidh_parts.py export --out DIR [--n 64]               # dev — DIR/files/<표>.<조각>.<해시12>.bin.gz · DIR/manifest-<시각>.json
  aidh_parts.py plan --manifest NEW [--mark OLD] --out PLAN.json [--cache DIR]
                                                         # cae00 — 지난 목록과 해시가 다른 조각만. 다른 것이 없으면 끝 코드 10
  aidh_parts.py verify --plan PLAN.json --cache DIR     # 받은 조각의 sha256 대조
  aidh_parts.py gc --dir DIR --keep-manifests N          # 남은 목록이 가리키지 않는 조각 파일을 지운다
  aidh_parts.py referenced MANIFEST...                   # 목록들이 가리키는 파일 이름(한 줄에 하나)

psql · pg_dump 는 환경의 AIDH_PSQL_CMD · AIDH_PGDUMP_CMD(JSON 목록 — 접속 인자까지)로 부른다. 같은 데이터면 같은 바이트가 나오게
ORDER BY 를 키로 두고 gzip 의 시각 · 이름을 비운다(mtime=0) — 안 바뀐 조각은 이름(해시)이 같아 다시 올리지 않는다. 내기는 한 시점이다
(pg_export_snapshot — 조각마다 따로 여는 psql 이 그 시점을 빌린다) — 전체 덤프처럼 표 사이 · 조각 사이가 맞는다.
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import os
import re
import select
import subprocess
import sys
import threading
from pathlib import Path

FORMAT = 1
# 표 · 조각 열(None = 한 조각) · 차례(ORDER BY — 같은 데이터면 같은 바이트) · 내지 않는 열.
# 내지 않는 열 — 그 박스 몫이라 dev 값이 조각 해시를 매일 흔드는 것(조회 통계 · 상류 상태 점검)과 박스마다 따로 매기는 대리 id(병합도 쓰지 않는다).
# 차례는 병합(merge-from-drive.sh try_merge)과 같다 — 부모 다음 자식.
TABLES = [
    ("org_teams", None, "code", ()),
    ("org_groups", None, "team_code, code", ()),
    ("doc_types", None, "code", ()),
    ("agents", None, "agent_type", ()),
    ("records", "id", "id", ("read_count", "last_accessed_at")),
    ("agent_records", "record_id", "agent_type, record_id", ()),
    ("record_sections", "record_id", "record_id, section_id", ("id",)),
    ("record_attachments", "record_id", "id", ()),
    ("agent_sample_embeddings", None,
     "agent_type, sample_text, created_at, embedding_m3_at, embedding_m3_model, "
     "md5(coalesce(embedding::text, '') || coalesce(embedding_m3::text, ''))", ("id",)),
    ("external_id_map", None, "source, external_id", ("id",)),
    ("mcp_upstreams", None, "alias", ("last_health_check_at", "last_health_status", "last_tool_count")),
]
# 레코드에 딸린 표 — 레코드와 같은 조각 번호(record_id)로 나뉘고, 병합이 레코드를 이긴 쪽을 따른다.
CHILDREN = ("agent_records", "record_sections", "record_attachments")
GZ_LEVEL = 6
CHUNK = 1 << 20


def _cmd(env: str) -> list[str]:
    raw = os.environ.get(env, "").strip()
    if not raw:
        sys.exit(f"{env} 가 비었다 — 접속까지 든 명령 목록(JSON)을 준다")
    return json.loads(raw)


_SNAP_RE = re.compile(r"^[0-9A-F]+-[0-9A-F]+-[0-9]+$")


class _Snapshot:
    """한 시점을 쥔 세션 — 끝날 때까지 트랜잭션을 열어 둔다. 조각마다 따로 여는 psql 이 이 시점(SET TRANSACTION SNAPSHOT)을 빌려 표
    사이 · 조각 사이가 맞는다. 따로 보면 내는 도중 dev 에 새로 든 레코드의 자식(external_id_map 처럼 한 조각인 표)이 실려 운영에서
    외래키에 걸리고, 레코드와 그 절이 서로 다른 판으로 실린다."""

    def __enter__(self) -> "_Snapshot":
        self.proc = subprocess.Popen([*_cmd("AIDH_PSQL_CMD"), "-v", "ON_ERROR_STOP=1", "-qtA"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\nSELECT pg_export_snapshot();\n")
        self.proc.stdin.flush()
        # psql 은 질의 결과마다 표준출력을 비운다(14 · 16 실측) — 트랜잭션을 연 채로 한 줄을 읽는다.
        ready, _, _ = select.select([self.proc.stdout], [], [], 120)
        line = self.proc.stdout.readline().strip() if ready else ""
        if not _SNAP_RE.match(line):
            self.proc.kill()
            _, err = self.proc.communicate(timeout=30)
            raise RuntimeError(f"스냅숏을 못 받았다({line!r}): {(err or '').strip()[-400:]}")
        self.id = line
        return self

    def __exit__(self, *exc) -> None:
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write("COMMIT;\n")
            self.proc.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
        self.proc.wait(timeout=60)

    def script(self, body: str) -> str:
        return f"BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\nSET TRANSACTION SNAPSHOT '{self.id}';\n{body}\nCOMMIT;\n"


def _psql_rows(sql: str, snap: _Snapshot | None = None) -> list[list[str]]:
    # 문장은 표준입력으로 — 인자 하나의 상한(리눅스 128 KiB)에 걸리지 않는다. -q 라 BEGIN · SET · COMMIT 꼬리표는 찍히지 않는다.
    body = sql.rstrip().rstrip(";") + ";"
    r = subprocess.run([*_cmd("AIDH_PSQL_CMD"), "-v", "ON_ERROR_STOP=1", "-qtA", "-F", "\t"],
                       input=snap.script(body) if snap else body, capture_output=True, text=True, timeout=600)
    if r.returncode:
        raise RuntimeError(f"psql 실패: {r.stderr.strip()[-400:]}")
    return [ln.split("\t") for ln in r.stdout.splitlines() if ln]


def part_of(key: str, n: int) -> int:
    """조각 번호 — PostgreSQL 판에 묶이지 않게 md5 로 매긴다(판을 올려도 조각이 흔들리지 않는다)."""
    return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % n


def _write_part(sql: str, dest_dir: Path, stem: str, snap: _Snapshot) -> tuple[str, str, int]:
    """COPY … TO STDOUT 을 gzip(시각 0)으로 받아 해시를 단 이름으로 둔다 → (이름, sha256, 바이트). 같은 이름이 있으면 그것을 쓴다."""
    tmp = dest_dir / f".{stem}.tmp"
    h = hashlib.sha256()
    size = 0
    # 표준출력은 데이터뿐이다(-q). 문장(조각의 키 목록이 길다)은 표준입력으로 — 쓰기는 따로 돌려 출력이 차도 서로 기다리지 않는다.
    proc = subprocess.Popen([*_cmd("AIDH_PSQL_CMD"), "-v", "ON_ERROR_STOP=1", "-q"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    script = snap.script(f"COPY ({sql}) TO STDOUT (FORMAT binary);").encode("utf-8")

    def _feed() -> None:
        try:
            assert proc.stdin is not None
            proc.stdin.write(script)
            proc.stdin.close()
        except BrokenPipeError:
            pass

    feeder = threading.Thread(target=_feed, daemon=True)
    feeder.start()
    with open(tmp, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=GZ_LEVEL, mtime=0) as gz:
            assert proc.stdout is not None
            while True:
                block = proc.stdout.read(CHUNK)
                if not block:
                    break
                gz.write(block)
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    feeder.join(timeout=60)
    if proc.wait():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{stem} COPY 실패: {err.strip()[-400:]}")
    with open(tmp, "rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            h.update(block)
            size += len(block)
    digest = h.hexdigest()
    name = f"{stem}.{digest[:12]}.bin.gz"
    final = dest_dir / name
    if final.exists():
        tmp.unlink()
    else:
        tmp.rename(final)
    return name, digest, size


def _schema(dest_dir: Path, snap: _Snapshot) -> tuple[str, str]:
    """색인 · 제약을 뺀 스키마(pre-data) — cae00 이 빈 스테이징을 이것으로 짓는다(색인을 짓지 않아 싣기가 빠르다). pg_dump 16.10 · 17.6
    부터는 덤프마다 무작위 열쇠(\\restrict)가 들어 이 파일은 내기마다 이름이 바뀐다 — 받을 조각을 고르는 데는 쓰지 않는다(plan)."""
    r = subprocess.run([*_cmd("AIDH_PGDUMP_CMD"), "--schema-only", "--section=pre-data", "--no-owner", "--no-acl",
                        f"--snapshot={snap.id}"], capture_output=True, timeout=600)
    if r.returncode:
        raise RuntimeError(f"pg_dump 스키마 실패: {r.stderr.decode('utf-8', 'replace').strip()[-400:]}")
    data = gzip.compress(r.stdout, compresslevel=GZ_LEVEL, mtime=0)
    digest = hashlib.sha256(data).hexdigest()
    name = f"schema.{digest[:12]}.sql.gz"
    if not (dest_dir / name).exists():
        (dest_dir / name).write_bytes(data)
    return name, digest


def export(out: Path, n: int, stamp: str) -> Path:
    files = out / "files"
    files.mkdir(parents=True, exist_ok=True)
    with _Snapshot() as snap:
        return _export(out, files, n, stamp, snap)


def _export(out: Path, files: Path, n: int, stamp: str, snap: _Snapshot) -> Path:
    have = {r[0] for r in _psql_rows("SELECT tablename FROM pg_tables WHERE schemaname = 'public'", snap)}
    record_ids = [r[0] for r in _psql_rows("SELECT id FROM records ORDER BY id", snap)] if "records" in have else []
    parts_ids: dict[int, list[str]] = {}
    for rid in record_ids:
        parts_ids.setdefault(part_of(rid, n), []).append(rid)
    schema_name, schema_sha = _schema(files, snap)
    tables = []
    for name, part_col, order, skip in TABLES:
        if name not in have:
            continue
        cols = _psql_rows(
            "SELECT a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            f"WHERE a.attrelid = 'public.{name}'::regclass AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = '' "
            "ORDER BY a.attnum", snap)
        cols = [c for c in cols if c[0] not in skip]
        collist = ", ".join(f'"{c[0]}"' for c in cols)
        entry = {"name": name, "columns": [c[0] for c in cols], "types": [c[1] for c in cols],
                 "part_by": part_col, "order": order, "skipped": list(skip), "parts": []}
        if part_col is None:
            fname, sha, size = _write_part(f"SELECT {collist} FROM public.{name} ORDER BY {order}", files, f"{name}.0", snap)
            entry["parts"].append({"k": 0, "file": fname, "sha256": sha, "bytes": size})
        else:
            for k in range(n):
                ids = parts_ids.get(k, [])
                arr = "ARRAY[" + ", ".join("'" + i.replace("'", "''") + "'" for i in ids) + "]::text[]" if ids else "ARRAY[]::text[]"
                sql = f"SELECT {collist} FROM public.{name} WHERE {part_col}::text = ANY({arr}) ORDER BY {order}"
                fname, sha, size = _write_part(sql, files, f"{name}.{k}", snap)
                entry["parts"].append({"k": k, "file": fname, "sha256": sha, "bytes": size})
        tables.append(entry)
    manifest = {"format": FORMAT, "created": stamp, "n": n, "schema": {"file": schema_name, "sha256": schema_sha},
                "tables": tables}
    path = out / f"manifest-{stamp}.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _load(path: Path | None) -> dict | None:
    if not path or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def plan(new: dict, old: dict | None) -> dict:
    """지난 목록과 견줘 받을 조각만 → 계획. 표의 열 목록이 바뀌었으면 그 표는 조각 전부다(이진 COPY 는 열 꼴이 같아야 실린다).
    표본(agent_sample_embeddings)은 전문가(agents) 조각이 바뀌어도 함께 싣는다 — 병합이 sample_queries 와 견준다."""
    old_tables = {t["name"]: t for t in (old or {}).get("tables", [])}
    out = []
    for t in new["tables"]:
        prev = old_tables.get(t["name"])
        same_cols = prev is not None and prev.get("columns") == t["columns"] and prev.get("types") == t["types"]
        prev_sha = {p["k"]: p["sha256"] for p in prev["parts"]} if same_cols else {}
        need = [p for p in t["parts"] if prev_sha.get(p["k"]) != p["sha256"]]
        if need:
            out.append({"name": t["name"], "columns": t["columns"], "parts": need})
    # 레코드에 딸린 표의 조각을 실으면 같은 k 의 레코드 조각도 싣는다 — 병합이 '이 박스 판이 이긴 레코드' 를 dev 의 updated_at 과 견줘 가린다
    # (merge-from-drive.sh merge_table $7). 안 바뀐 레코드 조각이어도 그렇다 — 레코드 병합은 시각 조건이라 다시 쓰지 않는다.
    child_k = {p["k"] for t in out if t["name"] in CHILDREN for p in t["parts"]}
    rec_new = next((t for t in new["tables"] if t["name"] == "records"), None)
    if child_k and rec_new:
        rec_out = next((t for t in out if t["name"] == "records"), None)
        if rec_out is None:
            rec_out = {"name": "records", "columns": rec_new["columns"], "parts": []}
            out.append(rec_out)
        have = {p["k"] for p in rec_out["parts"]}
        rec_out["parts"] = sorted(rec_out["parts"] + [p for p in rec_new["parts"] if p["k"] in child_k - have], key=lambda p: p["k"])
    names = {t["name"] for t in out}
    if "agents" in names and "agent_sample_embeddings" not in names:
        sample = next((t for t in new["tables"] if t["name"] == "agent_sample_embeddings"), None)
        if sample:
            out.append({"name": sample["name"], "columns": sample["columns"], "parts": sample["parts"]})
    order = [name for name, *_ in TABLES]
    out.sort(key=lambda t: order.index(t["name"]) if t["name"] in order else len(order))
    return {"manifest": new["created"], "schema": new["schema"], "tables": out}


def verify(plan_doc: dict, cache: Path) -> list[str]:
    bad = []
    items = [plan_doc["schema"]] + [p for t in plan_doc["tables"] for p in t["parts"]]
    for p in items:
        f = cache / p["file"]
        if not f.is_file():
            bad.append(f"{p['file']} 없음")
            continue
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for block in iter(lambda: fh.read(CHUNK), b""):
                h.update(block)
        if h.hexdigest() != p["sha256"]:
            bad.append(f"{p['file']} sha256 다름")
    return bad


def referenced(manifests: list[dict]) -> set[str]:
    out = set()
    for m in manifests:
        out.add(m["schema"]["file"])
        out.update(p["file"] for t in m["tables"] for p in t["parts"])
    return out


def gc(d: Path, keep: int) -> list[str]:
    """DIR 의 목록을 시각 차례로 keep 판만 두고, 남은 목록이 가리키지 않는 조각 파일을 지운다 → 지운 이름."""
    mans = sorted(d.glob("manifest-*.json"))
    drop_m, keep_m = mans[:-keep] if keep else mans, mans[-keep:] if keep else []
    for m in drop_m:
        m.unlink()
    live = referenced([json.loads(m.read_text(encoding="utf-8")) for m in keep_m])
    files = d / "files" if (d / "files").is_dir() else d
    gone = []
    for f in files.iterdir():
        if f.is_file() and (f.name.endswith(".bin.gz") or f.name.endswith(".sql.gz")) and f.name not in live:
            f.unlink()
            gone.append(f.name)
    return sorted(gone)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AIDataHub 조각 스냅숏 도우미")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--out", type=Path, required=True)
    e.add_argument("--n", type=int, default=64)
    e.add_argument("--stamp", default=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%SZ"))
    p = sub.add_parser("plan")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--mark", type=Path)
    p.add_argument("--out", type=Path, required=True)
    v = sub.add_parser("verify")
    v.add_argument("--plan", type=Path, required=True)
    v.add_argument("--cache", type=Path, required=True)
    g = sub.add_parser("gc")
    g.add_argument("--dir", type=Path, required=True)
    g.add_argument("--keep-manifests", type=int, default=3)
    r = sub.add_parser("referenced")
    r.add_argument("manifests", type=Path, nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "export":
        print(export(a.out, a.n, a.stamp))
        return 0
    if a.cmd == "plan":
        new = _load(a.manifest)
        if new is None or new.get("format") != FORMAT:
            print(f"목록을 못 읽었다(또는 모르는 판): {a.manifest}", file=sys.stderr)
            return 2
        doc = plan(new, _load(a.mark))
        a.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        n_parts = sum(len(t["parts"]) for t in doc["tables"])
        print(f"받을 조각 {n_parts} — " + (" · ".join(f"{t['name']} {len(t['parts'])}" for t in doc["tables"]) or "없음"))
        return 10 if n_parts == 0 else 0
    if a.cmd == "verify":
        bad = verify(json.loads(a.plan.read_text(encoding="utf-8")), a.cache)
        for b in bad:
            print(b, file=sys.stderr)
        return 1 if bad else 0
    if a.cmd == "gc":
        for name in gc(a.dir, a.keep_manifests):
            print(name)
        return 0
    if a.cmd == "referenced":
        for name in sorted(referenced([json.loads(m.read_text(encoding="utf-8")) for m in a.manifests])):
            print(name)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
