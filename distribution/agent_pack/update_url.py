#!/usr/bin/env python3
"""Update the canonical API URL across the entire agent_pack folder.

Replaces every occurrence of the current URL (stored in `.api_url`) with the
new URL given as an argument, then updates `.api_url`. Idempotent.

Usage:
    python update_url.py http://new-server:8000
    python update_url.py --dry-run http://new-server:8000   # preview only

Standalone — Python 3.10+, no external dependencies.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PACK_DIR = Path(__file__).parent.resolve()
URL_MARKER = PACK_DIR / ".api_url"
SCAN_EXTS = {".md", ".py", ".sh", ".ts", ".js", ".json", ".txt", ".yaml", ".yml"}
EXCLUDE_NAMES = {"update_url.py", "update_url.ps1", ".api_url"}


def _read_marker() -> str:
    if not URL_MARKER.exists():
        sys.stderr.write(f"ERROR: marker file missing: {URL_MARKER}\n")
        sys.stderr.write(
            "  Create it manually with the current canonical URL inside.\n"
        )
        sys.exit(1)
    return URL_MARKER.read_text(encoding="utf-8").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("new_url", help="new API URL (e.g. http://server:8000)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would change without writing",
    )
    args = parser.parse_args()

    new_url = args.new_url.rstrip("/")
    old_url = _read_marker()

    if old_url == new_url:
        print(f"Already at {new_url} — nothing to do.")
        return 0

    print(f"OLD: {old_url}")
    print(f"NEW: {new_url}")
    if args.dry_run:
        print("(dry-run — no writes)")
    print()

    changed: list[tuple[Path, int]] = []
    skipped: list[Path] = []

    for f in sorted(PACK_DIR.rglob("*")):
        if not f.is_file():
            continue
        if f.name in EXCLUDE_NAMES:
            continue
        if f.suffix.lower() not in SCAN_EXTS:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped.append(f)
            continue
        count = text.count(old_url)
        if count == 0:
            continue
        if not args.dry_run:
            f.write_text(text.replace(old_url, new_url), encoding="utf-8")
        changed.append((f.relative_to(PACK_DIR), count))

    if not args.dry_run:
        URL_MARKER.write_text(new_url + "\n", encoding="utf-8")

    if not changed:
        print("No files contained the old URL.")
        return 0

    total = sum(c for _, c in changed)
    print(f"Replaced {total} occurrences across {len(changed)} file(s):")
    for path, count in changed:
        print(f"  {count:3d}× {path}")

    if skipped:
        print(f"\n(skipped {len(skipped)} non-utf8 file(s))")

    if args.dry_run:
        print("\n[dry-run] no writes performed. Run without --dry-run to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
