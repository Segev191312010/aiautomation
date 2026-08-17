# Stage 9A Pre-Live Fault Matrix

**Status:** Draft / limited local containment checks executed  
**Date:** 2026-07-27  
**Gate:** Every applicable deterministic row must pass on an identified clean
artifact before PAPER soak or LIVE canary.

## Evidence Contract

Each execution records:

- test ID and pre-registered expected result;
- commit, image digest, schema/migration checksum;
- sanitized configuration and policy fingerprints;
- account/environment/client and TWS/IBGW/API versions;
- model/prompt/router fingerprints where AI is involved;
- start/end time and injected fault;
- durable intent/order/fill/protection/risk/reconciliation records;
- broker-side screenshots/export or machine-readable evidence;
- alerts and measured detection/containment/recovery time; and
- reviewer/approver.

Passing unit tests alone does not satisfy a broker-contract or operational-drill
row.

## A. Intent, Ownership, and Submission

| ID | Injection | Required invariant / expected result | Level | Status |
|---|---|---|---|---|
| EXE-01 | 20 coroutine callers, same key/payload | One intent/claim; all callers receive same identity | integration | Not built |
| EXE-02 | Separate processes, same key/payload | One intent/claim under DB contention | multiprocess | Not built |
| EXE-03 | Same key, altered payload | Conflict; zero broker calls | integration | Not built |
| EXE-04 | Crash before durable claim | No broker call; safe new request allowed | fault | Not built |
| EXE-05 | Crash after claim, before `SUBMITTING` | Recover or expire by legal transition; no broker call | fault | Not built |
| EXE-06 | Crash after `SUBMITTING`, before adapter | `UNKNOWN`/reconcile; no automatic retry | fault | Not built |
| EXE-07 | Broker accepts, adapter times out | `UNKNOWN`; reconcile attaches broker identity; one effect | paper broker | Not built |
| EXE-08 | Broker rejects synchronously | Explicit broker rejection, not success/unknown | contract | Not built |
| EXE-09 | Duplicate `orderRef` accepted by fake broker | Local system still prevents duplicate effect | contract | Not built |
| EXE-10 | API worker count 2 | Startup exits before lifespan/broker connection | startup | Helper + lifecycle unit pass; deployed-worker proof pending |
| EXE-11 | Two executor replicas/lease race | Only approved owner claims; stale owner stops | deployment/fault | Not built |
| EXE-12 | Lease expires before mutation | No broker call; readiness false; alert | fault | Not built |
| EXE-13 | Stale fencing epoch attempts DB write | CAS rejects transition | integration | Not built |
| EXE-14 | Configured client ID occupied | Fail unready; no fallback identity | paper broker | Not built |
| EXE-15 | Account sync incomplete/degraded | Fail unready; no broker mutation | paper broker | Not built |
| EXE-16 | Manual/order/rule/AI/TV/MCP/exit/recovery source | Every path creates typed durable intent | architecture/E2E | Not built |
| EXE-17 | New direct `placeOrder`/`cancelOrder` callsite | Static CI test fails | architecture | Not built |
| EXE-18 | Shared rate DB unavailable/locked past retry | Entry denied and metric/log emitted within bounded time | integration | Passed locally with held write lock; deployment fault pending |
| EXE-19 | Eight independent PIDs race for three per-symbol slots | Exactly three acquire against one SQLite file | multiprocess | Passed locally |
| EXE-20 | Blank/in-memory SQLite configured | Startup and limiter fail closed | table-driven | Passed locally |
| EXE-21 | SIM startup or configured real-money broker/manual connect | Zero IBKR connection/reconnect calls | unit | Passed locally for flags and known live ports; account assertion pending |

## B. Broker Protection and Order Lifecycle

| ID | Injection | Required invariant / expected result | Level | Status |
|---|---|---|---|---|
| PRO-01 | Normal full fill | Acknowledged stop covers 100% cumulative fill | paper broker | Not built |
| PRO-02 | Parent partial fills in fragments | Coverage equals cumulative fill after every fragment | paper broker | Not built |
| PRO-03 | Final transmitting child rejected | Entry remainder cancelled; fill contained; entries locked | paper broker | Not built |
| PRO-04 | Child remains `Inactive`/unacknowledged past SLA | Same containment as PRO-03 | paper broker | Not built |
| PRO-05 | Crash after parent stage, before final child transmit | No silent naked exposure; reconcile/contain | paper broker/fault | Not built |
| PRO-06 | Crash after child call, before local save | Reconcile graph; no duplicate child | paper broker/fault | Not built |
| PRO-07 | Stop replacement result unknown | Old protection preserved until replacement proven; reconcile | paper broker | Not built |
| PRO-08 | Late entry fill after cancel acknowledgment | Protection expands or containment runs | paper broker | Not built |
| PRO-09 | Missing/duplicate/out-of-order order-status events | Executions dedupe by `execId`; state converges | contract | Not built |
| PRO-10 | Commission arrives after fill | Fee/P&L updates once without reopening terminal state | contract | Not built |
| PRO-11 | Stop and discretionary exit fire together | No position reversal; one coordinated reduce graph | paper broker | Not built |
| PRO-12 | Exit partially fills then cancels | Residual broker quantity remains visible/protected | paper broker | Not built |
| PRO-13 | Gap through stop | No price guarantee claim; actual slippage recorded/alerted | simulation/paper | Not built |
| PRO-14 | Halt while exposed | Entries lock; protection state honest; operator procedure works | drill | Not built |

## C. Reconciliation and Readiness

| ID | Injection | Required invariant / expected result | Level | Status |
|---|---|---|---|---|
| REC-01 | Startup with clean matching broker/local state | Readiness only after complete snapshot/cursors | paper broker | Not built |
| REC-02 | Broker-only manual position | Durable discrepancy; entries locked; no silent adoption | paper broker | Not built |
| REC-03 | Broker-only working order | Classified/quarantined; protection not globally cancelled | paper broker | Not built |
| REC-04 | Local-only position/order | Discrepancy; no blind deletion/retry | integration | Not built |
| REC-05 | Offline completed fill | Execution overlap finds/dedupes it | paper broker | Not built |
| REC-06 | Execution at overlap boundary | Seen exactly once | contract | Not built |
| REC-07 | Outage exceeds broker retention | Readiness stays false pending statement evidence | drill | Not built |
| REC-08 | Wrong account returned | Immediate unready/entry lock/critical alert | paper broker | Not built |
| REC-09 | Reused order ID across client/account | Identity match does not cross-associate | contract | Not built |
| REC-10 | Reconnect/TWS restart mid-order | Readiness false until convergence; no retry | paper broker/fault | Not built |
| REC-11 | Reconciliation task dies | Supervisor detects, locks entries, alerts | fault | Not built |
| REC-12 | Old orphan with no local order ID | Quarantined and searched by correlation; never reaped to retryable | integration | Not built |
| REC-13 | Restore old DB against newer broker state | Disarmed, discrepancies recorded, no replay | restore drill | Not built |

## D. Continuous Account Risk

| ID | Injection | Required invariant / expected result | Level | Status |
|---|---|---|---|---|
| RSK-01 | Equity crosses warning threshold | Durable warning/metric/alert; authority unchanged per policy | deterministic | Not built |
| RSK-02 | Equity crosses entry-lock threshold | Lock commits before selective cancellation | deterministic/fault | Not built |
| RSK-03 | Crash immediately after lock commit | Restart remains locked; reconcile resumes response | fault | Not built |
| RSK-04 | Stale/missing/NaN/infinite account value | New entries locked; reduce/protect remains available | table-driven | Not built |
| RSK-05 | Wrong account/currency sample | Rejected; readiness false; prior baseline unchanged | table-driven | Not built |
| RSK-06 | Database unavailable during risk read/write | Fail closed for entries; alert | fault | Not built |
| RSK-07 | Deposit/withdrawal | Only approved cash-flow adjustment changes baseline | deterministic | Not built |
| RSK-08 | Manual/external trade | Included in account equity and discrepancy/attribution | paper broker | Not built |
| RSK-09 | Midnight/session boundary | Existing latch persists; new session follows signed policy | time simulation | Not built |
| RSK-10 | DST/holiday/weekend transition | One deterministic boundary, no double/reset gap | time simulation | Not built |
| RSK-11 | Reset missing/wrong/replayed step-up | Reset denied | security/integration | Not built |
| RSK-12 | Reset before reconcile/cooldown/cause note | Reset denied with auditable reason | integration | Not built |
| RSK-13 | Flatten threshold reached with stale position truth | No blind flatten; entry lock + reconcile/alert policy | fault | Not built |

## E. Security, Data, and Operations

| ID | Injection | Required invariant / expected result | Level | Status |
|---|---|---|---|---|
| SEC-01 | Unauthenticated call to each non-health route | 401/404 through FastAPI and ingress | generated E2E | Not built |
| SEC-02 | Viewer/operator calls privileged mutation | Denied by role | table-driven | Not built |
| SEC-03 | Expired/revoked/deleted-user HTTP or WS session | Next request denied; WS closes within SLA | E2E | Not built |
| SEC-04 | Wrong/replayed/altered step-up | Denied; exact credential succeeds once | E2E | Not built |
| SEC-05 | Canary secret in build input | History/context/image/source-map scan fails CI | CI | Not built |
| SEC-06 | LAN request to local profile | Connection fails | network | Not built |
| SEC-07 | Non-VPN/direct-backend request to VPN profile | Connection fails | network | Not built |
| SEC-08 | Webhook ingress requests any other route | 404 | ingress | Not built |
| SEC-09 | Spoofed forwarding headers | Ignored unless peer is trusted proxy | ingress | Not built |
| SEC-10 | WS message at max bytes / max+1 | First accepted; second closes 1009 | E2E | Not built |
| SEC-11 | WS subscriptions N / N+1 | N accepted; N+1 denied; disconnect releases refs | E2E/load | Not built |
| SEC-12 | Startup/API requests LIVE or configured real-money broker access | Code-owned Stage 9A fence rejects mode, broker flag, and known live ports | unit | Passed locally; account identity/human release design pending |
| DAT-01 | Required source age threshold / +epsilon | Boundary accepted; +epsilon blocks entry | deterministic | Not built |
| DAT-02 | Yahoo fallback available, broker quote stale | Display may degrade; broker entry blocked | integration | Not built |
| OBS-01 | Real intent/order/fill/reconcile/risk event | Metric increments exactly once | integration | Not built |
| OBS-02 | Synthetic credentials/account ID in exception | Structured log remains redacted | security | Not built |
| OBS-03 | Half-open broker socket/no valid messages | Trading readiness false within SLA | fault | Not built |
| OBS-04 | DB full/low disk/clock drift/old backup/task death | Readiness false and correct out-of-band alert | fault | Not built |
| OBS-05 | Default app and isolated monitoring profile | Default has no `/metrics`; opt-in route has no symbol labels | unit | Passed locally; network proof pending |
| BAK-01 | Scheduled Docker-volume backup | Meets RPO; hash/integrity/schema/encryption/off-host pass | drill | Not built |
| BAK-02 | Latest and oldest-supported restore | Meets RTO; starts disarmed; no intent replay | drill | Not built |
| DRL-01 | Pause entries | Working protection preserved | operator drill | Not built |
| DRL-02 | Cancel working entries | Only owned risk-increasing entries cancelled | operator drill | Not built |
| DRL-03 | Exit-only | New risk blocked; reduce/protect works | operator drill | Not built |
| DRL-04 | Flatten managed exposure | Uses broker truth; ends flat without reversal | paper drill | Not built |
| DRL-05 | Application unavailable | Out-of-band broker control succeeds within SLA | operator drill | Not built |

## Statistical Evidence (After Deterministic Matrix)

Soak duration and sample size are not hard-coded here. The strategy/risk owner
must pre-register them from expected signal frequency, desired confidence, and
minimum detectable failure/effect. Evidence separates:

- operational reliability and unknown/protection/reconciliation incident rate;
- execution quality/slippage/partial fills;
- baseline strategy results; and
- incremental AI decision contribution.

Any material code/config/model/prompt/dependency/broker/account/session change
invalidates the affected evidence and restarts its soak.

