#!/usr/bin/env bash
# Atomic SQLite backup (online .backup API — safe on a live DB, no write lock) + gzip.
# Usage: scripts/backup_db.sh [DB_PATH] [OUT.gz]
set -euo pipefail
DB="${1:-backend/trading_bot.db}"
OUT="${2:-backups/trading_bot.$(date -u +%Y%m%dT%H%M%SZ).db.gz}"
mkdir -p "$(dirname "$OUT")"
sqlite3 "$DB" ".backup '${OUT}.tmp'"
gzip -f "${OUT}.tmp"
mv "${OUT}.tmp.gz" "$OUT"
echo "$OUT"
