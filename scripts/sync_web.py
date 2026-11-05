"""Copy the root ``web/`` into the package so the front end ships inside the wheel.

The UI is plain HTML/JS with no build step and lives at the repository root;
``scripts/build.sh`` runs this before ``pip wheel``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "web"
TARGET = ROOT / "musicpilot" / "web"


def main() -> None:
    if not SOURCE.is_dir():
        raise SystemExit(f"web directory not found: {SOURCE}")
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET, ignore=shutil.ignore_patterns("__pycache__", "*.map"))
    print(f"synced {SOURCE} -> {TARGET}")


if __name__ == "__main__":
    main()
