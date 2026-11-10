"""Delete a workspace: files first, then metadata in one transaction (P1.1).

Order (per ADR-008): data/uploads/<ws>/, data/csv_db/<ws>.db,
data/exports/<ws>/ → then chunks → documents → datasources → scenarios →
feedback → workspaces row (single transaction).

- DRY RUN by default: reports what WOULD be removed (file counts, row counts).
- ``--apply`` performs the deletion; idempotent — a second run is a no-op.
- Refuses to delete ``ws_default`` (the seeded dev workspace) unless --force.

Usage (from backend/):
    python scripts/delete_workspace.py --ws ws_alpha            # dry run
    python scripts/delete_workspace.py --ws ws_alpha --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.storage import exports_root, uploads_root  # noqa: E402
from app.db import app_db  # noqa: E402
from app.tenancy import DEFAULT_WORKSPACE_ID  # noqa: E402

WS_RE_OK = __import__("re").compile(r"^[A-Za-z0-9_-]+$")

META_TABLES_ORDER = [
    ("chunks", "DELETE FROM chunks WHERE document_id IN (SELECT id FROM documents WHERE workspace_id = :ws)"),
    ("documents", "DELETE FROM documents WHERE workspace_id = :ws"),
    ("datasources", "DELETE FROM datasources WHERE workspace_id = :ws"),
    ("scenarios", "DELETE FROM scenarios WHERE workspace_id = :ws"),
    ("feedback", "DELETE FROM feedback WHERE workspace_id = :ws"),
    ("workspaces", "DELETE FROM workspaces WHERE id = :ws"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete a workspace (files + metadata)")
    parser.add_argument("--ws", required=True, help="workspace id to delete")
    parser.add_argument("--apply", action="store_true", help="perform deletion (default: dry run)")
    parser.add_argument("--allow-default", action="store_true",
                        help="allow deleting ws_default (dev/demo workspace)")
    args = parser.parse_args()

    ws = args.ws
    if not WS_RE_OK.match(ws):
        raise SystemExit(f"invalid workspace id: {ws!r}")
    if ws == DEFAULT_WORKSPACE_ID and not args.allow_default:
        raise SystemExit("refusing to delete the default dev workspace (use --allow-default)")

    # ---- files ----
    uploads_dir = uploads_root() / ws
    csv_db = get_settings().data_dir / "csv_db" / f"{ws}.db"
    exports_dir = exports_root() / ws
    file_targets = [
        ("uploads", uploads_dir, sum(1 for _ in uploads_dir.rglob("*")) if uploads_dir.exists() else 0),
        ("csv_db", csv_db, 1 if csv_db.exists() else 0),
        ("exports", exports_dir, sum(1 for _ in exports_dir.rglob("*")) if exports_dir.exists() else 0),
    ]
    # ---- metadata counts ----
    eng = app_db.get_app_engine()
    with eng.connect() as conn:
        n_docs = conn.execute(
            text("SELECT COUNT(*) FROM documents WHERE workspace_id = :ws"), {"ws": ws}
        ).scalar() or 0
        n_chunks = conn.execute(
            text("SELECT COUNT(*) FROM chunks WHERE document_id IN "
                 "(SELECT id FROM documents WHERE workspace_id = :ws)"), {"ws": ws}
        ).scalar() or 0
        n_sources = conn.execute(
            text("SELECT COUNT(*) FROM datasources WHERE workspace_id = :ws"), {"ws": ws}
        ).scalar() or 0
        n_scen = conn.execute(
            text("SELECT COUNT(*) FROM scenarios WHERE workspace_id = :ws"), {"ws": ws}
        ).scalar() or 0
        n_fb = conn.execute(
            text("SELECT COUNT(*) FROM feedback WHERE workspace_id = :ws"), {"ws": ws}
        ).scalar() or 0

    verb = "WOULD DELETE" if not args.apply else "DELETING"
    print(f"{verb}: workspace {ws!r}")
    for label, path, count in file_targets:
        print(f"  [{label:<9}] {path}  ({count} item(s))")
    print(f"  [metadata] chunks={n_chunks} documents={n_docs} datasources={n_sources} "
          f"scenarios={n_scen} feedback={n_fb}")

    if not args.apply:
        print("\nDry run — re-run with --apply to delete.")
        return

    # files
    import shutil

    for label, path, _ in file_targets:
        if path.is_dir():
            shutil.rmtree(path)
            print(f"  removed dir: {path}")
        elif path.exists():
            path.unlink()
            print(f"  removed file: {path}")
        else:
            print(f"  already gone: {path}")

    # metadata: one transaction, dependency order
    with eng.begin() as conn:
        for table, stmt in META_TABLES_ORDER:
            n = conn.execute(text(stmt), {"ws": ws}).rowcount
            print(f"  metadata {table:<12}: {n} row(s) deleted")
    print(f"\nWorkspace {ws!r} deleted. (Re-run is a no-op.)")


if __name__ == "__main__":
    main()
