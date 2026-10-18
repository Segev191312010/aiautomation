# Weekend layered-install rehearsal (S8): layers 1, 2 and 3

Copies only, under sandbox-exec (IBKR ports 7496/7497/4000–4002 and live paths blocked, fake HOME).
Account id masked as DU1234567, owner chat id as `<masked>`. Rehearsal agent's reports, as returned; Part B of layer 3
(deploy check and ff on the final ops-p1b tip) is recorded in the ledger.

Finding found after the rehearsal (not visible in a clone): the live checkout had an untracked `backend/Osintgram/`
(a public tool's clone, 10-16 22:56), outside `deploy check`'s benign list; the owner runbook's step L0 moves it out
before layer 1 (pre-mortem I7).

---

# Part 1: layers 1 and 2

## Layers 1 (v7b) and 2 (P1-A) rehearsal: PASS. Clone left at 2608230 for layer 3.

Every block, brake test and undo passed on the copies. The live DB never changed, and no IBKR port, Keychain, launchctl or network was touched.

---

## Weekend layered-install rehearsal: layers 1 (v7b) and 2 (P1-A): PASS
Run Sat 10-17 20:11 → Sun 10-18 08:08 Israel (the Mac slept 20:22–08:05, see F-1). Copies only.

- Live (rehearsed from): 664c93be7a48a4d1fb17f85667f0bed0f4f62e40.
- Layer 1: 5d0eb1ca947a45bfa1a30ab836850df70a1bf12e. Layer 2: 2608230632365c555926528ffc7f93c9d607f246.
- Bridge pin for both layers: 7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4.
- Clone at the end: 2608230, clean. The fake home is kept for layer 3.

### Result table
| Step | Layer 1 | Layer 2 |
|---|---|---|
| Blocks 1–5 | PASS (B2 `664c93b`) | PASS (B2 `5d0eb1c`; deploy check `8 ok, 1 warn, 0 refused`, exit 0) |
| Block 6 / 9 stop/start | printed no-ops | printed no-ops |
| Block 7 ff + sha | PASS | PASS |
| Block 8 dry run | PASS (no `!!!`, 10 lines once each) | PASS (same, plus `restart cap`) |
| Block 9 HEAD | 5d0eb1ca94… | 2608230632… |
| B1 paper stop/resume --identity | PASS | PASS |
| B2 /halt + /confirm | n/a (P1-A adds it) | PASS |
| B3 /closeall Monday → ledger → pinned bridge | PASS | PASS |
| B3 /closeall Sunday | PASS | PASS |
| B4 telegram always-on | PASS | PASS |
| B5 /status, /fences | PASS (/fences unknown at v7b, expected) | PASS |
| Undo | L2 → 5d0eb1c: dry run byte-identical to L1's | L1 → 664c93b: 9790eb3c1e81, MOMO only |
| Redo | both dry runs byte-identical to the first pass | |

### Safety record
- **Sandbox** `$R/sandbox.sb` is sandbox-gates.sb with the write allow-list replaced by `(allow file-write* (subpath "/Users/salomon/autotrader-work/rehearsal-weekend"))`.
  - Every deny kept.
  - Added: deny writes to `~/.local` and `~/Library`; deny all outbound IP, then allow loopback, then deny loopback 7496/7497/4000–4002; deny exec of /bin/launchctl, /usr/bin/security and /usr/bin/sntp.
  - Verified first on test ports 59997/59998 and 192.0.2.1. No IBKR port was ever probed.
  - Every python, git and zsh step ran under it with HOME=$R/fakehome and PYTHONDONTWRITEBYTECODE=1.
- **Bot fakes**: Gateway probe, pmset and the client-21 TWS actor (reports DU1234567) were injected. The Keychain reader raised. Telegram was an httpx.MockTransport.
- **Live DB**: there was no -wal, so the copy used `?mode=ro&immutable=1`.

| Live DB check | Time (Israel) | Size (bytes) | mtime | -wal/-shm |
|---|---|---|---|---|
| Before the copy | 10-17 20:13:12 | 200278016 | 2026-10-17T16:49:21 | none |
| After the copy | 10-17 20:13:13 | 200278016 | 2026-10-17T16:49:21 | none |
| After everything | 10-18 08:07:40 | 200278016 | 2026-10-17T16:49:21 | none |

- **Copy state**:
  - The copy's `integrity_check` was ok.
  - The main copy's manual-order rows match the pristine copy: 3 historic rows from 10-13.
  - It gained two resumed fence rows (paper:ORB_V2, paper:GAP_GO_V2) and 15 rehearsal Telegram update rows (ids ≥ 990000000).
  - The Monday B3 writes (heartbeat, CLOSE_ALL row) went only to scratch copies, now deleted.
- **Live repo**: HEAD unchanged at 664c93b. Git there was used only for clone, rev-parse, show and log. The clone's origin was removed.

### Layer 1: verbatim output (clone paths shown as the live paths)
- **Block 2**: `664c93b`. `status --short` was empty in the clone; live shows ` M .claude/settings.local.json`, which deploy check treats as benign.
- **Block 3**: one `… | v7b.2 7872c86 verdict=GO addendum: tests/docs only since 7f7178d, bridge sha 7d313fd0 unchanged | …` line and one `… | GATES Docker v7b.2 PASS 7872c86 (…) | …` line. Then 4 files: `docs/reviews/2026-10-15-ledger.md`, `docs/reviews/2026-10-16-v7b-rehearsal.md`, `docs/reviews/2026-10-16-v7b2-review.md`, `docs/reviews/2026-10-17-v7b-premortem.md`.
- **Block 4**:
  ```
  ff-ok
   33 files changed, 2478 insertions(+), 100 deletions(-)
  database  /Users/salomon/.local/share/autotrader-v1/autotrader.db
  state     schema v16 is current
  migrate   allowed now (outside every supervised trading day)
  PAPER kill switch: INACTIVE
  ```
  sntp was a no-op.
- **Block 7**: `Updating 664c93b..5d0eb1c`, `Fast-forward`, 33 file lines, ` 33 files changed, 2478 insertions(+), 100 deletions(-)`, 9 `create mode` lines, then `7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4  /Users/salomon/aiautomation/backend/scripts_local/paper_bridge.py`.
- **Block 8**: exit 0, no `!!!`, each of these lines exactly once:
  ```
  bridge       reviewed (sha256 7d313fd075d2)
  bridge       INFO bridge sha256=7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4
  bridge       INFO sendable identities = GAP_GO_V2, MOMO_HOD_V2, ORB_V2, VWAP_PB_V2
  bridge       INFO caps GAP_GO_V2 positions=1 entries/day=1 last_entry=close-30m
  bridge       INFO caps MOMO_HOD_V2 positions=3 entries/day=none last_entry=close-60m
  bridge       INFO caps ORB_V2 positions=1 entries/day=1 last_entry=close-30m
  bridge       INFO caps VWAP_PB_V2 positions=1 entries/day=2 last_entry=close-30m
  bridge       INFO fenced identities = none
  registry     sendable identities, size table and strategy registry agree
  database     schema v16 current (/Users/salomon/.local/share/autotrader-v1/autotrader.db)
  ```
  Also `telegram  always-on    never         15-300s    backend/autotraderctl.py telegram run` and `00:00:00  SESSION_WAIT date=2026-10-19 until=2026-10-19T11:55:00+00:00`.
- **Block 9/10**: HEAD `5d0eb1ca947a45bfa1a30ab836850df70a1bf12e`. Deploy line: `… DEPLOY v7b 5d0eb1c PREV_SHA=664c93be7a48a4d1fb17f85667f0bed0f4f62e40 pin=7d313fd075d2 result=PASS`.

### Layer 2
**deploy check** (from the candidate clone, in a subshell):
```
( cd "$WT/backend" && "$WT/backend/.venv/bin/python" "$WT/backend/autotraderctl.py" deploy check --candidate ops-p1 --repo "$LIVE" --expect-parent 5d0eb1ca947a45bfa1a30ab836850df70a1bf12e --scope T-1,T-2,T-5,T-7,L-2,L-3,L-4,GW-HMDS,N14 --now 2026-10-18T02:00:00+03:00 --skip-clock )
```
```
deploy check: candidate ops-p1 = 260823063236 in <LIVE> at 2026-10-17 19:00 EDT scope=T-1,T-2,T-5,T-7,L-2,L-3,L-4,GW-HMDS,N14
OK verdict
  docs/reviews/2026-10-15-ledger.md:68: 2026-10-17T20:08:49+03:00 | P1-A.5 0a4893e47c8c verdict=GO scope=T-1,T-2,T-5,T-7,L-2,L-3,L-4,GW-HMDS,N14,N14b bridge_sha=7d313fd0…(64 hex) | reviewer N14c: … | docs/reviews/2026-10-17-p1a5-review.md
  head 260823063236 = verdict 0a4893e47c8c + 1 commit(s) touching only docs/reviews/:
  + 2608230 docs(reviews): P1-A.5 0a4893e re-read N14c (GO) and Docker gates pass; P1-A frozen for Sunday
OK tree_clean
OK parent
  HEAD 5d0eb1ca947a -> ops-p1 260823063236: 21 commit(s) ahead
OK files
  25 path(s) HEAD..ops-p1: LEARNED.md 1, backend/ 17, docs/ 7
OK schema
  <HOME>/.local/share/autotrader-v1/autotrader.db: schema v16 is current
OK fence
  PAPER kill switch: INACTIVE
WARN clock: skipped (--skip-clock): offset not measured
OK window
  now 2026-10-17 19:00 EDT: inside 18:30 -> 07:20 ET
OK pin
  bridge sha256 7d313fd075d2 == REVIEWED_BRIDGE_SHA256
deploy check: 8 ok, 1 warn, 0 refused
```
Exit 0, and the outer `pwd` did not change. Live, without `--now`/`--skip-clock`, the last line should be `deploy check: 9 ok, 0 warn, 0 refused`.

**Install blocks:**
- **Block 2**: `5d0eb1c`.
- **Block 3**: one `P1-A.5 0a4893e47c8c verdict=GO …` line and one `GATES Docker P1-A PASS 0a4893e47c8c …` line. `diff --name-only 0a4893e47c8c..ops-p1` gives exactly `docs/reviews/2026-10-15-ledger.md` and `docs/reviews/2026-10-17-p1a5-review.md`.
- **Block 4**: `ff-ok`; ` 25 files changed, 9594 insertions(+), 143 deletions(-)`; the 3 db status lines; `PAPER kill switch: INACTIVE`. The rehearsal also printed `identity ORB_V2: RESUMED`, left by B1 at layer 1.
- **Block 7**: `Updating 5d0eb1c..2608230`, `Fast-forward`, 25 file lines, ` 25 files changed, 9594 insertions(+), 143 deletions(-)`, 9 `create mode` lines, then `7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4  …`.
- **Block 8**: exit 0, no `!!!`. The same ten lines plus exactly one new line, which is the only diff from layer 1: `restart cap  6 exits / 30 min; bridge and always-on children page only`.
- **Block 9/10**: HEAD `2608230632365c555926528ffc7f93c9d607f246`. Deploy line: `… DEPLOY P1-A 2608230 PREV_SHA=5d0eb1ca947a45bfa1a30ab836850df70a1bf12e pin=7d313fd075d2 result=PASS`.

### Brake tests (production code, DB copy under the fake HOME)
**B1** (identical at L1 and L2):
- `paper stop --identity ORB_V2 --reason rehearsal` → `PAPER identity fence ORB_V2: STOPPED`
- `paper status` → `PAPER kill switch: INACTIVE` / `identity ORB_V2: STOPPED (rehearsal)`
- dry run → `bridge       CRITICAL fenced identities = ORB_V2` (exit 0, no `!!!`)
- `paper resume --identity ORB_V2` → `PAPER identity fence ORB_V2: RESUMED`
- `paper status` → `PAPER kill switch: INACTIVE` / `identity ORB_V2: RESUMED`
- dry run → `bridge       INFO fenced identities = none`

**B2** (L2). Driven through the real `run_telegram_assistant` → `_serve` wiring over a mock Telegram transport:
- `/halt GAP_GO_V2` → `halt GAP_GO_V2: confirm with /confirm 7af88b within 60 s (stop only; resume is CLI-only)`
- wrong code 1 → `❌ Wrong code for /halt GAP_GO_V2 — nothing written. Send /confirm with the code from the /halt reply (2 tries left).`
- wrong code 2 → same, `(1 try left).`
- wrong code 3 → `❌ Wrong code for /halt GAP_GO_V2 — nothing written. 3 wrong codes: this /halt is dropped. Send /halt GAP_GO_V2 again for a new code.`
- the right code now → `❌ No /halt is waiting for confirmation — send /halt <IDENTITY> first. Nothing written.`; dry run → `fenced identities = none`
- new `/halt GAP_GO_V2` + right `/confirm` → `identity fence GAP_GO_V2: STOPPED (new entries blocked; exits, flatten and manual orders unaffected)`
- `paper status` → `identity GAP_GO_V2: STOPPED (telegram /halt by owner)`; dry run → `bridge       CRITICAL fenced identities = GAP_GO_V2`
- `paper resume --identity GAP_GO_V2` → `PAPER identity fence GAP_GO_V2: RESUMED`; dry run → `INFO fenced identities = none`

**B3 Monday** (identical at L1 and L2). Fake clock Mon 10-19 10:00 ET, fake client-21 session reporting DU1234567, `write_bridge_heartbeat('loop', 09:59:57 ET)` on a scratch copy:
- `/closeall` → `🟢 Queued CLOSE ALL · #76c290 · /cancel 76c290` (L2: `#c086f7`)
- row `action=CLOSE_ALL status=queued chat_is_owner=True`
- Pinned bridge: sha checked, BRIDGE_VERSION=v7b, a real `Bridge` over a fake ibapi app, the bridge's own `manual_desk().tick()`:
  - `claim_next_order(2026-10-19T10:00:05-04:00) -> … action=CLOSE_ALL status=sending`
  - `revalidate_claimed(...) -> ValidatedOrder kind=close_all code=None accounts_all_paper=True heartbeat_age_s=0.0 kill_switch_active=False`
  - `close_all(...)` reached, intercepted before any placeOrder.

**B3 Sunday** (Sun 03:30 ET, main copy, data client never connected), exact reply:
`❌ Not sent — Market closed: manual orders run 09:30-15:54 ET on trading days. Nothing sent. (market_closed)`

**B4**: both dry runs list `telegram  always-on    never         15-300s    backend/autotraderctl.py telegram run`.

**B5 at L1**, `/status`:
- `🤖 Status · Sun 18 Oct 03:30:00 ET`
- `Market: CLOSED · opens Mon 19 Oct 09:30 ET`
- `Supervisor: running (pid 55714) since Thu 18:46:32 ET (2 d ago)`
- `Session: wait date=2026-10-19 until=2026-10-19T11:55:00+00:00`
- `✅ telegram: running since 18:46 ET (pid 55718)`, then one ⏹ line per child
- `Bridge: last heartbeat 35 h 35 min ago — manual orders are refused until it is back`
- `Regime: NEUTRAL as of Fri 15:58:24 ET (35 h 31 min ago)`
- `Kill switch: off`

`/fences` at L1 → `Unknown command /fences — send /help for the list.`

**B5 at L2**: `/status` adds these lines:
- `Gateway 7497: CLOSED` (fake probe; live shows the real port)
- `Schema: schema v16 is current`
- `LaunchAgent: unknown (no plist installed; launchctl not queried)`. Live should say `plist current`.
- `Recorder/replay: recorder ok Fri 16 Oct 16:54 ET, bars through none · replay ok Fri 16 Oct 17:38 ET, report none`. Research files weren't copied.
- `Power: AC`
- `Restarts today: none (no child started today)`
- While a fence is on: `Identity fences ON: GAP_GO_V2 (autotraderctl paper resume --identity)`.

`/fences` at L2:
```
🚧 Fences · Sun 18 Oct 03:30:00 ET
paper (global): clear (no row)
paper:ORB_V2: clear · operator resume via autotraderctl · Sat 17 Oct 13:22 ET
Stop one identity: /halt <IDENTITY> · resume: autotraderctl paper resume --identity (Mac only)
```
A fenced row reads `paper:GAP_GO_V2: STOPPED · telegram /halt by owner · <time>`.

Simulated U2.5 `/status` after Sunday's two restarts: `Supervisor: running (pid …) since Sun 03:11:00 ET (19 min ago)` … `Restarts today: telegram=1 (starts after each child's first today)`. That count is expected, not a fault.

### Undo
- **L2**: `git -C "$LIVE" reset --keep 5d0eb1ca947a45bfa1a30ab836850df70a1bf12e` exits 0. HEAD 5d0eb1c…, tree clean, shasum still 7d313fd0…. The dry run is byte-identical to layer 1's: no `restart cap` line, pin still `7d313fd075d2`.
- **L1**: `reset --keep 664c93be7a48a4d1fb17f85667f0bed0f4f62e40`. shasum `9790eb3c1e81a3c74ceaab5245570ea07ee9cbfd407a0fa167d51015456af01f`. Dry run: `bridge       reviewed (sha256 9790eb3c1e81)`, `sendable identities = MOMO_HOD_V2`, `caps MOMO_HOD_V2 positions=3 entries/day=none last_entry=close-60m`, `fenced identities = none`.
- **Redo**: ff → identical dry runs; deploy check `8 ok, 1 warn, 0 refused`. The clone ends at 2608230, clean.

### Findings
- **F-1 (needs the owner before U1).** `pmset -g log` shows `2026-10-17 20:22:41 Sleep … 'Low Power Sleep' … Using Batt (Charge:1%)` and `2026-10-18 08:05:47 Wake from Hibernate … due to acattach … Using AC`.
  - The live supervisor and bot were frozen for about 11.7 h.
  - The plist's `caffeinate -i -s` only prevents sleep on AC.
  - At 08:08 the Mac was on AC at 6 % and charging.
  - Before U1, `pmset -g batt` must show AC Power with the charge rising.
- **F-2. The pin cannot tell L1 from L2**: both are `7d313fd075d2`.
  - The current v7b undo sentence ("if it still shows 7d313fd075d2 the undo did not happen") is right only for undoing L1.
  - For L2 the markers are `rev-parse HEAD` and the `restart cap` line.
- **F-3. A fenced identity does not print `!!!`**: the dry run exits 0 and prints `CRITICAL fenced identities = <ID>`. The runbook must require `INFO fenced identities = none` explicitly.
- **F-4. HIGH-1 is confirmed.** `LaunchdManager.payload()` takes `WorkingDirectory` from the checkout of the code that runs it. From the clone it renders `…/live-clone/backend`; from the candidate, `…/cand-p1a/backend`. So CTL must be built from `$LIVE`.
  - The live plist today (read with plutil, read-only) has `WorkingDirectory` `/Users/salomon/aiautomation/backend` and the args `/usr/bin/caffeinate -i -s /Users/salomon/aiautomation/backend/.venv/bin/python /Users/salomon/aiautomation/backend/autotraderctl.py run`.
- **F-5. Fence rows stay after a resume.** Later `paper status` adds `identity <ID>: RESUMED`, and `/fences` lists the row as `clear`. Layer 3's Block 4 must expect `identity ORB_V2: RESUMED` after U2.5.
- **F-6.** `/confirm` must come within 60 s of `/halt`. After three wrong codes, even the right code gets "No /halt is waiting".
- **F-7.** The real WT `/Users/salomon/aiautomation/.claude/worktrees/ops-p1` is at 2608230632365c555926528ffc7f93c9d607f246, and its `backend/.venv/bin/python` points to the live venv, so deploy check from it works as rehearsed.
- **F-8.** The live checkout has a gitignored `backend/.env` that the clone lacks. The trading code reads none of its keys and it has no DB_PATH. No effect.

### Recommended runbook text (zsh: CTL arrays, no `#` lines, absolute paths, literal shas, one block at a time)

**L1-B1.** Expect `/Users/salomon/aiautomation/backend`.
```
LIVE=/Users/salomon/aiautomation
cd "$LIVE/backend"
CTL=("$LIVE/backend/.venv/bin/python" "$LIVE/backend/autotraderctl.py")
L=~/Library/Logs/AutotraderV1
pwd
```

**L1-B2.** `git -C "$LIVE" rev-parse --short HEAD` and `git -C "$LIVE" status --short`. Expect `664c93b`, then only ` M .claude/settings.local.json`.

**L1-B3 and L1-B4.** The current lines with `git -C "$LIVE"`. Expect the B3/B4 output shown above.

**L1-B5 and L1-B6.** Unchanged, with `git -C "$LIVE"`.

**L1-B7.** Expect `Updating 664c93b..5d0eb1c`, `Fast-forward`, 33 names, the stat line and 9 `create mode` lines, then `7d313fd0…  /Users/salomon/aiautomation/backend/scripts_local/paper_bridge.py`, then `5d0eb1ca947a45bfa1a30ab836850df70a1bf12e`.
```
git -C "$LIVE" merge --ff-only bridge-v7b
shasum -a 256 "$LIVE/backend/scripts_local/paper_bridge.py"
git -C "$LIVE" rev-parse HEAD
```

**L1-B8.** `$CTL run --dry-run --date 2026-10-19`. Expect the ten lines plus the telegram always-on line, no `!!!`, and explicitly `INFO fenced identities = none`.

**L1-B9.** Expect `SUPERVISOR_START`, `CHILD_START telegram`, `SESSION_WAIT date=2026-10-19`, then `/Users/salomon/aiautomation/backend`, then `5d0eb1ca947a45bfa1a30ab836850df70a1bf12e`. Anything else: STOP and undo.
```
$CTL shadow agent enable
sleep 20
tail -n 3 $L/supervisor.log
plutil -extract WorkingDirectory raw ~/Library/LaunchAgents/com.maccabi.autotrader-v1.shadow.plist
git -C "$LIVE" rev-parse HEAD
```

**L1-B10.** Unchanged, with `git -C "$LIVE"`.

**U1.5.** `/status` only. `/fences` does not exist at v7b.

**Undo L1** (only when flat). Expect `664c93be…`, `reviewed (sha256 9790eb3c1e81)` and `sendable identities = MOMO_HOD_V2`.
```
$CTL shadow agent disable
git -C "$LIVE" reset --keep 664c93be7a48a4d1fb17f85667f0bed0f4f62e40
git -C "$LIVE" rev-parse HEAD
$CTL run --dry-run --date 2026-10-19
$CTL shadow agent enable
```

**L2-B1** (same window). Expect `2608230632365c555926528ffc7f93c9d607f246`, `5d0eb1c`, ` M .claude/settings.local.json`.
```
WT=/Users/salomon/aiautomation/.claude/worktrees/ops-p1
git -C "$WT" rev-parse HEAD
git -C "$LIVE" rev-parse --short HEAD
git -C "$LIVE" status --short
```

**L2-B2.** Expect one line, one line, then exactly the two docs/reviews files.
```
git -C "$LIVE" show ops-p1:docs/reviews/2026-10-15-ledger.md | grep -E 'P1-A.5 0a4893e47c8c verdict=GO'
git -C "$LIVE" show ops-p1:docs/reviews/2026-10-15-ledger.md | grep -E 'GATES Docker P1-A PASS 0a4893e47c8c'
git -C "$LIVE" diff --name-only 0a4893e47c8c..ops-p1
```

**L2-B3.** Expect `deploy check: 9 ok, 0 warn, 0 refused`, then `/Users/salomon/aiautomation/backend`.
```
( cd "$WT/backend" && "$WT/backend/.venv/bin/python" "$WT/backend/autotraderctl.py" deploy check --candidate ops-p1 --repo "$LIVE" --expect-parent 5d0eb1ca947a45bfa1a30ab836850df70a1bf12e --scope T-1,T-2,T-5,T-7,L-2,L-3,L-4,GW-HMDS,N14 )
pwd
```

**L2-B4.** Expect `ff-ok`, ` 25 files changed, 9594 insertions(+), 143 deletions(-)`, the 3 db status lines, `PAPER kill switch: INACTIVE`, plus any `identity <ID>: RESUMED` lines.
```
git -C "$LIVE" merge-base --is-ancestor HEAD ops-p1 && echo ff-ok
git -C "$LIVE" diff --stat HEAD..ops-p1 | tail -1
$CTL db status
$CTL paper status
```

**L2-B5.** `PREV_SHA=$(git -C "$LIVE" rev-parse HEAD)` and `echo "$(date -Iseconds) P1-A PREV_SHA=$PREV_SHA" >> $L/deploys.log`.

**L2-B6.** The stop block, unchanged.

**L2-B7.** `merge --ff-only ops-p1`, shasum and rev-parse, as in L1-B7. Expect `Updating 5d0eb1c..2608230`, `Fast-forward`, 25 names, the stat line and 9 `create mode` lines, then `7d313fd0…`, then `2608230632365c555926528ffc7f93c9d607f246`.

**L2-B8.** Expect the L1-B8 lines plus exactly `restart cap  6 exits / 30 min; bridge and always-on children page only`.

**L2-B9.** As L1-B9. Expect HEAD 2608230… and the plist dir `/Users/salomon/aiautomation/backend`.

**L2-B10.** `echo "$(date -Iseconds) DEPLOY P1-A $(git -C "$LIVE" rev-parse --short HEAD) PREV_SHA=$PREV_SHA pin=7d313fd075d2 result=PASS" >> $L/deploys.log`

**U2.5 expected:**
- `/status` lines `Gateway 7497: OPEN|CLOSED`, `Schema: schema v16 is current`, `LaunchAgent: unknown (plist current; launchctl not queried)`, `Power: AC`, `Restarts today: telegram=1 (starts after each child's first today)`. If it says `plist differs from this code`: STOP.
- `/halt ORB_V2` → `halt ORB_V2: confirm with /confirm <6 hex> within 60 s (stop only; resume is CLI-only)`; `/confirm <code>` within 60 s → `identity fence ORB_V2: STOPPED (new entries blocked; exits, flatten and manual orders unaffected)`.
- `$CTL paper resume --identity ORB_V2` → `PAPER identity fence ORB_V2: RESUMED`; `$CTL paper status` → `PAPER kill switch: INACTIVE` / `identity ORB_V2: RESUMED`.
- `/closeall` → `❌ Not sent — Market closed: manual orders run 09:30-15:54 ET on trading days. Nothing sent. (market_closed)`.

**Undo L2** (only when flat). Expect `5d0eb1ca947a45bfa1a30ab836850df70a1bf12e`. The dry run still shows `reviewed (sha256 7d313fd075d2)`, which is correct here, and must no longer show the `restart cap` line. Then the L1-B9 checks. To go further back, run Undo L1 after it.
```
$CTL shadow agent disable
git -C "$LIVE" reset --keep 5d0eb1ca947a45bfa1a30ab836850df70a1bf12e
git -C "$LIVE" rev-parse HEAD
$CTL run --dry-run --date 2026-10-19
$CTL shadow agent enable
```

---

### Short summary
- Every block for v7b and P1-A matched; the verbatim lines are above.
- deploy check gave `8 ok, 1 warn, 0 refused` (clock skipped).
- All five brake tests passed through the production code. `/closeall` reached the pinned bridge's `close_all`, and on Sunday it replies "Market closed … Nothing sent".
- Undo works one layer at a time.
- The live DB stayed at 200278016 bytes, mtime 2026-10-17T16:49:21, no -wal.
- **Action before U1**: the Mac slept overnight on battery at 1 % (F-1). It must be on AC with the charge rising.
- **Runbook changes**: the L2 undo text must not reuse the v7b "pin still 7d313fd075d2 means the undo failed" sentence (F-2). Every dry run needs an explicit `fenced identities = none` check (F-3).

### Files (in /Users/salomon/autotrader-work/rehearsal-weekend)
- `out/`: verbatim outputs (layer1.txt, layer2.txt, L1-dryrun.txt, L2-dryrun.txt, L2-deploycheck.txt, L*-B*.txt, undo*.txt, redo*.txt, livedb-*.txt).
- `scripts/`: the zsh blocks and `rehearse_bot.py`, the bot/bridge harness.
- `sandbox.sb`, `sb.sh`.
- `live-clone` is at 2608230; `cand-p1a` is the candidate clone.
- `fakehome` keeps the main DB copy, the pristine copy, logs and config. These hold the real account id; they are kept for layer 3 and deleted after it. The scratch copies are already deleted.

I'm ready for layer 3 when you send the frozen P1-B head.

---

# Part 2: layer 3 (P1-B), Part A on 0217e10

## Layer 3 (P1-B) rehearsal, Part A on 0217e10: PASS. Ready for Part B when you send the final tip.

All of Part A passed:
- The only `deploy check` refusal is the missing P1-B verdict line (expected).
- The fast-forward is clean and the pin is unchanged.
- The dry run has no `!!!`.
- A real supervisor start rotated the logs (`LOG_ROTATED`, no `LOG_ROTATION_FAILED`) and stopped cleanly, leaving no rehearsal process.
- B1–B5 and the Sentinel log-only check passed.
- Undoing to 2608230 gives layer 2's dry run; redoing gives step 4's. In both comparisons only the dry-run process's own pid differs, which is inherent.

The live DB is unchanged: 200278016 bytes, mtime 2026-10-17T16:49:21, no -wal, checked at 08:26 Israel. The live repo HEAD is still 664c93b. The clone is at 0217e105d5663f76b984cd1eb40df065d0a92aeb, clean. Account ids are masked as DU1234567 and the chat id as `<masked>`.

**Safety additions for this layer:**
- **Getting 0217e10:** fetched into the clone from the source repo (read-only) under the sandbox; the candidate clone `cand-p1b` was made from the rehearsal clone.
- **Real start:**
  - It ran under an extra sandbox rule: no signals to processes outside the sandbox (tested first on my own throwaway processes).
  - The Gateway probe was replaced by a recorder. It was called 0 times.
  - The heartbeat URL source was injected as `None`, so the supervisor made no Keychain call at all.
  - The scratch home had no `telegram.json`, so the telegram child refused before reaching the Keychain.
- **P1-B dry runs:** the CLI dry run always runs a presence-only lookup (`security find-generic-password` without `-w`). My sandbox refused that exec every time, so nothing was read.
- **Bot harness:** the new `AssistantEnvironment.gateway_probe` and `tws_factory` fields were injected. The real `gateway_port_open` functions were trapped and never reached.

### 2. deploy check on 0217e10 (verbatim)
```
( cd "$WT/backend" && "$WT/backend/.venv/bin/python" "$WT/backend/autotraderctl.py" deploy check --candidate ops-p1b --repo "$CLONE" --expect-parent 2608230632365c555926528ffc7f93c9d607f246 --scope T-3,T-4,T-4b,T-6,T-8,T-9,W6.4 --now 2026-10-18T02:00:00+03:00 --skip-clock )
```
```
deploy check: candidate ops-p1b = 0217e105d566 in <CLONE> at 2026-10-17 19:00 EDT scope=T-3,T-4,T-4b,T-6,T-8,T-9,W6.4
REFUSE verdict: no `<sha> verdict=GO scope=T-3,T-4,T-4b,T-6,T-8,T-9,W6.4` line (ledger grammar `<time> | [<label>] <sha> verdict=GO ... | ...`) in 1 ledger(s): docs/reviews/2026-10-15-ledger.md
OK tree_clean
OK parent
  HEAD 260823063236 -> ops-p1b 0217e105d566: 22 commit(s) ahead
OK files
  52 path(s) HEAD..ops-p1b: LEARNED.md 1, backend/ 45, docs/ 6
OK schema
  <HOME>/.local/share/autotrader-v1/autotrader.db: schema v16 is current
OK fence
  PAPER kill switch: INACTIVE
WARN clock: skipped (--skip-clock): offset not measured
OK window
  now 2026-10-17 19:00 EDT: inside 18:30 -> 07:20 ET
OK pin
  bridge sha256 7d313fd075d2 == REVIEWED_BRIDGE_SHA256
deploy check: 7 ok, 1 warn, 1 refused
```
Exit 78, and the outer `pwd` did not change. The only refusal is the missing verdict line.

### 3. Fast-forward (verbatim)
- Block 4 before the merge: `ff-ok`; ` 52 files changed, 18573 insertions(+), 384 deletions(-)`; `state     schema v16 is current`; `migrate   allowed now (outside every supervised trading day)`; `PAPER kill switch: INACTIVE`, then `identity GAP_GO_V2: RESUMED` and `identity ORB_V2: RESUMED` (rows left by the earlier layers' brakes).
- Merge output: `Updating 2608230..0217e10`, `Fast-forward`, 52 file lines, ` 52 files changed, 18573 insertions(+), 384 deletions(-)`, 24 `create mode` lines.
- Bridge shasum: `7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4`.
- HEAD: `0217e105d5663f76b984cd1eb40df065d0a92aeb`.

The final tip adds docs/reviews lines, so on Sunday the file count and insertions will be slightly higher and the `Updating` line ends in the tip's short sha.

### 4. Dry run at 0217e10 (full, verbatim; clone paths read as live paths)
Exit 0, no `!!!` line.
```
DRY RUN: nothing is launched and no log is written.
session      2026-10-19 (Mon), XNYS 09:30-16:00 ET
clock start  2026-10-19 00:00 ET (simulated)
python       /Users/salomon/aiautomation/backend/.venv/bin/python
logs         /Users/salomon/Library/Logs/AutotraderV1/<child>.log; CHILD_* lines in /Users/salomon/Library/Logs/AutotraderV1/supervisor.log

child     window ET    final exit 0  backoff    command
screener  07:55-16:00  from 16:00    15-300s    backend/autotraderctl.py screener run --interval 60
notifier  07:55-16:15  from 16:05    15-300s    backend/autotraderctl.py notify run --date 2026-10-19
bridge    09:25-16:00  from 15:55    15-300s    backend/scripts_local/paper_bridge.py --date 2026-10-19
orb-v2    09:28-15:31  from 15:30    95-300s    backend/autotraderctl.py shadow run --strategy orb-v2 --universe scanner --date 2026-10-19
momo      09:55-15:01  from 15:00    95-300s    backend/autotraderctl.py shadow run --strategy momo --universe watchlist --date 2026-10-19
gap-v2    09:28-15:31  from 15:30    95-300s    backend/autotraderctl.py shadow run --strategy gap-v2 --universe all-gappers --date 2026-10-19
vwap-v2   09:28-15:31  from 15:30    95-300s    backend/autotraderctl.py shadow run --strategy vwap-v2 --universe watchlist --date 2026-10-19
manager   09:15-14:40  from 14:30    15-300s    backend/autotraderctl.py manager run --date 2026-10-19
momo-ap   09:55-15:01  from 15:00    95-300s    backend/autotraderctl.py shadow run --strategy momo-ap --universe watchlist --date 2026-10-19
recorder  16:20-17:30  from 16:20    15-300s    backend/autotraderctl.py research record
replay    17:35-18:30  from 17:35    15-300s    backend/autotraderctl.py research replay
bridge exit 78 is a refusal: logged, never restarted
telegram  always-on    never         15-300s    backend/autotraderctl.py telegram run
telegram runs from supervisor start to supervisor stop, between sessions too; restarted after any exit (not shown in the timeline below)
restart cap  6 exits / 30 min; bridge and always-on children page only
heartbeat    unknown (Keychain item com.maccabi.autotrader-v1.heartbeat/heartbeat-url could not be checked: KeychainReadError)
rotation     daily at 18:35 ET between sessions into <name>.log.YYYY-MM-DD, 14 days kept; launchd.*.log and running always-on children's logs copied then truncated

simulated timeline (ET; pids are placeholders):
00:00:00  SUPERVISOR_START pid=<n> mode=once date=2026-10-19
00:00:00  SESSION_WAIT date=2026-10-19 until=2026-10-19T11:55:00+00:00
… (the timeline is identical to layers 1–2: SESSION_START 07:55 … SUPERVISOR_STOP reason=completed 18:30)
bridge       reviewed (sha256 7d313fd075d2)
bridge       INFO bridge sha256=7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4
bridge       INFO sendable identities = GAP_GO_V2, MOMO_HOD_V2, ORB_V2, VWAP_PB_V2
bridge       INFO caps GAP_GO_V2 positions=1 entries/day=1 last_entry=close-30m
bridge       INFO caps MOMO_HOD_V2 positions=3 entries/day=none last_entry=close-60m
bridge       INFO caps ORB_V2 positions=1 entries/day=1 last_entry=close-30m
bridge       INFO caps VWAP_PB_V2 positions=1 entries/day=2 last_entry=close-30m
bridge       INFO fenced identities = none
registry     sendable identities, size table and strategy registry agree
database     schema v16 current (/Users/salomon/.local/share/autotrader-v1/autotrader.db)
```

**The only diff from layer 2 is two added lines**: `heartbeat …` and `rotation …`. There are no Sentinel, overnight or pre-session lines: those run inside the notifier and telegram children, not in the dry run.

**Why the heartbeat line reads `unknown`:** the dry run checks only whether the Keychain item exists, and my sandbox refuses the `security` exec. So it shows `unknown (… could not be checked: KeychainReadError)`, and no heartbeat always-on row appears.

**What the owner will see on Sunday.** I rendered this from the production `dry_run_report` with the presence answer injected.

If the Keychain item exists (S11 done), these lines replace the two above:
```
heartbeat always-on    never         15-300s    -m trading.operations.heartbeat
heartbeat runs from supervisor start to supervisor stop, between sessions too; restarted after any exit (not shown in the timeline below)
heartbeat    Keychain item com.maccabi.autotrader-v1.heartbeat/heartbeat-url present (the child validates the URL)
```
The two `heartbeat always-on` lines sit right after the telegram ones; the `heartbeat    Keychain item …` line sits after `restart cap`.

If the item is missing: `heartbeat    not configured (no Keychain item com.maccabi.autotrader-v1.heartbeat/heartbeat-url)`, and there is no always-on row. That means STOP: S11 was not done.

### 5. Real supervisor start (scratch copy of the logs, ~40 s, then SIGTERM)
Console and log lines, verbatim:
```
2026-10-18T05:22:55+00:00 PREFLIGHT_WARNING heartbeat not configured (no Keychain item com.maccabi.autotrader-v1.heartbeat/heartbeat-url)
2026-10-18T05:22:55+00:00 SUPERVISOR_START pid=62892 mode=daily
2026-10-18T05:22:55+00:00 CHILD_START telegram pid=62898
2026-10-18T05:22:55+00:00 SESSION_WAIT date=2026-10-19 until=2026-10-19T11:55:00+00:00
2026-10-18T05:22:55+00:00 LOG_ROTATED date=2026-10-17 renamed=12 truncated=2 pruned=0 carried=3
2026-10-18T05:22:56+00:00 CHILD_EXIT telegram code=78 restart_in=15
2026-10-18T05:23:11+00:00 CHILD_START telegram pid=62952
2026-10-18T05:23:11+00:00 CHILD_EXIT telegram code=78 restart_in=30
2026-10-18T05:23:35+00:00 SUPERVISOR_STOP reason=SIGTERM
```
`run_supervisor` returned 0, with 0 port probes and no `LOG_ROTATION_FAILED`.

**Why the rotation fired:** the archive for the latest 18:35 ET boundary (Sat 10-17) was missing, so the start made up the rotation at once. The same will happen at Sunday's L3 start.

**What was rotated:**
- Renamed (12): the 11 child logs, plus `supervisor.log` → `*.log.2026-10-17`.
- Copied then truncated (2): `telegram.log` (its child was running) and `launchd.stdout.log`. `launchd.stderr.log` was empty.
- Untouched: `shadow.log` and `deploys.log`.
- The legacy `gap.log`, `orb.log` and `vwap.log` are never rotated; that is cosmetic.

**Heartbeat with no URL:** it was not scheduled (no CHILD_START heartbeat), and the supervisor logged `PREFLIGHT_WARNING heartbeat not configured (no Keychain item …)`.

**Telegram exit 78:** in the scratch home it printed `telegram: refusing to start: no owner chat id is pinned in …/telegram.json`. That was by design, to keep it away from the Keychain. It then restarted with a 15 s, then 30 s backoff.

**Processes:**
- During the run only the rehearsal supervisor pid existed.
- After SIGTERM it exited 0 at once.
- A pgrep for `rehearsal-weekend/.*(trading[.]operations|autotraderctl[.]py|real_start[.]py)` found `none`.
- The live processes 55714, 55715 and 55718 were the same before and after.

**The new `supervisor.log`** starts with the carried copies (SUPERVISOR_START, CHILD_START telegram, SESSION_WAIT), then the `LOG_ROTATED` marker, then the new lines.

**FINDING L3-A (runbook):** the `PREFLIGHT_WARNING heartbeat` line is written before the rotation, so it lands in `supervisor.log.2026-10-17`, not in the new `supervisor.log`. U3's "no PREFLIGHT_WARNING heartbeat" check must grep both files.

**FINDING L3-B (runbook):** after the start, `tail -n 3 $L/supervisor.log` ends at `LOG_ROTATED …`. Use `tail -n 6`. Live with the heartbeat, expect `SUPERVISOR_START`, `CHILD_START telegram`, `CHILD_START heartbeat`, `SESSION_WAIT date=2026-10-19 …`, then `LOG_ROTATED date=2026-10-17 renamed=<n> truncated=<n> pruned=0 carried=4`.

### 6. Brakes through the P1-B code (all PASS)
- **B1** (identical to layers 1–2):
  - `paper stop --identity ORB_V2 --reason rehearsal` → `PAPER identity fence ORB_V2: STOPPED`; the dry run shows `bridge       CRITICAL fenced identities = ORB_V2`, exit 0, no `!!!`.
  - `paper resume --identity ORB_V2` → `PAPER identity fence ORB_V2: RESUMED`; the dry run shows `INFO fenced identities = none`.
  - `paper status`: `identity ORB_V2: RESUMED`.
- **B2** (P1-B bot; the log says `overnight watch started (Gateway port, restart cap, recorder competing login)`):
  - `/halt GAP_GO_V2` → `halt GAP_GO_V2: confirm with /confirm 078a8f within 60 s (stop only; resume is CLI-only)`.
  - Three wrong codes give `(2 tries left)`, `(1 try left)`, then `3 wrong codes: this /halt is dropped. Send /halt GAP_GO_V2 again for a new code.`
  - The right code after that → `❌ No /halt is waiting for confirmation — send /halt <IDENTITY> first. Nothing written.`
  - A new `/halt` + `/confirm b0fc43` → `identity fence GAP_GO_V2: STOPPED (new entries blocked; exits, flatten and manual orders unaffected)`; the log shows `INCIDENT halt identity=GAP_GO_V2 via=telegram`; the dry run shows `bridge       CRITICAL fenced identities = GAP_GO_V2`.
  - `paper resume --identity GAP_GO_V2` → `PAPER identity fence GAP_GO_V2: RESUMED`; the dry run shows `none`.
- **B3 Monday** (clock 10:00 ET, scratch copy):
  - `/closeall` → `🟢 Queued CLOSE ALL · #060fe3 · /cancel 060fe3`; the row is `action=CLOSE_ALL status=queued`.
  - Pinned bridge (sha checked): `claim_next_order(2026-10-19T10:00:05-04:00) -> #060fe3 action=CLOSE_ALL status=sending`.
  - Then `revalidate_claimed(...) -> ValidatedOrder kind=close_all code=None accounts_all_paper=True heartbeat_age_s=0.0 kill_switch_active=False`, and `close_all(...)` was reached and intercepted before any placeOrder.
  - The log said `overnight watch: BrokerWatch owns the Gateway until 16:15 ET`.
- **B3 Sunday:** `❌ Not sent — Market closed: manual orders run 09:30-15:54 ET on trading days. Nothing sent. (market_closed)`.
- **B4:** the telegram always-on row is present. The heartbeat row appears only when the Keychain item is present (variant above).
- **B5** `/status` (Sunday 03:30 ET, main copy): identical to layer 2 except `Gateway 7497: OPEN`, which comes from the injected probe. `/fences` renders the global row plus `paper:GAP_GO_V2` and `paper:ORB_V2` as `clear · operator resume via autotraderctl · …`.
- **`/status` read from the rotated scratch log:**
  - It shows `Supervisor: STOPPED at Sun 01:23:35 ET (85 s ago) (SIGTERM)` and `⚠️ telegram: exited with code 78, restarting in 30 s (01:23 ET)`.
  - It also shows `Recorder/replay: recorder no run in the log tail, bars through none · replay no run in the log tail, report none` and `Restarts today: telegram=1 (starts after each child's first today)`.
  - So after Sunday's rotation, "no run in the log tail" is expected: those lines are in the archive.

### 7. Sentinel log-only (main DB copy, real SQLitePaperKillStore)
- `SENTINEL_FENCES_LIVE = False; LOSING_STREAK = 4`.
- Fence rows before: 2 (`paper:GAP_GO_V2`, `paper:ORB_V2`, both stopped=0).
- I wrote a synthetic bridge state for Mon 10-19: ORB_V2 closed with realized -130.00, where 1 R = $100.
- `Sentinel.check(10:00:30 ET)` gave `[('sentinel_would_fence', '🔎 auto-fence (log-only): would stop ORB_V2 — daily loss 1.30 R > 1.25 R (realized -$130.00)')]`, with the log line `sentinel would fence ORB_V2: daily loss 1.30 R > 1.25 R (realized -$130.00)`. The check at 10:01 gave `[]` (once per identity per day; memory key `sentinel_would_fence:ORB_V2`).
- The nightly writer, `fence_identity(GAP_GO_V2, 'sentinel: replay mismatch 3 decisions')`, returned `would_fence`.
- Fence rows after: the same 2 rows, byte for byte (same reasons and updated_at). No row was written by either writer.

### 8. Undo L3 and redo
- `git -C "$LIVE" reset --keep 2608230632365c555926528ffc7f93c9d607f246` exits 0. HEAD is `2608230…`, the tree is clean, and shasum stays `7d313fd0…`.
- That dry run equals layer 2's: the `heartbeat` and `rotation` lines are gone, `restart cap` is still there, and the only byte difference is the pid line.
- Redo: `Updating 2608230..0217e10`, `Fast-forward`, HEAD `0217e105d5…`. That dry run equals step 4's; again only the pid differs.
- **Owner's undo step I cannot rehearse:** pause the Healthchecks check (healthchecks.io → autotrader-mac → Pause). After the undo, `pgrep -fl 'trading.operations.heartbeat' || echo none` must print `none`.

### Other findings
- **L3-C (LOW, for the N15e reviewer).** At start, `heartbeat.url_from_keychain` turns a Keychain read failure into `None`. `load_target` then reports `not configured (no Keychain item …)`, so its "unreadable" branch can never be reached from the supervisor. Owner meaning: if the dry run says `present` but the start says `not configured (no Keychain item …)`, the item exists and the start could not read it (for example, the Keychain is locked for launchd). It is not missing.
- **L3-D.** After the L3 start, U3.5 `/status` will show `Restarts today: none (2 children started once today)`, because the L1/L2 starts are now in the archive. It will also show `Recorder/replay: … no run in the log tail`. Both are expected.
- **Power:** at 08:19 the Mac was on AC, battery 25 % and charging.

### Runbook text for L3 (zsh-safe; same window; LIVE and CTL from L1-B1)
- **L3-B1.** Expect the final tip sha (its code equals 0217e10), `2608230`, and ` M .claude/settings.local.json`. The real worktree is at 0217e10 now, and its `backend/.venv/bin/python` points to the live venv.
  ```
  WT=/Users/salomon/aiautomation/.claude/worktrees/ops-p1b
  git -C "$WT" rev-parse HEAD
  git -C "$LIVE" rev-parse --short HEAD
  git -C "$LIVE" status --short
  ```
- **L3-B2.** Grep the final verdict and Docker lines on `ops-p1b:docs/reviews/2026-10-15-ledger.md`.
- **L3-B3.** Expect the last line `deploy check: 9 ok, 0 warn, 0 refused`.
  ```
  ( cd "$WT/backend" && "$WT/backend/.venv/bin/python" "$WT/backend/autotraderctl.py" deploy check --candidate ops-p1b --repo "$LIVE" --expect-parent 2608230632365c555926528ffc7f93c9d607f246 --scope T-3,T-4,T-4b,T-6,T-8,T-9,W6.4 )
  ```
- **L3-B4.** `ff-ok`; the diffstat (about 52+ files); the db status lines; `PAPER kill switch: INACTIVE` plus `identity ORB_V2: RESUMED` (from U2.5).
- **L3-B6** (stop). Add `pgrep -fl 'trading.operations.heartbeat' || echo none` (expect `none`).
- **L3-B7.** Expect `Updating 2608230..<tip>`, `Fast-forward`, then `7d313fd0…  …/paper_bridge.py`, then the tip sha.
- **L3-B8.** Expect the step-4 dry run with the three "item present" heartbeat lines and the `rotation` line, no `!!!`, and `INFO fenced identities = none`.
- **L3-B9.** Expected output:
  - the five lines from FINDING L3-B (`tail -n 6`), ending `LOG_ROTATED date=2026-10-17 … carried=4`;
  - one heartbeat process;
  - `none` for the PREFLIGHT_WARNING grep (FINDING L3-A);
  - `0` for the `LOG_ROTATION_FAILED` count;
  - `/Users/salomon/aiautomation/backend`, then the tip sha.
  ```
  $CTL shadow agent enable
  sleep 20
  tail -n 6 $L/supervisor.log
  pgrep -fl 'trading.operations.heartbeat' || echo none
  grep -h 'PREFLIGHT_WARNING heartbeat' $L/supervisor.log.2026-10-17 $L/supervisor.log || echo none
  grep -c 'LOG_ROTATION_FAILED' $L/supervisor.log
  plutil -extract WorkingDirectory raw ~/Library/LaunchAgents/com.maccabi.autotrader-v1.shadow.plist
  git -C "$LIVE" rev-parse HEAD
  ```
- **Undo L3** (only when flat). Expect `2608230632365c555926528ffc7f93c9d607f246`; the dry run has no `heartbeat` or `rotation` lines but still has `restart cap`, and the pin stays `7d313fd075d2`. Then `pgrep` prints `none`, then pause Healthchecks.
  ```
  $CTL shadow agent disable
  git -C "$LIVE" reset --keep 2608230632365c555926528ffc7f93c9d607f246
  git -C "$LIVE" rev-parse HEAD
  $CTL run --dry-run --date 2026-10-19
  $CTL shadow agent enable
  pgrep -fl 'trading.operations.heartbeat' || echo none
  ```

### Files
Under `/Users/salomon/autotrader-work/rehearsal-weekend`:
- `out/`:
  - `L3-deploycheck-0217e10.txt`
  - `layer3.txt`, `L3-dryrun.txt`, `L3-dryrun-heartbeat-variants.txt`
  - `L3-realstart-console.txt`, `L3-realstart-driver.txt`
  - `L3-B*.txt`, `L3-sentinel.txt`
  - `undo3.txt`, `undo-L3-dryrun.txt`, `redo-L3-dryrun.txt`
  - `livedb-after-L3A.txt`
- `scripts/`: `layer3.zsh`, `l3-check.zsh`, `real_start.zsh`, `real_start.py`, `dry_variants.py`, `sentinel_check.py`, `undo3.zsh`, and the updated `rehearse_bot.py`.
- `sandbox-supervisor.sb`: the sandbox plus the no-outside-signals rule.

The fakehome is kept, as you asked. It now also holds the `b3-L3`, `sup-run` (rotated-log evidence) and `sentinel-rehearsal` scratch copies; all of it holds the real account id and should be deleted when you say.
