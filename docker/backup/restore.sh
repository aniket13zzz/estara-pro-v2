#!/bin/sh
# Restore a backup made by backup.sh. REPLACES the current database and uploaded files.
# Stop the backend and worker first (docs/deploy.md, "Restore"):
#   restore.sh estara-db-YYYYMMDD-HHMM.dump [estara-uploads-YYYYMMDD-HHMM.tar.gz]
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
UPLOADS_DIR="${UPLOADS_DIR:-/uploads}"
db="$BACKUP_DIR/$(basename "${1:?name of the estara-db-....dump file}")"
files="${2:+$BACKUP_DIR/$(basename "$2")}"

[ -f "$db" ] || { echo "not found: $db" >&2; exit 1; }
pg_restore --list "$db" > /dev/null

echo "restoring $(basename "$db") into database $PGDATABASE"
psql -v ON_ERROR_STOP=1 -d postgres -c "DROP DATABASE IF EXISTS \"$PGDATABASE\" WITH (FORCE)"
psql -v ON_ERROR_STOP=1 -d postgres -c "CREATE DATABASE \"$PGDATABASE\""
pg_restore --no-owner --exit-on-error -d "$PGDATABASE" "$db"

if [ -n "$files" ]; then
  [ -f "$files" ] || { echo "not found: $files" >&2; exit 1; }
  echo "restoring uploaded files from $(basename "$files")"
  find "$UPLOADS_DIR" -mindepth 1 -delete
  tar -xzf "$files" -C "$UPLOADS_DIR"
fi
echo "restore ok. Start the backend and worker again."
