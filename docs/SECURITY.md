# TradeBot Security Boundary

Status: Phase B web-runtime policy, 2026-07-11

TradeBot is a local-only, single-operator application. Public or remote web exposure is unsupported.

## Browser response policy

FastAPI and the canonical Nginx dashboard both apply the same Content Security
Policy. Scripts, network connections, fonts, and top-level resources are
restricted to the application origin; images additionally allow `data:` URLs;
framing and object embedding are prohibited. React's existing dynamic style
attributes are temporarily allowed through `style-src-attr` while stylesheet
elements remain same-origin only.

The policy supplements the existing MIME-sniffing, frame, referrer, and API
no-store headers. It is regression-tested in `backend/tests/test_error_handling.py`
and `backend/tests/test_nginx_security.py`.

Compose publishes the dashboard only on `127.0.0.1`. Its Nginx proxy overwrites
forwarded client identity, and the backend trusts that header only when the
Compose-specific opt-in is enabled. Remote embeds, pop-outs, and data-supplied
external links are disabled in the renderer.

## Desktop boundary

Phase D will add Tauri capability restrictions and native navigation controls. Until then, CSP is the web-runtime boundary and does not make remote deployment supported.

## Secrets and sessions

Renderer assets must not contain a bootstrap secret or long-lived credential. Phase B uses a short-lived, per-launch session and in-memory renderer state. Persistent API keys remain scheduled for OS-backed secure storage in Phase D.

The legacy `/api/auth/token` compatibility route is simulation-only. Any
broker-backed runtime requires a non-default JWT signing secret and uses the
short-lived session bootstrap boundary, even while Autopilot authority is OFF.
