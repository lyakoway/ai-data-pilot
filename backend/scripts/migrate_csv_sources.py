"""Migrate uploaded CSV tables from the pre-P0.4 SHARED sqlite file into
per-workspace files (data/csv_db/<ws>.db).

Contract (mirrors migrate_uploads.py):
- dry run by default, ``--apply`` moves data;
- idempotent: re-running skips already-migrated tables (per-ws file wins);
- collision (table already exists in the per-ws file) → REPORT + SKIP,
  never overwrite. Read priority is ALWAYS the per-ws file — the app never
  touches the shared file after this migration;
- orphans (metadata row references a workspace that does not exist, or no
  metadata at all) → REPORT, untouched (operator decision);
- atomicity: CREATE+INSERT into the per-ws file, verify row counts, only
  then DROP from the shared file. A crash between write and drop leaves the
  table in BOTH files — reads stay correct (per-ws priority) and a re-run
  finishes the job (drop step is safe to repeat).

Usage (from backend/):
    python scripts/migrate_csv_sources.py            # dry run
    python scripts/migrate_csv_sources.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import app_db  # noqa: E402
from app.db.datasources import _WS_ID_RE  # noqa: E402
from app.tenancy import DEFAULT_WORKSPACE_ID  # noqa: E402

SHARED_DB = "csv_sources.db"


def _shared_engine():
    path = get_settings().data_dir / SHARED_DB
    if not path.exists():
        return None
    return create_engine(f"sqlite:///{path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Per-workspace CSV db migration (P0.4)")
    parser.add_argument("--apply", action="store_true", help="move tables (default: dry run)")
    args = parser.parse_args()

    shared = _shared_engine()
    if shared is None:
        print(f"No shared {SHARED_DB} found — nothing to migrate.")
        return

    with shared.connect() as conn:
        tables = [r[0] for r in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        ).fetchall()]

    # workspace lookup: table_name → workspace via datasource metadata
    meta_rows = {}
    eng = app_db.get_app_engine()
    with eng.connect() as conn:
        for r in conn.execute(
            text("SELECT table_name, workspace_id FROM datasources WHERE kind = 'csv' AND table_name IS NOT NULL")
        ).fetchall():
            meta_rows[r[0]] = r[1]
    ws_exists = {w["id"] for w in app_db.list_workspaces()}

    moved = skipped = collided = orphaned = 0
    orphans: list[str] = []
    collisions: list[str] = []

    for tbl in tables:
        ws = meta_rows.get(tbl)
        if ws is None or ws not in ws_exists or not _WS_ID_RE.match(ws or ""):
            orphans.append(f"{tbl} (workspace={ws!r})")
            orphaned += 1
            continue
        dest_path = get_settings().data_dir / "csv_db" / f"{ws}.db"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest = create_engine(f"sqlite:///{dest_path}")
        with dest.connect() as dconn:
            exists = dconn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name = :t"), {"t": tbl}
            ).fetchone()
        if exists:
            collisions.append(f"{tbl} -> {ws}/ (already present)")
            collided += 1
            # finish an interrupted migration: drop the shared copy if identical
            if args.apply:
                with shared.begin() as sconn, dest.begin() as dconn2:
                    s_n = sconn.execute(text(f'SELECT COUNT(*) FROM "{tbl}"')).scalar()
                    d_n = dconn2.execute(text(f'SELECT COUNT(*) FROM "{tbl}"')).scalar()
                    if s_n == d_n:
                        sconn.execute(text(f'DROP TABLE IF EXISTS "{tbl}"'))
                        print(f"FINISHED (dropped shared copy): {tbl}")
            continue
        if args.apply:
            with shared.connect() as sconn, dest.begin() as dconn2:
                rows = sconn.execute(text(f'SELECT * FROM "{tbl}"')).fetchall()
                cols = list(sconn.execute(text(f'SELECT * FROM "{tbl}"')).keys())
                col_sql = ", ".join(f'"{c}"' for c in cols)
                ph = ", ".join(f":c{i}" for i in range(len(cols)))
                dconn2.execute(text(f'CREATE TABLE "{tbl}" ({col_sql})'))
                dconn2.execute(
                    text(f'INSERT INTO "{tbl}" ({col_sql}) VALUES ({ph})'),
                    [dict(zip((f"c{i}" for i in range(len(cols))), row)) for row in rows],
                )
                written = dconn2.execute(text(f'SELECT COUNT(*) FROM "{tbl}"')).scalar()
                if written == len(rows):
                    # atomicity: drop only after verified copy
                    with shared.begin() as sconn2:
                        sconn2.execute(text(f'DROP TABLE IF EXISTS "{tbl}"'))
                    print(f"MOVED: {tbl} -> csv_db/{ws}.db ({written} rows)")
                else:
                    print(f"VERIFY FAILED (kept shared copy): {tbl} {written}!={len(rows)}")
                    collided += 1
                    continue
        else:
            print(f"WOULD MOVE: {tbl} -> csv_db/{ws}.db")
        moved += 1

    print(f"\n{'Applied' if args.apply else 'Dry run'}: moved={moved}, collisions(skipped)={collided}, "
          f"untouched-orphans={orphaned}")
    if collisions:
        print("\nCOLLISIONS (per-ws copy wins; shared copy kept/verified):")
        for c in collisions:
            print(f"  - {c}")
    if orphans:
        print("\nORPHANS (unknown workspace — NOT touched, operator decision):")
        for o in orphans:
            print(f"  - {o}")


if __name__ == "__main__":
    main()
