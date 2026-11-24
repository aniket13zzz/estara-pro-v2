#!/bin/sh
# Daily backup of the database and uploaded files into BACKUP_DIR (the ./backups folder on the
# server). Keeps BACKUP_KEEP_DAYS days. Run as the `backup` service; `backup.sh now` makes one
# backup immediately and exits.
#
# Needs PGHOST, PGUSER, PGPASSWORD, PGDATABASE. Times are UTC (21:00 UTC = 02:30 in India).
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
UPLOADS_DIR="${UPLOADS_DIR:-/uploads}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
AT="${BACKUP_TIME_UTC:-21:00}"

backup_once() {
  stamp=$(date -u +%Y%m%d-%H%M)
  umask 077
  mkdir -p "$BACKUP_DIR"
  db="$BACKUP_DIR/estara-db-$stamp.dump"
  files="$BACKUP_DIR/estara-uploads-$stamp.tar.gz"
  trap 'rm -f "$db.part" "$files.part"' EXIT
  pg_dump --format=custom --no-owner --file="$db.part" "$PGDATABASE"
  pg_restore --list "$db.part" > /dev/null   # the file must be readable before we keep it
  mv "$db.part" "$db"
  if [ -d "$UPLOADS_DIR" ]; then
    tar -czf "$files.part" -C "$UPLOADS_DIR" .
    mv "$files.part" "$files"
  fi
  find "$BACKUP_DIR" -name 'estara-*' -type f -mtime +"$KEEP_DAYS" -delete
  echo "backup ok: $(basename "$db") ($(du -h "$db" | cut -f1)); keeping $KEEP_DAYS days"
}

if [ "${1:-}" = "now" ]; then
  backup_once
  exit 0
fi

echo "daily backup at $AT UTC into $BACKUP_DIR, keeping $KEEP_DAYS days"
last=""
while true; do
  today=$(date -u +%Y%m%d)
  if [ "$(date -u +%H:%M)" = "$AT" ] && [ "$last" != "$today" ]; then
    # A separate process, so `set -e` stops a failed backup (it is ignored inside `if`).
    if sh "$0" now; then last="$today"; else echo "BACKUP FAILED, will retry in a minute" >&2; fi
  fi
  sleep 30
done
