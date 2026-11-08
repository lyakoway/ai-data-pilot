"""Migrate legacy flat upload files into workspace-scoped directories (P0.3).

Idempotent and safe-by-default:
- only moves files whose document metadata exists (workspace known from P0.2
  migration — legacy rows were stamped ws_default);
- a file with NO metadata row is reported and LEFT UNTOUCHED (blocking
  question for the operator: which workspace owns it?);
- already-migrated files (in a workspace dir) are skipped;
- a partial run can simply be re-run: every step checks current state.

Usage (from backend/):
    python scripts/migrate_uploads.py            # dry run (default)
    python scripts/migrate_uploads.py --apply    # actually move files
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.storage import _DOC_ID_RE, _WS_RE, uploads_root  # noqa: E402
from app.db import app_db  # noqa: E402
from app.tenancy import DEFAULT_WORKSPACE_ID  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate flat uploads into workspace dirs")
    parser.add_argument("--apply", action="store_true", help="move files (default: dry run)")
    args = parser.parse_args()

    root = uploads_root()
    flat_files = [p for p in root.iterdir() if p.is_file()]
    moved = skipped = orphaned = 0
    orphans: list[str] = []

    for f in flat_files:
        stem = f.stem
        ext = f.suffix.lower()
        if not _DOC_ID_RE.match(stem):
            orphans.append(f.name)
            orphaned += 1
            continue
        doc = None
        # metadata lookup without workspace filter (migration is admin-level)
        eng = app_db.get_app_engine()
        from sqlalchemy import text

        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT workspace_id FROM documents WHERE id = :id"), {"id": stem}
            ).fetchone()
        if row is None:
            orphans.append(f.name)
            orphaned += 1
            continue
        ws = row[0] or DEFAULT_WORKSPACE_ID
        if not _WS_RE.match(ws):
            orphans.append(f"{f.name} (bad workspace {ws!r})")
            orphaned += 1
            continue
        dest_dir = root / ws
        dest = dest_dir / f.name
        if dest.exists():
            print(f"SKIP (already migrated): {f.name} -> {ws}/")
            skipped += 1
            continue
        if args.apply:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dest))
            print(f"MOVED: {f.name} -> {ws}/{f.name}")
        else:
            print(f"WOULD MOVE: {f.name} -> {ws}/{f.name}")
        moved += 1

    print(f"\n{'Applied' if args.apply else 'Dry run'}: moved={moved}, "
          f"already-migrated={skipped}, untouched-orphans={orphaned}")
    if orphans:
        print("\nORPHANS (no document metadata — NOT touched, need an owner decision):")
        for name in orphans:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
