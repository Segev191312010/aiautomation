# Browser Validation Checklist — `d104372e89f11bbed2068d1f52ccb98a69fea268`

This checklist records manual browser evidence for the downstream execution
build. It is intentionally separate from unit, integration, and deterministic
harness results. A passing automated test does not mark a manual item passed.
This checklist does not authorize PAPER or LIVE trading.

## Run metadata and preconditions

- [ ] Record the exact deployed SHA shown by the application: `d104372e89f11bbed2068d1f52ccb98a69fea268`.
- [ ] Record environment URL, UTC start/end, browser/version, OS, and operator.
- [ ] Use two disposable authenticated accounts in separate browser profiles.
- [ ] Confirm HTTPS (or explicitly approved local development origin), valid
      service-worker registration, and no browser console errors before testing.
- [ ] Redact tokens, cookies, VAPID private material, push endpoints, and
      account secrets from screenshots and logs.

## A. TradingView single-symbol chart

1. [ ] Open a single-symbol chart (for example `NASDAQ:AAPL`) from the Markets
      page and confirm the hosted TradingView widget renders, not a blank iframe.
2. [ ] Change symbol and each supported timeframe; confirm the URL/query changes
      and the displayed symbol/interval match the selection.
3. [ ] Refresh and open the chart in a new tab; confirm it loads without relying
      on a stale sidecar process.
4. [ ] Inspect the browser console/network panel for blocked iframe, CSP,
      mixed-content, or failed widget requests; any unresolved error is a fail.
5. [ ] Record the visible feed status (real-time, delayed, or unavailable), the
      exchange/data entitlement used, and a UTC observation timestamp. Never infer
      zero delay from a TradingView Pro subscription alone.

**Pass criteria:** the hosted widget renders and remains usable after refresh;
the observed latency/entitlement is explicitly recorded. A chart that renders
but has unverified real-time entitlement is **not** a real-time pass.

## B. Multi-symbol chart fallback

1. [ ] Open a multi-symbol view with two symbols and verify the configured,
      allowlisted `ib_chart` sidecar loads.
2. [ ] Verify symbols are normalized/encoded and the nine-symbol limit is
      enforced; malformed or over-limit input must fail visibly.
3. [ ] Stop or make the sidecar unavailable in a test environment. The UI must
      report multi-symbol unavailability without breaking single-symbol charts.

**Pass criteria:** fallback behavior is explicit and bounded; do not describe
this SHA as using a unified TradingView engine for multi-symbol charts.

## C. Browser push enrollment and preference gate

1. [ ] In profile A, enable browser notifications; record permission state,
      subscription count, and UTC result (never record endpoint/key values).
2. [ ] Trigger a test alert with A's browser-push preference enabled. With all A
      tabs closed, verify one background notification and record provider/result
      status.
3. [ ] Disable A's browser-push preference, leave a subscription present if the
      UI permits, and trigger another alert. Confirm no background notification
      and a visible preference-skip/failure result.
4. [ ] In profile B, verify A's subscription cannot be listed, registered,
      deleted, or used by B. Record only status codes and booleans.
5. [ ] Deny notification permission in a fresh profile. Enabling push must fail
      visibly and leave no backend subscription.

**Pass criteria:** stored preferences are authoritative, ownership is enforced,
and background delivery is proven with tabs closed. A connected-tab native
`Notification` is not proof of background push delivery.

## D. Two-user WebSocket isolation

1. [ ] Authenticate profiles A and B and record successful handshake/origin
      checks without exposing tokens.
2. [ ] Emit a private fill, signal, position update, order modification, and
      alert for A. A receives each; B receives zero A-private events.
3. [ ] Emit equivalent private events for B. B receives each; A receives zero
      B-private events.
4. [ ] Emit a public quote/bar/status event and confirm both profiles receive it.
5. [ ] Reconnect both browsers and repeat one private event in each direction;
      isolation must survive reconnect.
6. [ ] Send an unknown or missing-owner event in a test environment. It must be
      dropped or rejected, never broadcast to authenticated users.

**Pass criteria:** any cross-user private event, unexplained reconnect loss, or
unknown-event broadcast is a failed security gate.

## Evidence record

```text
Code SHA: 4d8bbda2c7bb97018db8da9be4bda86470b34dbb
Environment / URL:
Browser + version / OS:
UTC start / end:
Operator / reviewer:
TradingView single-symbol: PASS / FAIL — notes:
Feed entitlement + observed latency:
Multi-symbol fallback: PASS / FAIL / NOT RUN — notes:
Push enrollment + closed-tab delivery: PASS / FAIL — notes:
Push preference/ownership/denial: PASS / FAIL — notes:
Two-user WebSocket isolation: PASS / FAIL — notes:
Screenshots/log bundle (redacted):
Automated references and SHAs:
```

Any unchecked, ambiguous, or unrecorded item is **FAIL**, **NOT RUN**, or
**UNKNOWN**—never silently converted to pass. Attach this completed record to
the release/re-audit for the same immutable SHA.

## Automated coverage (does not replace this checklist)

- TradingView URL contract: `dashboard/src/utils/__tests__/tradingView.test.ts`
- Push contracts: `backend/tests/test_push_notifications.py`,
  `dashboard/src/hooks/__tests__/useNotifications.test.tsx`,
  `dashboard/src/services/__tests__/browserPush.test.ts`
- Deterministic WebSocket routing: `scripts/run_ws_isolation_drill.py`
- Browser/authenticated WebSocket behavior and provider delivery require the
  manual checks above.
