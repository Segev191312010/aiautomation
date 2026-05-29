#!/usr/bin/env bash
# Restore a gzipped SQLite backup, verifying integrity before swapping it in.
# Usage: scripts/restore_db.sh IN.gz [DB_PATH]
set -euo pipefail
IN="$1"
DB="${2:-backend/trading_bot.db}"
if [ -f "$DB" ]; then
  cp "$DB" "${DB}.pre-restore.$(date -u +%Y%m%dT%H%M%SZ)"
fi
gunzip -kc "$IN" > "${DB}.tmp"
if sqlite3 "${DB}.tmp" "PRAGMA integrity_check;" | grep -q '^ok$'; then
  mv "${DB}.tmp" "$DB"
  echo "restored $DB from $IN"
else
  rm -f "${DB}.tmp"
  echo "integrity check FAILED — restore aborted, live DB untouched" >&2
  exit 1
fi
