# WebSocket Isolation Drill Evidence

This deterministic harness checks the routing contract locally:

```bash
python scripts/run_ws_isolation_drill.py
```

Record the JSON output and the exact immutable application SHA with the
release evidence. A passing harness proves the manager's user-bucket routing,
unknown-event fail-closed behavior, missing-owner drop, and public-event
fanout. It does **not** prove JWT authentication, origin checks, browser
handshake behavior, reconnect handling, or deployment configuration.

## Required authenticated browser drill

- SHA:
- Date/time (UTC):
- Environment / URL:
- User A and User B test identities (non-secret IDs only):
- Authentication and origin checks: PASS / FAIL
- User A receives User A private event: PASS / FAIL
- User B receives zero User A private events: PASS / FAIL
- User B receives User B private event: PASS / FAIL
- Public quote/bar fanout reaches both: PASS / FAIL
- Reconnect preserves isolation: PASS / FAIL
- Raw logs/screenshots:
- Operator:
- Reviewer:

Any private-event leak or unresolved reconnect discrepancy is a failed gate;
do not promote the build or discuss LIVE authorization.
