# v7b pre-mortem (written 2026-10-16, for the first v7b session on 2026-10-19)

Question: it is Monday 23:00 Israel, and the first v7b session went badly. What happened, how did we notice, and what limited the damage?

v7b deploys bridge-v7b at 7872c86 plus docs/reviews-only commits, with bridge sha256 7d313fd075d2.

Sendable identities and their caps:

| Identity | Size | Positions | Entries/day | Last entry |
|---|---|---|---|---|
| MOMO_HOD_V2 | ×1 | 3 | none | close−60m |
| ORB_V2 | ×0.25 | 1 | 1 | close−30m |
| VWAP_PB_V2 | ×0.5 | 1 | 2 | close−30m |
| GAP_GO_V2 | ×0.5 | 1 | 1 | close−30m |

Risk accepted by the owner on 2026-10-15:
- With six slots full: 2.125 % open risk and 63.75 % notional.
- The daily stop is −2 % realized.

## Failure scenarios

| # | What went wrong | How we notice | What limits it | Response |
|---|---|---|---|---|
| 1 | A V2 identity sized ×1 again (the v7b.1 bug class) | Monday checker: size = floor(policy × row) per SENT; the bridge's SENT line shows the shares | v7b.1 fix + v7b.2 tests at both hops (21 mutations killed); bridge ceiling 1 % risk / 25 % notional | `paper stop --identity <ID>`; undo v7b only when flat |
| 2 | An identity that should not send does | Block 8 dry run line `sendable identities = GAP_GO_V2, MOMO_HOD_V2, ORB_V2, VWAP_PB_V2`; every order carries `autotrader:<ID>:` | Exact-match allowlist in the bridge; SKIP lines for shadow-only names (seen live 10-16: `SKIP HST (AP_MOMO_HOD_V1)`) | Stop the install at Block 8 if the line differs |
| 3 | A V2 identity is fenced by mistake, or not fenced when it should be | Block 8 `fenced identities = none`; the bridge connect line turns CRITICAL while any identity row is stopped | Fences block new entries only; exits, stops, targets and the flatten keep working | `paper status`; only the owner resumes (`paper resume --identity <ID>`) |
| 4 | Six slots fill early and a bad market day follows | Notifier per-trade lines; 16:05 report P&L per identity | −2 % realized daily stop; stops resting at IBKR (GTC) | `/closeall` + `paper stop` only in a real emergency (the global stop has no resume) |
| 5 | The Gateway is connected but refuses data (as on 10-16, GW-HMDS-1016) | Owner check before install (W2c): launcher line 1 dated Mon ~02:30 + "Running in auto-restart mode"; live notifier page at 08:15 ET; screener timeouts | Nothing is sent without data; resting stops are server-side | 1) log out of the IBKR app and website; 2) restart IB Gateway (File → Exit, reopen, log in) |
| 6 | The Mac sleeps (battery + lid closed, MAC-SLEEP-1015) | `pmset -g batt` in the owner check; no bridge lines; no 15:45 flatten | Stops and targets rest at IBKR | AC power, lid open; after a sleep, check TWS for robot positions after 23:00 |
| 7 | A competing IBKR login (owner's phone or web) during the session | Screener "IBKR login active elsewhere" lines; the existing `ibkr_session` alert | Market data refused; no new entries can form | Log out of IBKR on the phone and the website |
| 8 | Entries/day counts attempts, so a rejected ORB/GAP order uses the day's only entry | Checker: entries/day per identity vs caps | By design (attempt = attempt); no over-trading possible | None; note it in the session line |
| 9 | The install starts late or is interrupted mid-way | Clock; Blocks 6–9 not completed | Hard stop 14:20 Israel; never start after 13:50; the robot stays stopped until Block 9 | If stopped after Block 7 and before 14:20: run the Undo (it restores 664c93b and starts the robot) |
| 10 | The bridge pin mismatches the file on disk | Block 7 `shasum` ≠ 7d313fd075d2; the supervisor refuses to launch the bridge | Supervisor pin check | Stop; do not start the robot; tell Claude |
| 11 | An undo is wanted mid-session | — | **Forbidden mid-session.** Undo only when flat with no working robot orders | Use `paper stop --identity <ID>` for the faulty identity instead |
| 12 | Auto-restart did not happen Monday 02:30 (first real test after the Sunday login) | Owner check (W2c) | The Gateway still runs from Sunday's login | Do not install v7b until the Gateway is logged in and port 7497 is listening; tell Claude |

## Go / no-go before Block 1 on Monday
- [ ] The Saturday SESSION line for v7a 2026-10-16 says PASS (W3).
- [ ] The ledger has `GATES Docker v7b.2 PASS`.
- [ ] The owner check (W2c) passed: Gateway restarted about 02:30, port listening, AC power.
- [ ] The time is between 01:30 and 13:50 Israel, and not 02:25–02:45.
- [ ] The owner is awake and has read this page.

## Watch during the first v7b session (Claude, read-only)
- The first V2 orders from the 09:35 ET bar (16:35 Israel); each SENT carries `autotrader:<ID>:` and is attributed on FILL.
- Shares per SENT = floor(policy × row): ORB ×0.25, VWAP/GAP ×0.5, MOMO ×1.
- Entries/day ≤ caps; no V2 entry after close−30m; no MOMO entry after close−60m.
- The 15:45 ET flatten closes every robot position; nothing is "still open after flatten".
- The checker (built on ops-p1b) runs Monday night; its output is committed under docs/reviews.
