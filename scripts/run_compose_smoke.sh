#!/usr/bin/env bash
# Run the disposable pre-T Compose smoke gate.
#
# Usage: scripts/run_compose_smoke.sh --candidate "$(git rev-parse HEAD)"
# The candidate is mandatory and must be the checked-out commit.  The script
# creates a unique Compose project, uses Docker-assigned ports, checks FastAPI
# and nginx, and always tears down its project/volumes on exit.
set -Eeuo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

die() { printf 'compose-smoke: %s\n' "$*" >&2; exit 1; }
usage() {
  printf '%s\n' 'usage: scripts/run_compose_smoke.sh --candidate 40-char-oid'
}

candidate_arg=
while (($#)); do
  case "$1" in
    --candidate)
      (($# >= 2)) || die "--candidate requires a value"
      [[ -z "$candidate_arg" ]] || die "--candidate may be specified only once"
      candidate_arg=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

# CANDIDATE_SHA remains a compatibility path for older automation, but the
# governance interface and the aggregate pre-T gate use --candidate.  If both
# are supplied they must identify exactly the same commit.
candidate_env=${CANDIDATE_SHA:-}
if [[ -n "$candidate_arg" && -n "$candidate_env" && "$candidate_arg" != "$candidate_env" ]]; then
  die "--candidate and CANDIDATE_SHA disagree"
fi
candidate=${candidate_arg:-$candidate_env}
[[ "$candidate" =~ ^[0-9a-f]{40}$ ]] || die "--candidate must be a lowercase 40-character commit OID"

head_oid=$(git rev-parse HEAD)
[[ "$candidate" == "$head_oid" ]] || die "candidate ($candidate) does not match HEAD ($head_oid)"
git cat-file -e "${candidate}^{commit}" 2>/dev/null || die "candidate commit is not available"

command -v docker >/dev/null 2>&1 || die "docker is required"
docker compose version >/dev/null 2>&1 || die "docker compose plugin is required"

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
