"""AI Data Hub — 새 문서 적재 (ingest) 데모.

Agent 가 외부에서 문서 파일을 받아 본 시스템에 적재하는 흐름.
다음 두 경로 중 선택:

  (A) 서버에 multipart 업로드 — 서버가 변환 + 적재 (가장 간단)
  (B) 클라이언트에서 변환기 CLI 실행 → JSON 만 서버에 POST (서버 부담 적음)

본 예제는 (A) 경로 — 가장 흔한 케이스.
"""
from __future__ import annotations
import argparse
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
import json
import mimetypes

# === API URL ================================================================
# 우선순위: AIDH_API_URL env > 하드코딩. 일괄 갱신은 ../update_url.py.
# ============================================================================
BASE = os.environ.get("AIDH_API_URL", "http://110.15.177.125:8000").rstrip("/")
API_KEY: str | None = os.environ.get("AIDH_API_KEY")


def upload_and_ingest(
    file_path: str,
    division: str,
    team: str,
    year: int,
    seq: int,
    *,
    agents: list[str] | None = None,
    tags: list[str] | None = None,
    classification: str = "internal",
    domain: str | None = None,
    ocr: bool = False,
    detect_multi_tables: bool = False,
) -> dict:
    """파일 업로드 → /api/convert/ingest → DB 적재 후 record 요약 반환."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(file_path)

    # multipart/form-data 직접 조립 (stdlib only).
    boundary = "----aidh-pack-boundary"
    body = bytearray()
    fields: list[tuple[str, str]] = [
        ("division", division), ("team", team),
        ("year", str(year)), ("seq", str(seq)),
        ("classification", classification),
    ]
    if agents:
        fields.append(("agents", ",".join(agents)))
    if tags:
        fields.append(("tags", ",".join(tags)))
    if domain:
        fields.append(("domain", domain))

    for k, v in fields:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        body.extend(v.encode("utf-8"))
        body.extend(b"\r\n")

    # 파일 part
    mime, _ = mimetypes.guess_type(path.name)
    mime = mime or "application/octet-stream"
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        .encode()
    )
    body.extend(f"Content-Type: {mime}\r\n\r\n".encode())
    body.extend(path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    qs = []
    if ocr:
        qs.append("ocr=true")
    if detect_multi_tables:
        qs.append("detect_multi_tables=true")
    url = BASE + "/api/convert/ingest" + (("?" + "&".join(qs)) if qs else "")

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY

    req = urllib.request.Request(url, data=bytes(body), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        payload = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"HTTP {e.code}: {payload}") from e


def main() -> int:
    p = argparse.ArgumentParser(description="Ingest a document into AI Data Hub")
    p.add_argument("file", help="input file (.docx/.pptx/.xlsx/.md/.html/.pdf)")
    p.add_argument("--division", required=True, help="대문자 (예: HE)")
    p.add_argument("--team", required=True, help="대문자 (예: CAE)")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--seq", type=int, required=True)
    p.add_argument("--agents", default="", help="콤마 구분")
    p.add_argument("--tags", default="", help="콤마 구분")
    p.add_argument("--classification", default="internal",
                   choices=["public", "internal", "confidential", "restricted"])
    p.add_argument("--domain", default=None)
    p.add_argument("--ocr", action="store_true", help="PDF: 빈 페이지 OCR 활성")
    p.add_argument("--detect-multi-tables", action="store_true",
                   help="Excel: 다중 표 자동 탐지")
    args = p.parse_args()

    print(f"Uploading {args.file} → {BASE}/api/convert/ingest ...")
    try:
        result = upload_and_ingest(
            args.file,
            division=args.division,
            team=args.team,
            year=args.year,
            seq=args.seq,
            agents=[a.strip() for a in args.agents.split(",") if a.strip()] or None,
            tags=[t.strip() for t in args.tags.split(",") if t.strip()] or None,
            classification=args.classification,
            domain=args.domain,
            ocr=args.ocr,
            detect_multi_tables=args.detect_multi_tables,
        )
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    print("Ingest 완료:")
    print(f"  id:      {result.get('id') or result.get('record_id')}")
    print(f"  title:   {result.get('title','')}")
    print(f"  tags:    {result.get('tags', [])}")
    print(f"  agents:  {result.get('agents', [])}")
    print(f"  status:  {result.get('status', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
