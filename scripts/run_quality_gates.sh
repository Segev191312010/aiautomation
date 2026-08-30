#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

step() { printf '\n=== [%s] %s ===\n' "$1" "$2"; }

step "1/8" "backend tests"
(cd "$ROOT/backend" && .venv/bin/pytest -q)

step "2/8" "Python dependency audit"
(cd "$ROOT/backend" && .venv/bin/pip-audit --local)

step "3/8" "repository diff check"
(cd "$ROOT" && git diff --check)

step "4/8" "dashboard typecheck"
(cd "$ROOT/dashboard" && npm run typecheck)

step "5/8" "dashboard build"
(cd "$ROOT/dashboard" && npm run build)

step "6/8" "dashboard tests"
(cd "$ROOT/dashboard" && npm run test -- --run)

if [ "${SKIP_DOCKER:-0}" = "1" ]; then
  printf '\nDocker gates skipped.\n'
else
  step "7/8" "backend image"
  docker build -f "$ROOT/backend/Dockerfile" -t autotrader-v1-backend:gate "$ROOT"

  step "8/8" "dashboard image"
  docker build -f "$ROOT/dashboard/Dockerfile" -t autotrader-v1-dashboard:gate "$ROOT"
fi

printf '\n=== ALL QUALITY GATES PASSED ===\n'
