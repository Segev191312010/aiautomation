#!/usr/bin/env bash
# =============================================================================
# ULTRAPLAN v4 — full quality-gate runner
#
# Runs every gate that must be green before a commit / before the LIVE flip:
#   1. Backend pytest        (via backend/.venv)
#   2. Dashboard typecheck    (tsc --noEmit)
#   3. Dashboard build        (tsc && vite build)
#   4. Dashboard vitest       (npx vitest run)
#   5. Backend docker build   (backend/Dockerfile)
#   6. Full-stack docker build (root Dockerfile — dashboard + backend image)
#
# Usage:
#   scripts/run_quality_gates.sh             # all gates
#   SKIP_DOCKER=1 scripts/run_quality_gates.sh   # skip the two docker builds
#
# Exit code 0 only if EVERY gate passed. Any failure aborts immediately
# (set -e) — nothing should be committed on a red gate.
# =============================================================================
set -euo pipefail

# Resolve repo root from this script's location so it runs from anywhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

VENV_PYTEST="${REPO_ROOT}/backend/.venv/bin/pytest"

step() { printf '\n=== [%s] %s ===\n' "$1" "$2"; }

# --- 1. Backend pytest -------------------------------------------------------
step "1/6" "backend pytest (backend/.venv)"
if [ ! -x "${VENV_PYTEST}" ]; then
  echo "ERROR: ${VENV_PYTEST} not found. Create it: python3.11 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt" >&2
  exit 1
fi
( cd "${REPO_ROOT}/backend" && .venv/bin/pytest -q )

# --- 2. Dashboard typecheck --------------------------------------------------
step "2/6" "dashboard typecheck (tsc --noEmit)"
( cd "${REPO_ROOT}/dashboard" && npm run typecheck )

# --- 3. Dashboard build ------------------------------------------------------
step "3/6" "dashboard build (tsc && vite build)"
( cd "${REPO_ROOT}/dashboard" && npm run build )

# --- 4. Dashboard vitest -----------------------------------------------------
step "4/6" "dashboard vitest (npx vitest run)"
( cd "${REPO_ROOT}/dashboard" && npx vitest run )

# --- 5 & 6. Docker builds ----------------------------------------------------
if [ "${SKIP_DOCKER:-0}" = "1" ]; then
  echo
  echo "SKIP_DOCKER=1 set — skipping the two docker build gates (5/6, 6/6)."
else
  step "5/6" "backend docker build (backend/Dockerfile)"
  docker build -f "${REPO_ROOT}/backend/Dockerfile" -t trading-backend:gate "${REPO_ROOT}"

  step "6/6" "full-stack docker build (root Dockerfile)"
  docker build -f "${REPO_ROOT}/Dockerfile" -t trading-fullstack:gate "${REPO_ROOT}"
fi

echo
echo "=== ALL QUALITY GATES PASSED ==="
