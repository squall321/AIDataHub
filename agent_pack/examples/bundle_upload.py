"""Mobile eXperience AI Data Hub — 사전 변환된 JSON + 자원 폴더를 ZIP 번들로 업로드.

변환기 (Word/PPT/Excel/MD/PDF/HTML) 가 이미 출력해 둔 ``output/{doc_id}.json``
+ ``output/{doc_id}/`` (figures/attachments) 를 한꺼번에 서버에 적재한다.
변환 단계를 서버에서 다시 돌리지 않으므로 빠르고, 자원 파일까지 정적
마운트에 자동 배치된다.

사용:
    # 변환기 출력 폴더 통째 zip + 업로드
    python bundle_upload.py /path/to/output/DOC-HE-CAE-2026-0000000001.json

    # 또는 미리 만든 zip 직접 업로드
    python bundle_upload.py /path/to/bundle.zip
"""
from __future__ import annotations
import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import urllib.error
import zipfile
from pathlib import Path

# === API URL ================================================================
BASE = os.environ.get("AIDH_API_URL", "http://110.15.177.125:8000").rstrip("/")
API_KEY: str | None = os.environ.get("AIDH_API_KEY")
# ============================================================================


def build_bundle_zip(json_path: Path) -> bytes:
    """``output/{doc_id}.json`` 옆의 ``output/{doc_id}/`` 폴더를 함께 zip.

    레이아웃 (A) 으로 압축 — 서버가 자동 인식.
    """
    with json_path.open("r", encoding="utf-8-sig") as f:
        record = json.load(f)
    meta = record.get("meta") or {}
    doc_id = meta.get("doc_id") or record.get("data_id") or meta.get("id")
    if not doc_id:
        raise ValueError(
            f"cannot determine doc_id from {json_path}: meta.doc_id / data_id / meta.id 모두 없음"
        )

    resources_dir = json_path.parent / doc_id

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(json_path, arcname=json_path.name)
        if resources_dir.is_dir():
            for p in resources_dir.rglob("*"):
                if p.is_file():
                    rel = p.relative_to(json_path.parent)
                    zf.write(p, arcname=str(rel).replace("\\", "/"))
    return buf.getvalue()


def upload_bundle(zip_bytes: bytes, filename: str = "bundle.zip") -> dict:
    """ZIP 바이트를 ``POST /api/ingest/bundle`` 로 업로드."""
    boundary = "----aidh-pack-bundle"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        .encode()
    )
    body.extend(b"Content-Type: application/zip\r\n\r\n")
    body.extend(zip_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY

    req = urllib.request.Request(
        f"{BASE}/api/ingest/bundle",
        data=bytes(body),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:500]}"
        ) from e


def main() -> int:
    p = argparse.ArgumentParser(description="Upload pre-converted bundle.")
    p.add_argument(
        "input",
        help=".json (변환기 출력) 또는 .zip (이미 압축된 번들)",
    )
    args = p.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"FAIL: not found: {src}", file=sys.stderr)
        return 1

    if src.suffix.lower() == ".zip":
        zip_bytes = src.read_bytes()
        filename = src.name
        print(f"Uploading prebuilt zip: {src} ({len(zip_bytes)} bytes)")
    elif src.suffix.lower() == ".json":
        zip_bytes = build_bundle_zip(src)
        filename = f"{src.stem}.zip"
        print(f"Built bundle from {src} + sibling folder ({len(zip_bytes)} bytes)")
    else:
        print(f"FAIL: input must be .json or .zip, got {src.suffix!r}", file=sys.stderr)
        return 1

    print(f"POST {BASE}/api/ingest/bundle ...")
    try:
        result = upload_bundle(zip_bytes, filename=filename)
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    print(f"\n[OK] uploaded:")
    print(f"  id:                 {result.get('id')}")
    print(f"  data_type:          {result.get('data_type')}")
    print(f"  title:              {result.get('title')}")
    print(f"  figures_copied:     {result.get('figures_copied')}")
    print(f"  attachments_copied: {result.get('attachments_copied')}")
    warnings = result.get("warnings") or {}
    missing = warnings.get("missing_resources") or []
    extra = warnings.get("extra_resources") or []
    if missing:
        print(f"  ⚠ missing (referenced but not in zip): {missing}")
    if extra:
        print(f"  ℹ extra (in zip but not referenced):    {extra}")

    rid = result.get("id")
    if rid:
        print(f"\n  detail:    {BASE}/api/records/{rid}")
        print(f"  figures:   {BASE}/figures/{rid}/")
        print(f"  attachments: {BASE}/attachments/{rid}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
