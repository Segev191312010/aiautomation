# Phase B Auth And Session Baseline

Date: 2026-07-11

Baseline commit: `b2c01ddf88929c926cd67fbf8105dc715a0fde9c`

Stages: B5-B7

## Historical Baseline

At the recorded commit, the mounted `App.tsx` called `fetchAuthToken()` during
startup and silently continued if token minting failed. The request sent
`VITE_JWT_BOOTSTRAP_SECRET` from renderer code to `POST /api/auth/token`, then
stored the returned JWT under `localStorage.auth_token`.

`components/auth/AuthGuard.tsx` existed but was not mounted around the product
routes. It independently repeated the bootstrap flow, restored tokens from
`localStorage`, and switched between fake login and registration screens. Those
screens collected credentials but ignored them and minted the same demo token;
there were no matching production login or registration endpoints.

The tracked renderer references were:

- `src/services/api/auth.ts`: build-time `VITE_JWT_BOOTSTRAP_SECRET` access;
- `src/services/api/client.ts`: persisted JWT reads/writes and 401 cleanup;
- `src/components/auth/AuthGuard.tsx`: persisted-token restore;
- `src/components/auth/LoginPage.tsx` and `RegisterPage.tsx`: fake flows;
- auth/client tests which encoded the same persistence behavior.

An ignored, user-owned `dashboard/.env.local` still defines the legacy Vite
variable. Its value was not inspected or recorded. Phase B does not modify or
delete that local file; after the source removal it is inert and should be
removed by the operator.

## Phase B Boundary

B6/B7 replace the historical flow with:

- `POST /api/session/bootstrap`, returning a 15-minute bearer token and an
  explicit UTC expiry;
- loopback-only transport for every bootstrap request;
- a launcher-provided `TRADEBOT_SESSION_BOOTSTRAP_TOKEN` capability for desktop
  and any LIVE/non-paper launch;
- a zero-config fallback only for loopback paper or simulation development;
- runtime renderer injection through a window value or scrubbed URL fragment,
  never a `VITE_*` build variable;
- a Zustand in-memory session store with no JWT persistence;
- a mounted `AuthGuard` that never mounts the trading workspace before a valid
  session and shows an explicit bootstrap failure/expiry boundary;
- fail-closed protected requests and global Zustand store reset on 401, 403,
  local expiry, or explicit session reset;
- removal of the fake login and registration components.

The legacy backend `/api/auth/token` route is not used by the Phase B renderer
and is restricted to simulation compatibility. Broker-backed operation rejects
it, requires a non-default JWT signing key, and uses the short-lived per-launch
session boundary. No renderer asset consumes the legacy secret.

## Verification Searches

From `dashboard/`:

```powershell
rg -n "VITE_JWT_BOOTSTRAP_SECRET|fetchAuthToken|setAuthToken|remember_me" src
rg -n "auth_token" src
npm run build
rg -n "VITE_JWT_BOOTSTRAP_SECRET|JWT_BOOTSTRAP_SECRET|auth_token" dist
```

The first search must have no tracked source hits. `auth_token` may appear only
in negative regression assertions, never in production code or built assets.
Remaining `localStorage` uses are non-secret operator preferences such as theme,
watchlists, alert sound, and notification settings; session credentials are not
among them.
