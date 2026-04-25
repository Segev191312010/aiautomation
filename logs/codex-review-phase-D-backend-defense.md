# Phase D — Backend Defense (CORS + WS Origin) — Codex Review

**Reviewed:** 2026-04-19
**Diff range:** f7ecabc, 52a5f49 + fix fb7f4ff
**Reviewer:** codex (mcp__codex-review__codex)

## Commits reviewed

- f7ecabc security(backend): env-driven CORS allowlist with explicit methods (D1)
- 52a5f49 security(backend): env-driven WebSocket Origin allowlist (D2)
- fb7f4ff fix(backend): strict-prod CORS — env wins, dev origins are fallback only (D-fix)

## Verdict

- CRITICAL: none
- MAJOR (2 flagged, both FIXED in fb7f4ff):
  - **Dev origins permanently trusted** — `dev_defaults | env_extras` meant `FRONTEND_ORIGIN` could never tighten prod policy. FIXED: env is authoritative when set; dev defaults apply only when env is unset.
  - **HEAD dropped from allow_methods** — FastAPI auto-serves HEAD for every GET; authenticated cross-origin HEAD preflights would fail. FIXED: HEAD added to explicit list.
- MINOR (2 flagged, accepted as documented tradeoffs):
  - Operator mistakes (trailing slash, `null` origin, typos) fail closed — not fixed. Fail-closed is the correct default for a security boundary; normalizing origins creates ambiguity. No change.
  - Tests don't exercise middleware wiring at import-time — partially mitigated: `test_preflight_with_allowed_origin_returns_cors_headers` does go through the live middleware. The env-driven tests cover the helper only.
- LGTM:
  - No parser-based bypass (strip + exact match).
  - WS_ALLOW_NO_ORIGIN is default-deny and only accepts explicit truthy values.
  - Origin policy is aligned between HTTP and WS via the shared helper.
  - Origin is treated as defense-in-depth, not an auth boundary (JWT still required).

## Follow-up

None blocking. If the deployment target adds strict-CSP plus a production Origin different from the dev one, document the `FRONTEND_ORIGIN` export in the Stage 8 deployment runbook so prod does not silently fall through to the localhost allowlist.
