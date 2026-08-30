#!/usr/bin/env bash
set -euo pipefail

DB="${1:-${DB_PATH:-backend/autotrader_v1.db}}"
OUT="${2:-backups/autotrader_v1.$(date -u +%Y%m%dT%H%M%SZ).db.gz}"
mkdir -p "$(dirname "$OUT")"
sqlite3 "$DB" ".backup '${OUT}.tmp'"
gzip -f "${OUT}.tmp"
mv "${OUT}.tmp.gz" "$OUT"
printf '%s\n' "$OUT"
