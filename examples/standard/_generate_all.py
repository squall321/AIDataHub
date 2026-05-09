"""표준 예제 6종을 한 번에 생성 + 모든 변환기로 검증.

실행 방법
--------
$env:PYTHONPATH = "d:/Personal/AI_data/api_server/src"
& "d:/Personal/AI_data/api_server/.venv/Scripts/python.exe" \
    examples/standard/_generate_all.py

처리 단계:
1) Word/PPT/Excel/PDF 예제 파일 생성 (Markdown/HTML 은 hand-written, 손대지 않음)
2) nurbs_box.png placeholder 생성 (1x1 투명 PNG)
3) 6개 변환기를 차례로 실행해 examples/standard/converted/ 에 JSON 저장
4) 변환 결과 요약 (성공/실패, 추출된 핵심 메타) 출력

이 스크립트는 idempotent — 여러 번 실행해도 같은 결과.
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # d:/Personal/AI_data
VENV_PY = ROOT / "api_server" / ".venv" / "Scripts" / "python.exe"
SRC_DIR = ROOT / "api_server" / "src"
OUT_DIR = HERE / "converted"


# ── 0. 경로 추가 (이 스크립트 안에서 _generate_*.py 를 import) ─────
sys.path.insert(0, str(HERE))


# ── 1. PNG placeholder ────────────────────────────────────────────
def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_minimal_png(path: Path) -> None:
    """1x1 회색 PNG (외부 의존 없이 바이너리로 직접 생성)."""
    if path.exists():
        return
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    raw = b"\x00\x80"  # filter byte + 1 grey pixel
    idat = zlib.compress(raw, 9)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        sig
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


# ── 2. 예제 5종 생성 ──────────────────────────────────────────────
def generate_examples() -> dict[str, Path]:
    from _generate_word import build_sample_report
    from _generate_ppt import build_sample_presentation
    from _generate_excel import build_sample_data
    from _generate_pdf import build_sample_pdf

    word_path = build_sample_report(HERE / "sample_report.docx")
    ppt_path = build_sample_presentation(HERE / "sample_presentation.pptx")
    xlsx_path = build_sample_data(HERE / "sample_data.xlsx")
    pdf_path = build_sample_pdf(HERE / "sample_doc.pdf")

    md_path = HERE / "sample_doc.md"
    html_path = HERE / "sample_doc.html"
    png_path = HERE / "nurbs_box.png"
    write_minimal_png(png_path)

    return {
        "word": word_path,
        "ppt": ppt_path,
        "excel": xlsx_path,
        "pdf": pdf_path,
        "md": md_path,
        "html": html_path,
        "png": png_path,
    }


# ── 3. 변환기 호출 ────────────────────────────────────────────────
def run_converter(
    module: str,
    input_path: Path,
    seq: int,
    extra_args: list[str] | None = None,
) -> tuple[bool, str]:
    """변환기 모듈을 subprocess 로 실행해 stdout + stderr 반환."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(VENV_PY),
        "-m", module,
        str(input_path),
        "--division", "HE",
        "--team", "CAE" if module != "excel_converter" else "MFG",
        "--year", "2026",
    ]
    if module == "excel_converter":
        cmd += ["--start-seq", str(seq), "--infer-units"]
    else:
        cmd += ["--seq", str(seq)]
    cmd += ["--output-dir", str(OUT_DIR)]
    if extra_args:
        cmd += extra_args

    env = {
        **__import__("os").environ,
        "PYTHONPATH": str(SRC_DIR),
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", env=env
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0
    return ok, out


def find_latest_json(prefix_div: str, prefix_team: str, seq: int) -> Path | None:
    """방금 생성된 JSON 파일을 찾는다."""
    # DOC- 와 DATA- prefix 둘 다 시도
    for kind in ("DOC", "DATA"):
        cand = OUT_DIR / f"{kind}-{prefix_div}-{prefix_team}-2026-{seq:06d}.json"
        if cand.exists():
            return cand
    # 일부 변환기는 prefix 없는 형식 사용
    cand2 = OUT_DIR / f"{prefix_div}-{prefix_team}-2026-{seq:06d}.json"
    if cand2.exists():
        return cand2
    return None


# ── 4. 검증 / 요약 ────────────────────────────────────────────────
def summarize_json(p: Path) -> dict:
    if not p.exists():
        return {"error": "json not found"}
    obj = json.loads(p.read_text(encoding="utf-8"))
    meta = obj.get("meta", {}) or {}
    return {
        "doc_id": (
            meta.get("doc_id")
            or meta.get("data_id")
            or obj.get("data_id")
        ),
        "title": meta.get("title") or obj.get("caption"),
        "summary": (meta.get("summary") or "")[:60],
        "tags": meta.get("tags", []),
        "sections": len(obj.get("sections", []) or []),
        "figures": len(obj.get("figures", []) or []),
        "tables": len(obj.get("tables", []) or []),
        "rows": len(obj.get("rows", []) or [])
        if obj.get("rows") is not None
        else None,
        "warnings": len(obj.get("warnings", []) or []),
    }


def main() -> int:
    print("=" * 64)
    print(" 표준 예제 6종 생성 + 변환 검증")
    print("=" * 64)

    # converted/ 폴더 초기화 (이전 실행 잔여물 제거)
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 예제 생성
    print("\n[1/3] 예제 파일 생성")
    print("-" * 64)
    files = generate_examples()
    for name, p in files.items():
        size = p.stat().st_size if p.exists() else 0
        print(f"  {name:<6}  {p.relative_to(ROOT)}  ({size:,} bytes)")

    # 2) 변환기 실행
    print("\n[2/3] 변환기 실행")
    print("-" * 64)
    # 일부 변환기는 본문에서 [TAGS]/[AGENT_SCOPE] 마커를 자동 추출하지 않으므로
    # CLI 인자로 직접 주입한다 (PPT, PDF, Markdown 의 폴백).
    common_tags_agents = [
        "--tags", "IGA,NURBS,KooRemapper,sample,standard",
        "--agents", "iga-analyst,cae-reporter",
    ]

    runs = [
        ("converter",       files["word"],  "CAE", 100, []),
        ("excel_converter", files["excel"], "MFG", 200, []),
        ("ppt_converter",   files["ppt"],   "CAE", 300, common_tags_agents),
        ("pdf_converter",   files["pdf"],   "CAE", 400, common_tags_agents),
        ("md_converter",    files["md"],    "CAE", 500, []),  # MD 는 front matter 사용
        ("html_converter",  files["html"],  "CAE", 600, []),  # HTML 은 head meta 사용
    ]
    results: list[dict] = []
    for module, input_path, team, seq, extra in runs:
        print(f"\n--- {module}  ({input_path.name}) ---")
        ok, out = run_converter(module, input_path, seq, extra_args=extra)
        # 요약 라인만 출력 (전체는 길다)
        lines = [l for l in out.splitlines() if l.strip()]
        for l in lines[-12:]:
            print(f"   {l}")
        json_path = find_latest_json("HE", team, seq)
        results.append(
            {
                "module": module,
                "input": input_path.name,
                "ok": ok,
                "json_path": str(json_path.relative_to(ROOT)) if json_path else None,
                "summary": summarize_json(json_path) if json_path else None,
            }
        )

    # 3) 요약
    print("\n[3/3] 변환 요약")
    print("-" * 64)
    for r in results:
        flag = "OK " if r["ok"] and r["json_path"] else "FAIL"
        print(f"  [{flag}] {r['module']:<16} -> {r['json_path']}")
        if r["summary"]:
            s = r["summary"]
            print(f"         doc/data_id : {s.get('doc_id')}")
            print(f"         title       : {s.get('title')}")
            print(f"         sections    : {s.get('sections')}")
            print(f"         figures     : {s.get('figures')}")
            print(f"         tables      : {s.get('tables')}")
            if s.get("rows") is not None:
                print(f"         rows        : {s.get('rows')}")
            print(f"         warnings    : {s.get('warnings')}")

    # 0 if all ok else 2
    ok_all = all(r["ok"] and r["json_path"] for r in results)
    print()
    print("=" * 64)
    print(" 결과:", "ALL OK" if ok_all else "FAIL — 위 로그 확인")
    print("=" * 64)
    return 0 if ok_all else 2


if __name__ == "__main__":
    raise SystemExit(main())
