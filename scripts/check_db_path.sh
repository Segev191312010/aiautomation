#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"

usage() {
  printf 'Usage: %s --host|--docker\n' "$0" >&2
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

mode="$1"
legacy_path="${repo_root}/trading_bot.db"

case "$mode" in
  --host|--docker) ;;
  *)
    usage
    exit 2
    ;;
esac

if [[ -e "$legacy_path" || -L "$legacy_path" ]]; then
  printf 'ERROR: legacy database path exists: %s\n' "$legacy_path" >&2
  exit 1
fi

validate_database_path_value() {
  local value="$1"
  if [[ "$value" =~ [[:cntrl:]] ]]; then
    printf 'ERROR: DB_PATH must be a plain filesystem path\n' >&2
    exit 1
  fi
  case "$value" in
    :memory:|file:*)
      printf 'ERROR: DB_PATH must be a plain filesystem path\n' >&2
      exit 1
      ;;
  esac
}

validate_database_path_value "${DB_PATH:-}"

case "$mode" in
  --host)
    python_bin="${PYTHON_BIN:-${repo_root}/backend/.venv/bin/python}"
    if [[ -x "$python_bin" ]]; then
      python_bin="$(cd "$(dirname "$python_bin")" && pwd -P)/$(basename "$python_bin")"
    else
      python_bin="$(command -v python3 || true)"
    fi
    if [[ -z "$python_bin" ]]; then
      printf 'ERROR: Python is required to resolve the host DB_PATH\n' >&2
      exit 1
    fi
    if ! canonical_path="$(
      cd "${repo_root}/backend"
      PYTHONPATH="${repo_root}/backend" "$python_bin" -c 'from config import cfg; print(cfg.DB_PATH)' 2>/dev/null
    )"; then
      printf 'ERROR: unable to resolve DB_PATH through backend/config.py\n' >&2
      exit 1
    fi
    ;;
  --docker)
    canonical_path="${DB_PATH:-/data/trading_bot.db}"
    if [[ "$canonical_path" != "/data/trading_bot.db" ]]; then
      printf 'ERROR: Docker DB_PATH must be /data/trading_bot.db\n' >&2
      exit 1
    fi
    ;;
esac

validate_database_path_value "$canonical_path"

canonical_path_folded="$(
  printf '%s' "$canonical_path" | LC_ALL=C tr '[:upper:]' '[:lower:]'
)"
legacy_path_folded="$(
  printf '%s' "$legacy_path" | LC_ALL=C tr '[:upper:]' '[:lower:]'
)"
if [[ "$canonical_path_folded" == "$legacy_path_folded" ]]; then
  printf 'ERROR: DB_PATH resolves to the forbidden legacy database path: %s\n' "$legacy_path" >&2
  exit 1
fi

printf 'Database path policy: mode=%s; canonical=%s; legacy path absent\n' "${mode#--}" "$canonical_path"
