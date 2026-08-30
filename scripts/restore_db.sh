#!/usr/bin/env bash
set -euo pipefail

IN="$1"
DB="${2:-${DB_PATH:-backend/autotrader_v1.db}}"
if [ -f "$DB" ]; then
  cp "$DB" "${DB}.pre-restore.$(date -u +%Y%m%dT%H%M%SZ)"
fi
gunzip -kc "$IN" > "${DB}.tmp"
if sqlite3 "${DB}.tmp" "PRAGMA integrity_check;" | grep -q '^ok$'; then
  mv "${DB}.tmp" "$DB"
  printf 'restored %s from %s\n' "$DB" "$IN"
else
  rm -f "${DB}.tmp"
  printf 'integrity check failed; database unchanged\n' >&2
  exit 1
fi
