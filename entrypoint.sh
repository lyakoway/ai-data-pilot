#!/bin/sh
# App entrypoint: starts a small local demo PostgreSQL (database "shop", role
# "demo") before the API server, so the "Connect PostgreSQL" modal works out of
# the box for demo visitors. PG is best-effort: if it fails to start, the app
# still starts — it does not require PostgreSQL.

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SEED="${SEED:-$APP_DIR/tests/seed_postgres.sql}"
PGDATA="${PGDATA:-/tmp/pgdata}"
PGPORT="${PGPORT:-5432}"
PGLOG="${PGLOG:-/tmp/postgres.log}"

# Debian puts server binaries outside PATH (/usr/lib/postgresql/<v>/bin).
PGBIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)"
[ -n "$PGBIN" ] && PATH="$PGBIN:$PATH"
export PATH

if ! command -v initdb >/dev/null 2>&1; then
    echo "[entrypoint] postgresql not installed — starting app without demo DB"
    exec "$@"
fi

# initdb/pg_ctl refuse to run as root — the debian package creates user
# "postgres"; non-root images (e.g. HF Spaces uid 1000) run them directly.
as_pg() {
    if [ "$(id -u)" = "0" ]; then su postgres -c "$1"; else sh -c "$1"; fi
}

if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo "[entrypoint] initializing demo PostgreSQL in $PGDATA"
    mkdir -p "$PGDATA"
    [ "$(id -u)" = "0" ] && chown postgres "$PGDATA"
    as_pg "initdb -D '$PGDATA' -U demo --auth=trust --locale=C.UTF-8 -E UTF8" \
        || echo "[entrypoint] initdb failed — continuing without demo DB"
fi

if [ -s "$PGDATA/PG_VERSION" ] && ! pg_isready -h 127.0.0.1 -p "$PGPORT" -q; then
    as_pg "pg_ctl -D '$PGDATA' -o \"-c listen_addresses='127.0.0.1' -p $PGPORT\" -l '$PGLOG' -w -t 30 start" \
        || echo "[entrypoint] postgres failed to start — continuing without demo DB"
fi

if pg_isready -h 127.0.0.1 -p "$PGPORT" -q; then
    if ! psql -h 127.0.0.1 -p "$PGPORT" -U demo -d postgres -tAc \
            "SELECT 1 FROM pg_database WHERE datname='shop'" | grep -q 1; then
        echo "[entrypoint] creating demo database 'shop'"
        psql -h 127.0.0.1 -p "$PGPORT" -U demo -d postgres -c "CREATE DATABASE shop" || true
        if [ -f "$SEED" ]; then
            psql -h 127.0.0.1 -p "$PGPORT" -U demo -d shop -v ON_ERROR_STOP=1 -f "$SEED" \
                && echo "[entrypoint] demo seed loaded" \
                || echo "[entrypoint] seed failed — shop may be empty"
        fi
    fi
fi

exec "$@"
