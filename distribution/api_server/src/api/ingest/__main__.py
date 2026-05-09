"""``python -m api.ingest`` 진입점."""
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
