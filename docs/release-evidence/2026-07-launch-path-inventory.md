# Phase A Backend Launch-Path Inventory

Date: 2026-07-09
Phase: A - Truth, Safety, and Product Consolidation
Stages: A3 - Launch-path inventory, A4 - Force one-worker runtime

## Summary

The backend is currently a stateful FastAPI runtime. Its lifespan starts or owns
database initialization, IBKR connectivity, alert loops, WebSocket state,
market heartbeat work, notification state, and AI optimization/learning loops.
Until those responsibilities are separated into coordinated services, every
supported launch path must start exactly one backend worker.

## Launch Matrix

| Source | Intended environment | Command or source | Host bind | Reload | Baseline worker behavior | Phase A worker behavior | Safety result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `Dockerfile` | combined dashboard/backend container image | `uvicorn main:app ...` | `0.0.0.0` | no | `--workers ${WORKERS:-2}` | `--workers 1`, no `WORKERS` override | safe for A4 |
| `backend/Dockerfile` | backend-only container image | `uvicorn main:app ...` | `0.0.0.0` | no | `--workers ${WORKERS:-2}` | `--workers 1`, no `WORKERS` override | safe for A4 |
| `docker-compose.yml` | local container stack | backend image command | internal Docker network only | no | passed `WORKERS: ${WORKERS:-2}` | no worker environment override; Dockerfile pins 1 | safe for A4 |
| `README.md` | local developer backend | `uvicorn main:app --host 0.0.0.0 --port 8000 --reload` | `0.0.0.0` | yes | Uvicorn default one worker | unchanged command plus explicit no-multi-worker warning | acceptable dev path |
| `backend/main.py` | direct local debug entry point | `uvicorn.run("main:app", host=cfg.HOST, port=cfg.PORT, reload=True)` | `cfg.HOST`, default `0.0.0.0` | yes | Uvicorn default one worker | unchanged direct dev path | acceptable dev path |
| `sessions/phase2-paper-soak-runbook.md` | paper-soak operator runbook | `python -m uvicorn main:app --reload` | Uvicorn default `127.0.0.1` | yes | Uvicorn default one worker | unchanged one-worker runbook command | acceptable paper path |
| `docs/DEPLOYMENT.md` supervisor example | stale deployment example | `uvicorn main:app ...` | `0.0.0.0` | no | `--workers 4` | `--workers 1` with stateful-runtime warning | safe for A4 |
| `docs/DEPLOYMENT.md` systemd example | stale deployment example | `uvicorn main:app ...` | `0.0.0.0` | no | `--workers 4` | `--workers 1` | safe for A4 |
| `.github/workflows/ci.yml` | quality gates | no backend server launch | n/a | n/a | no backend server launch | unchanged | no worker risk |
| `dashboard/nginx.conf` | reverse proxy only | no backend launch | n/a | n/a | no backend server launch | unchanged | no worker risk |

No backend wrapper script or process-manager file was found that launches the
backend twice. The only shell/script directory added in this phase is
`scripts/check_workspace_hygiene.py`, which does not launch the backend.

## Verification

Static launch-manifest regression tests were added in
`backend/tests/test_launch_manifests.py`.

The tests use a small regex parser for `--workers N` and `--workers=N` forms,
plus a check for common worker override environment names. They prove:

- both Dockerfiles contain `--workers 1`;
- Docker launch manifests do not expose a worker-count environment override;
- compose no longer exposes a worker-count environment override;
- operational docs no longer recommend `--workers 2`, `--workers 4`, or
  non-1 worker values for backend startup.

Runtime duplicate-instance prevention is intentionally deferred to A5/A6. A4
only forces the supported launch manifests to one worker.

## A3 Checklist

| Item | Result | Evidence |
| --- | --- | --- |
| Docker launch files inventoried | done | `Dockerfile`, `backend/Dockerfile`, `docker-compose.yml` |
| Local dev launch paths inventoried | done | `README.md`, `backend/main.py` |
| Paper-soak launch path inventoried | done | `sessions/phase2-paper-soak-runbook.md` |
| CI and proxy files checked | done | `.github/workflows/ci.yml`, `dashboard/nginx.conf` |
| Worker count, host bind, and reload behavior recorded | done | launch matrix above |

## A4 Checklist

| Item | Result | Evidence |
| --- | --- | --- |
| Root Dockerfile pinned to one worker | done | `Dockerfile` uses `--workers 1` |
| Backend Dockerfile pinned to one worker | done | `backend/Dockerfile` uses `--workers 1` |
| Compose worker override removed | done | `docker-compose.yml` no longer sets `WORKERS` |
| Deployment examples stop recommending worker fan-out | done | `docs/DEPLOYMENT.md` uses `--workers 1` |
| Developer docs warn against multiple workers | done | `README.md` single-process warning |
| Startup logs include worker mode | done | `backend/startup.py` logs single-process worker mode |
| Regression coverage added | done | `backend/tests/test_launch_manifests.py` |
