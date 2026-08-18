# Browser Notification Drill — `c850786a209b3ef96cf5bdbe12630ca3e56bbf56`

This is an operator-run drill for the secure notification path. It does not
authorize PAPER or LIVE trading. Run it only against a disposable/test account
and record the exact deployed commit, browser, origin, and UTC timestamps.

## Preconditions

- [ ] The browser origin is HTTPS (or an explicitly trusted local-development
      origin) and the service worker is active.
- [ ] The backend is configured with Web Push enabled, valid VAPID keys, and an
      allowlisted provider host. Never paste private keys or subscription keys
      into this artifact.
- [ ] Two test accounts exist: `notification-user-a` and
      `notification-user-b`. Use separate browser profiles or private windows.
- [ ] The backend and dashboard report the exact SHA in the deployment metadata.
- [ ] Capture only status codes, booleans, counts, and timestamps; redact
      endpoint URLs, `p256dh`, `auth`, tokens, and VAPID material.

## Drill A — Ownership and enrollment

1. In profile A, sign in as user A and open the notification settings.
2. Enable browser push and confirm the browser permission is **Granted**.
3. Confirm the UI reports one registered subscription and browser push enabled.
4. In profile B, sign in as user B. Attempting to register A's subscription
   endpoint must return `409`; querying its status must return
   `{ "registered": false }`; deleting it must return `404`.
5. Return to profile A, disable browser push. The subscription count must become
   zero and the persisted `browser_push` preference must become `false`.

**Pass:** no endpoint is visible to the other user, no cross-user mutation is
possible, and disabling push removes the owned record or fails loudly.

## Drill B — Preference gate and connected-tab fallback

1. In profile A, re-enable push and use the dashboard **Send test notification**
   action. Record the result and UTC timestamp.
2. Set the stored browser-push preference to `false` while leaving the browser
   tab open. Trigger a test alert for A. No background Web Push notification may
   be delivered; the API/result must identify the preference skip or otherwise
   fail closed.
3. With the tab still open, set in-app/native notifications on and trigger the
   same test alert. The connected tab may show one native `Notification` and the
   in-app alert history may update. This is not proof of background delivery.
4. Close every A tab, wait for the alert event, and verify that a notification
   appears only when browser push is enabled and an owned subscription exists.

**Pass:** stored preferences are authoritative; a connected tab cannot bypass
the browser-push preference; background delivery is proven only with all tabs
closed and a provider delivery result.

## Drill C — Permission denial and fail-closed behavior

1. In a fresh profile, deny notification permission at the browser prompt (or
   set the origin permission to **Blocked**).
2. Attempt to enable browser push. The UI must report a blocked/not-granted
   error, remain unsubscribed, and must not call the subscription endpoint.
3. With server Web Push readiness disabled or incomplete, attempt to enable push.
   The UI must report that the server is not configured; no browser subscription
   may remain registered after the failed attempt.
4. Restore readiness and permission only after recording the failed result.

**Pass:** denial and missing configuration produce a visible failure and leave
no orphaned browser or backend subscription.

## Evidence record

```text
Code SHA: c850786a209b3ef96cf5bdbe12630ca3e56bbf56
Environment/origin:
Browser + version:
Backend status endpoint ready/enabled (no secrets):
UTC start/end:
Drill A: PASS / FAIL — notes:
Drill B: PASS / FAIL — notes:
Drill C: PASS / FAIL — notes:
Provider delivery result/count (no endpoint):
Operator:
Evidence bundle path:
```

Any failed, ambiguous, or unrecorded step is **FAIL**. Do not infer success
from a connected-tab notification, a browser permission prompt, or a `200` API
response alone. A failed drill blocks notification readiness and must not be
used to justify LIVE trading.

## Automated coverage reference

The corresponding regression suites are:

- `backend/tests/test_push_notifications.py`
- `dashboard/src/hooks/__tests__/useNotifications.test.tsx`
- `dashboard/src/services/__tests__/browserPush.test.ts`
- `dashboard/src/components/auth/__tests__/AuthGuard.push.test.tsx`

These tests validate contracts and failure handling; they do not replace the
real-browser/provider drill above.
