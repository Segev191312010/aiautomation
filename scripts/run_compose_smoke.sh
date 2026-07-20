#!/usr/bin/env bash
# Run the disposable pre-T Compose smoke gate.
#
# Usage: CANDIDATE_SHA=$(git rev-parse HEAD) scripts/run_compose_smoke.sh
# The candidate is mandatory and must be the checked-out commit.  The script
# creates a unique Compose project, uses Docker-assigned ports, checks FastAPI
# and nginx, and always tears down its project/volumes on exit.
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

die() { printf 'compose-smoke: %s\n' "$*" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || die "docker is required"
docker compose version >/dev/null 2>&1 || die "docker compose plugin is required"

candidate=${CANDIDATE_SHA:-}
[[ "$candidate" =~ ^[0-9a-fA-F]{40}$ ]] || die "CANDIDATE_SHA must be a 40-character commit OID"
head_oid=$(git rev-parse HEAD)
[[ "$candidate" == "$head_oid" ]] || die "CANDIDATE_SHA ($candidate) does not match HEAD ($head_oid)"
git cat-file -e "${candidate}^{commit}" 2>/dev/null || die "candidate commit is not available"

# A UUID-like project name avoids collisions with developer stacks and other CI
# jobs.  It is never persisted as an application secret.
if command -v uuidgen >/dev/null 2>&1; then
  suffix=$(uuidgen | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9' | cut -c1-12)
else
  suffix=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
fi
project="tradebot-smoke-${suffix}"
compose=(docker compose --project-name "$project" -f docker-compose.test.yml)
cleanup() {
  set +e
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1
}
trap cleanup EXIT INT TERM

printf 'compose-smoke: candidate %s, project %s\n' "$candidate" "$project"
"${compose[@]}" config --quiet
"${compose[@]}" up --build -d

wait_for_health() {
  local service=$1 state
  for _ in $(seq 1 60); do
    state=$("${compose[@]}" ps --format '{{.Service}} {{.Health}}' 2>/dev/null | awk -v s="$service" '$1 == s {print $2}')
    [[ "$state" == healthy ]] && return 0
    [[ "$state" == unhealthy || "$state" == exited || "$state" == dead ]] && {
      "${compose[@]}" logs "$service" >&2 || true
      return 1
    }
    sleep 2
  done
  "${compose[@]}" ps >&2
  return 1
}
wait_for_health backend || die "backend did not become healthy"
wait_for_health dashboard || die "dashboard did not become healthy"

# Verify the service in its network namespace (not merely the Docker healthcheck).
"${compose[@]}" exec -T backend curl -fsS http://localhost:8000/api/health >/dev/null || die "FastAPI health failed"

port=$(${compose[@]} port dashboard 80 | awk -F: 'END {gsub(/\r/, "", $NF); print $NF}')
[[ "$port" =~ ^[0-9]+$ && "$port" != 0 ]] || die "could not discover nginx host port"
curl --fail --silent --show-error --retry 10 --retry-connrefused "http://127.0.0.1:${port}/health" >/dev/null || die "nginx health failed"

printf 'compose-smoke: PASS (candidate %s; nginx port %s)\n' "$candidate" "$port"
