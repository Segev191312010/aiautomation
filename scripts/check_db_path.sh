#!/usr/bin/env bash
set -euo pipefail

legacy_path="${1:-trading_bot.db}"
canonical_path="${2:-backend/trading_bot.db}"

if [[ -e "$legacy_path" ]]; then
  printf 'ERROR: legacy database path exists: %s\n' "$legacy_path" >&2
  printf 'Use only the configured backend database path: %s\n' "$canonical_path" >&2
  exit 1
fi

if [[ -e "$canonical_path" && "$legacy_path" -nt "$canonical_path" ]]; then
  printf 'ERROR: legacy database path is newer than canonical path: %s\n' "$legacy_path" >&2
  exit 1
fi

printf 'Database path policy: canonical=%s; legacy path absent\n' "$canonical_path"
