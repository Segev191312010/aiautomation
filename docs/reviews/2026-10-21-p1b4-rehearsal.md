# Rehearsal P3-A (Sentinel live): candidate ops-p3a 666f06d

- Candidate: `ops-p3a` head `666f06db94812bb0eecc2b5451909dad80912e3a`. Its `backend/` is identical to `7b87f7ef6fb84c6928d0d28a9d21069b3759223d`: `git diff --stat 7b87f7e HEAD -- backend` printed nothing.
- Rehearsal "live": a clone of `/Users/salomon/aiautomation` at `9558442b4e0d200f6244e1b7b7a62fb757ad5648`, at `~/autotrader-work/rehearsal-p3a/repo`.
- Run on 2026-10-21 (Wed), from 13:42 Israel time. Every live-path read (DB backup, bridge and notify state copies, config, clone, venv copy) finished by 13:46 Israel time, well before 14:50.
- Every command ran through `~/autotrader-work/rehearsal-p3a/sb.sh`, which sets:
  - `sandbox-exec -f ~/autotrader-work/sandbox-rehearsal-p3a.sb`;
  - `HOME` = a fake home under the rehearsal dir;
  - `DB_PATH` = that home's canonical DB;
  - `PYTHONDONTWRITEBYTECODE=1`, plus `python -B`.
- Telegram was the tests' `FakeTelegram` (an `httpx.MockTransport`); nothing left the process, and every text below was captured from it. The bot token was the tests' fake. Nothing touched the Keychain, `launchctl` or ports 7496/7497/4000-4002.
- Raw outputs are in `~/autotrader-work/rehearsal-p3a/out/`, and the scripts in `~/autotrader-work/rehearsal-p3a/scripts/`.

## Results

| # | Scenario | Expected | Observed | Result |
|---|---|---|---|---|
| 1 | Setup: clone at 9558442, `merge --ff-only` to the candidate | HEAD = 666f06d…; `grep -c '^SENTINEL_FENCES_LIVE = True$'` = 1 | `Fast-forward`; HEAD `666f06db94812bb0eecc2b5451909dad80912e3a`; count `1`; venv imports the clone's sentinel.py with `SENTINEL_FENCES_LIVE= True` | PASS |
| 2 | DB copy (backup API from a `mode=ro` connection) | schema v16, intact, live DB never opened writable | `quick_check=ok`, schema 16 (`manager-day-plans`), 266412032 bytes in 0.5 s, 13:43 Israel time | PASS |
| 3 | Live fence through the real notifier path | `paper:ORB_V2` stopped=1 `sentinel: daily loss …`; exactly one ⛔ text; a second check the same day writes nothing; the pinned bridge says fenced; the dry run shows `CRITICAL fenced identities = ORB_V2` | All as expected (verbatim below) | PASS |
| 4 | Owner resume through the clone's CLI | `PAPER identity fence ORB_V2: RESUMED`; the reason gains ` (lifts sentinel fence of 2026-10-21)`; no second fence that day | All as expected, including after the notifier's memory is deleted | PASS |
| 5 | Undo drill (`reset --keep 9558442…`) | HEAD = 9558442…, `= False` count 1; the same scenario is log-only (🔎, no row); a row written while live stays until `paper resume` | `reset exit=0`; HEAD `9558442b4e0d200f6244e1b7b7a62fb757ad5648`; False count `1`, True count `0`. The same scenario gives one 🔎 text and an unchanged table. The live-written row stays STOPPED (status, dry run, bridge predicate, log-only check) until `paper resume` lifts it with the suffix | PASS |
| 6 | Real sessions on disk, judged live and log-only | nothing would be fenced on 10-20 | Nothing on any date (10-13, 10-14, 10-15, 10-16, 10-19, 10-20) in either mode. The positive control fences MOMO_HOD_V2 (streak 4) | PASS |

## 1. Setup (out/01-*.txt)

```
## before
9558442b4e0d200f6244e1b7b7a62fb757ad5648
Updating 9558442..666f06d
Fast-forward
 backend/tests/test_nightly_evidence.py     |   9 +-
 backend/tests/test_notify_service.py       |  11 +-
 backend/tests/test_sentinel.py             | 151 +++++++++++-
 backend/trading/operations/sentinel.py     |  31 +--
 docs/OPERATOR_PROMPT.md                    |  61 +++--
 docs/reviews/2026-10-15-ledger.md          |  15 ++
 docs/reviews/2026-10-18-p1b4-n16-review.md | 353 +++++++++++++++++++++++++++++
 7 files changed, 563 insertions(+), 68 deletions(-)
## after
666f06db94812bb0eecc2b5451909dad80912e3a
1
```

- Clone: `git clone --no-hardlinks --no-checkout /Users/salomon/aiautomation repo`, then `git checkout -B rehearsal-live 9558442…`. Before the merge, `grep -c '^SENTINEL_FENCES_LIVE = False$'` was `1`.
- `repo/backend/.venv` is laid out as the worktrees do it:
  - `lib` is a symlink to the live venv's `lib`;
  - `pyvenv.cfg` is copied;
  - `bin/python`, `bin/python3`, `bin/python3.11` and `bin/pytest` are symlinks to the live venv's `bin`.
- Proof that it runs the clone's code (`out/01-venv.txt`):
  - `sys.prefix` = `…/rehearsal-p3a/repo/backend/.venv`;
  - the sentinel module is `…/rehearsal-p3a/repo/backend/trading/operations/sentinel.py`.
- The live venv has no editable install (only `distutils-precedence.pth`), so no import can reach the live checkout.
- An insurance copy of the venv `lib` is at `venv-lib-copy/` (237M), in case work runs past 14:50. It was not needed.
- `git status --short --ignored` in the clone shows only `!! backend/.venv/`: no `__pycache__`.

## 2. DB copy (out/02-*.txt)

- `scripts/db_backup.py`: `sqlite3.connect("file:<live>?mode=ro", uri=True).backup(dst)`, run inside the sandbox, which denies writes to `~/.local`.
  - Output: `integrity_check= ok`, `size= 266412032 secs= 0.5`.
  - `data/autotrader.db` sha256 `53f05a34fd251ae13775665bcaecfd9f4e5358d922437f1f3f3649238ff5f096` is kept pristine.
  - No -wal/-shm file was copied by hand.
- Fence table in the copy: `('paper:ORB_V2', 0, 'operator resume via autotraderctl', '2026-10-18T07:52:53.031848+00:00')`. There is no global row.
- **Where the scenarios' DB lives.** The pinned bridge does not read `DB_PATH`: `DB = Path.home() / ".local/share/autotrader-v1/autotrader.db"` (paper_bridge.py:159-160). `autotraderctl run` also refuses `--db-path` ("its children use the canonical database", cli.py:1046).
  - So each scenario's working DB is a backup-API copy of `data/autotrader.db` at the fake home's canonical path (`scripts/mkhome.py`).
  - `DB_PATH` is set explicitly to that same file, and `--db-path` passes it to the CLI. The notifier, CLI, dry run and bridge predicate therefore all read one file.
- Fake config: the live `config.toml` with its account id replaced by the documented fake `DU1234567`. Everything else is unchanged.

## 3. Live fence through the real path (out/03a-03d)

Path under test: `run_notifier` → `_NotifierSession._run_sentinel` → `Sentinel.check` → `fence_identity` → the default `NotifyEnvironment.fence_store` (`SQLitePaperKillStore`) → the copy.

Fakes, as in `test_shipped_switch_writes_fence_row_through_real_store`:
- `FakeTime` from 10:30 ET on Wed 2026-10-21, with `notify_service.SESSION_END` set to the end of each run;
- `runtime_fence._now` = the fake clock;
- `FakeTelegram`;
- a bridge heartbeat that is always current;
- no market context and no hot list.

The synthetic `bridge/2026-10-21.json` was written by the **pinned bridge's own** `StateStore.save` and `Bracket` (sha256 `7d313fd075d2…` == pin, `BRIDGE_VERSION=v7b`). It holds one ORB_V2 bracket:
- `qty 100`, `entry 50.00`, `stop 49.00`, `filled/exit_filled 100`;
- `status closed`, `realized_pnl -130.00`, `exit_reason stop`;
- that is 1.30 R against ORB's 1.25 R limit (1 entry/day × 1.25).

**3a. First check, 10:30-10:40 ET (20 checks):**

```
[s3] code HEAD 666f06db94812bb0eecc2b5451909dad80912e3a; sentinel module /Users/salomon/autotrader-work/rehearsal-p3a/repo/backend/trading/operations/sentinel.py
[s3] SENTINEL_FENCES_LIVE = True
[s3] fence rows before: [('paper:ORB_V2', 0, 'operator resume via autotraderctl', '2026-10-18T07:52:53.031848+00:00')]
LOG CRITICAL autotrader.shadow.sentinel: sentinel: fenced ORB_V2: sentinel: daily loss 1.30 R > 1.25 R (realized -$130.00)
[s3] Telegram (fake, captured) sendMessage count=3:
   TEXT> 🤖 Autotrader notifier on for Wed 21 Oct 2026: signals, orders, exits, market updates and the 16:05 ET report will arrive here.
   TEXT> ⛔ auto-fence ORB_V2: daily loss 1.30 R > 1.25 R (realized -$130.00) — new entries blocked; resume: autotraderctl paper resume --identity ORB_V2
   TEXT> ⚠️ AI day manager: no plan in force since 09:20 ET (no plan written (is the manager child running?)). Managed MOMO (shadow) runs neutral, exactly as MOMO_HOD_V2, until a valid plan arrives. Real MOMO is unaffected.
[s3] auto-fence texts: 1
[s3] fence rows after: [('paper:ORB_V2', 1, 'sentinel: daily loss 1.30 R > 1.25 R (realized -$130.00)', '2026-10-21T14:30:00+00:00')]
[s3] pinned bridge identity_fence_active(db, 'ORB_V2') = True
[s3] pinned bridge fenced_identities(db) = ['ORB_V2']
[s3] notifier memory (sentinel keys): {'sentinel_fence:ORB_V2': '2026-10-21T14:30:00+00:00'}
```

- The existing resumed row (owner, 10-18) was correctly not treated as an owner resume of *today's* fence, so it was stopped.
- The manager-outage text is expected, because the fake session has no manager child. It is unrelated to the Sentinel.

**3b. Second check the same day: a restarted notifier with persisted memory and a bigger loss (ORB 2.30 R), 10:40-10:50 ET:**

```
[s3] Telegram (fake, captured) sendMessage count=0:
[s3] auto-fence texts: 0
[s3] fence rows after: [('paper:ORB_V2', 1, 'sentinel: daily loss 1.30 R > 1.25 R (realized -$130.00)', '2026-10-21T14:30:00+00:00')]
[s3] fence table changed: False
```

**3c. Extra: the same, with the notifier's state file deleted (memory lost), 10:50-11:00 ET.** The row is not rewritten. The texts are the documented "already blocked" and "Fenced at start" ones:

```
LOG WARNING autotrader.shadow.sentinel: sentinel: ORB_V2 already fenced; sentinel: daily loss 2.30 R > 1.25 R (realized -$230.00)
   TEXT> ⛔ Fenced at start — no new entries for:
         • ORB_V2: sentinel: daily loss 1.30 R > 1.25 R (realized -$130.00) (since Wed 21 Oct 10:30 ET)
         Resume is the owner's: autotraderctl paper resume --identity <IDENTITY>
   TEXT> ⛔ auto-fence ORB_V2: daily loss 2.30 R > 1.25 R (realized -$230.00) — new entries already blocked (fenced earlier; its reason is kept); resume: autotraderctl paper resume --identity ORB_V2
[s3] fence rows after: [('paper:ORB_V2', 1, 'sentinel: daily loss 1.30 R > 1.25 R (realized -$130.00)', '2026-10-21T14:30:00+00:00')]
[s3] fence table changed: False
```

**3d. The candidate's dry run** (`cd repo/backend && .venv/bin/python autotraderctl.py run --dry-run --date 2026-10-21`, HOME = fake home), exit 0:

```
bridge       reviewed (sha256 7d313fd075d2)
bridge       INFO bridge sha256=7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4
bridge       CRITICAL fenced identities = ORB_V2
database     schema v16 current (/Users/salomon/autotrader-work/rehearsal-p3a/fakehome/.local/share/autotrader-v1/autotrader.db)
```

- There is no `!!!` line, which matches N16 I-7: the fenced line is CRITICAL with exit 0.
- `heartbeat    unknown (… could not be checked: KeychainReadError)` appears because the sandbox blocks `/usr/bin/security`. This is expected.

## 4. Owner resume (out/04a-04d)

```
[s4] HEAD 666f06db94812bb0eecc2b5451909dad80912e3a; live constant count: 1
[s4] wall clock now: 2026-10-21T10:48:46Z (ET 2026-10-21 06:48)
>>> autotraderctl paper status --db-path <copy>
PAPER kill switch: INACTIVE
identity ORB_V2: STOPPED (sentinel: daily loss 1.30 R > 1.25 R (realized -$130.00))
>>> autotraderctl paper resume --db-path <copy> --identity ORB_V2 --reason "rehearsal"
PAPER identity fence ORB_V2: RESUMED
>>> autotraderctl paper status --db-path <copy>
PAPER kill switch: INACTIVE
identity ORB_V2: RESUMED
```

The stored row is `('paper:ORB_V2', 0, 'rehearsal (lifts sentinel fence of 2026-10-21)', '2026-10-21T10:48:47.305979+00:00')`.

**Clock note.** The CLI stamps with the real wall clock (06:48 ET, 2026-10-21). The session date was chosen as the real ET date for this reason, so `owner_resumed_on` sees the same ET day as a real owner would.

**4b. Further check, 11:20-11:30 ET, persisted memory:** 0 texts and the row is unchanged. The pinned bridge reports `identity_fence_active = False` and `fenced_identities = []`.

**4c. Further check, 11:30-11:40 ET, with the notifier's memory deleted.** This is the strong case, because the fence table alone must hold the owner's resume:

```
LOG WARNING autotrader.shadow.sentinel: sentinel: ORB_V2 not fenced again: the owner resumed today's fence; sentinel: daily loss 2.30 R > 1.25 R (realized -$230.00)
[s3] auto-fence texts: 0
[s3] fence rows after: [('paper:ORB_V2', 0, 'rehearsal (lifts sentinel fence of 2026-10-21)', '2026-10-21T10:48:47.305979+00:00')]
[s3] fence table changed: False
```

**4d. Dry run after the resume:** `bridge       INFO fenced identities = none`. That is the only difference from 3d, apart from the clock and pid lines.

## 5. Undo drill (out/05*)

Two fresh fake homes were built from the pristine copy:
- `fakehome-s5live` takes a fence written by the candidate, then the undo;
- `fakehome-s5fresh` repeats scenario 3 on the restored code.

**5a. Live fence written by the candidate, before the undo (`fakehome-s5live`, 10:30-10:40 ET):**

```
[s3] code HEAD 666f06db94812bb0eecc2b5451909dad80912e3a; ...
[s3] SENTINEL_FENCES_LIVE = True
LOG CRITICAL autotrader.shadow.sentinel: sentinel: fenced ORB_V2: sentinel: daily loss 1.30 R > 1.25 R (realized -$130.00)
   TEXT> ⛔ auto-fence ORB_V2: daily loss 1.30 R > 1.25 R (realized -$130.00) — new entries blocked; resume: autotraderctl paper resume --identity ORB_V2
[s3] fence rows after: [('paper:ORB_V2', 1, 'sentinel: daily loss 1.30 R > 1.25 R (realized -$130.00)', '2026-10-21T14:30:00+00:00')]
```

**5b. The undo**, run from `repo/backend` exactly as the runbook phrases it:

```
## before undo
666f06db94812bb0eecc2b5451909dad80912e3a
>>> git -C .. reset --keep 9558442b4e0d200f6244e1b7b7a62fb757ad5648
reset exit=0
## after undo (N16 LOW-3: by sha and constant)
9558442b4e0d200f6244e1b7b7a62fb757ad5648
False count: 1
True count: 0
bridge sha256 (same for both heads, so not a check): 7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4
imported SENTINEL_FENCES_LIVE = False
```

- The working tree was clean before and after (`status --porcelain=v1` was empty both times).
- The bridge sha is identical for both heads, which confirms N16 LOW-3: only the sha and the constant tell the two heads apart.

**5c. The row written while live stays after the undo** (restored code, `fakehome-s5live`):

```
>>> [HEAD 9558442b4e0d, True=0 False=1] autotraderctl paper status --db-path <fakehome-s5live copy>
PAPER kill switch: INACTIVE
identity ORB_V2: STOPPED (sentinel: daily loss 1.30 R > 1.25 R (realized -$130.00))
[dry] HEAD 9558442b4e0d exit=0 -> 05c-dryrun-restored-row-kept.txt
bridge       CRITICAL fenced identities = ORB_V2
```

The restored (log-only) notifier ran 11:00-11:10 ET with its memory deleted. It reads nothing from the store and writes nothing to it:

```
[s3] SENTINEL_FENCES_LIVE = False
LOG WARNING autotrader.shadow.sentinel: sentinel would fence ORB_V2: daily loss 1.30 R > 1.25 R (realized -$130.00)
   TEXT> ⛔ Fenced at start — no new entries for:
   TEXT> 🔎 auto-fence (log-only): would stop ORB_V2 — daily loss 1.30 R > 1.25 R (realized -$130.00)
[s3] fence rows after: [('paper:ORB_V2', 1, 'sentinel: daily loss 1.30 R > 1.25 R (realized -$130.00)', '2026-10-21T14:30:00+00:00')]
[s3] fence table changed: False
[s3] pinned bridge identity_fence_active(db, 'ORB_V2') = True
```

**5d. Scenario 3 repeated on the restored code** (`fakehome-s5fresh`, pristine copy, the same synthetic ORB loss). This run is log-only:

```
[s3] code HEAD 9558442b4e0d200f6244e1b7b7a62fb757ad5648; ...
[s3] SENTINEL_FENCES_LIVE = False
[s3] fence rows before: [('paper:ORB_V2', 0, 'operator resume via autotraderctl', '2026-10-18T07:52:53.031848+00:00')]
LOG WARNING autotrader.shadow.sentinel: sentinel would fence ORB_V2: daily loss 1.30 R > 1.25 R (realized -$130.00)
   TEXT> 🔎 auto-fence (log-only): would stop ORB_V2 — daily loss 1.30 R > 1.25 R (realized -$130.00)
[s3] auto-fence texts: 1
[s3] fence rows after: [('paper:ORB_V2', 0, 'operator resume via autotraderctl', '2026-10-18T07:52:53.031848+00:00')]
[s3] fence table changed: False
[s3] pinned bridge identity_fence_active(db, 'ORB_V2') = False
[s3] notifier memory (sentinel keys): {'sentinel_would_fence:ORB_V2': '2026-10-21T14:30:00+00:00'}
```

- A second check (10:40-10:50 ET, bigger loss) sent 0 texts, and the table was unchanged.
- The dry run shows `bridge       INFO fenced identities = none`.

**5e. Clearing the kept row after the undo** (restored code's CLI, `fakehome-s5live`). This uses N16's exact wording:

```
>>> [HEAD 9558442b4e0d, True=0 False=1] autotraderctl paper resume --identity ORB_V2 --reason owner: cleared after P1-B.4 undo --db-path <fakehome-s5live copy>
PAPER identity fence ORB_V2: RESUMED
>>> [HEAD 9558442b4e0d, True=0 False=1] autotraderctl paper status --db-path <fakehome-s5live copy>
PAPER kill switch: INACTIVE
identity ORB_V2: RESUMED
   ('paper:ORB_V2', 0, 'owner: cleared after P1-B.4 undo (lifts sentinel fence of 2026-10-21)', '2026-10-21T10:52:02.631972+00:00')
bridge       INFO fenced identities = none
```

- The clone stays at `9558442…` (branch `rehearsal-live`) after the drill, with a clean tree.

## 6. Real sessions on disk, judged live and log-only (out/06*)

**Inputs.**
- `~/.local/share/autotrader-v1/bridge/*.json` was copied read-only into `bridge-real/` at 13:43 Israel time. The sha256 of each copy equals its original (`out/06-bridge-copy.txt`), and every file has 0 account-id hits.
- The live notifier's per-date state was copied to `notify-real/`. None of its six files holds a `sentinel*` memory key. That includes 10-20, the first log-only session, so the live notifier sent no 🔎 and no blind line that day. This agrees with N16.

**Method** (`scripts/s6_replay.py`, on a scratch DB copy in `fakehome-s6`).
- The state directory for date D holds the real files of the dates before D.
- D's own file is replayed as it grew: its brackets in `sent_at` order, one prefix per check, then the real file byte-for-byte.
  - The state has no close time, so this approximates the intraday order.
  - The replay clock is synthetic: 10:00 ET plus 5 minutes per step, inside the watch window, with a current heartbeat.
- Live mode uses the real `SQLitePaperKillStore`, and the fence table is restored to the pristine rows between dates.
- Log-only mode sets `SENTINEL_FENCES_LIVE = False` and uses a store that raises if called.
- It ran on the candidate (`out/06-replay-candidate.txt`) and again on the restored 9558442 (`out/06-replay-restored-9558442.txt`). The two outputs are identical apart from the HEAD line.

| Session | Sendable trades (closed) | Daily loss vs limit | Trailing losing run (ledger, dates ≤ D) | Live would fence | Log-only 🔎 |
|---|---|---|---|---|---|
| 2026-10-13 | MOMO_HOD_V2 T −523.56 | MOMO 0.67 R vs ≥ 2 R | MOMO 1 (today) | nothing | 0 |
| 2026-10-14 | none (empty state) | — | MOMO 1 (latest 10-13) | nothing | 0 |
| 2026-10-15 | none closed (IP, CPRT, VEEV `open`, realized 0; the rest skipped) | — | MOMO 1 (latest 10-13) | nothing | 0 |
| 2026-10-16 | MOMO_HOD_V2 HST −608.25, MCHP −812.43 | MOMO 1.88 R vs ≥ 2 R | MOMO 3 (today) | nothing | 0 |
| 2026-10-19 | none (no session; the Mac hibernated) | — | MOMO 3 (latest 10-16) | nothing | 0 |
| 2026-10-20 | VWAP_PB_V2 WELL −261.25 (KEY skipped) | VWAP 1.03 R vs > 2.5 R | MOMO 3 (latest 10-16), VWAP 1 (today) | **nothing** | 0 |

**Positive control** (`out/06-replay-candidate-positive-control.txt`). This shows the replay can see a fence, and reproduces N16 LOW-2.
- One synthetic closed MOMO_HOD_V2 stop-out (CTRL, −210.00 on 1000 × 0.20) was added to the scratch copy of 10-20 only. The real copy was not changed.
- Result:

```
[s6] log-only 2026-10-20 ... 🔎 auto-fence (log-only): would stop MOMO_HOD_V2 — losing streak 4 losing trades in a row (-0.72 R, -1.07 R, -0.89 R, -1.05 R net)
[s6] live     2026-10-20 ... rows_written=[('paper:MOMO_HOD_V2', 1, 'sentinel: losing streak 4 losing trades in a row (-0.72 R, -1.07 R, -0.89 R, -1.05 R net)', ...)]
        10:10 ET [sentinel_fence] ⛔ auto-fence MOMO_HOD_V2: losing streak 4 losing trades in a row (-0.72 R, -1.07 R, -0.89 R, -1.05 R net) — new entries blocked; resume: autotraderctl paper resume --identity MOMO_HOD_V2
```

- **What this means for the owner's yes.** Live mode would have fenced nothing on any real session so far. But MOMO_HOD_V2's trailing run is still 3 (T 10-13, HST and MCHP 10-16; the 10-15 positions are invisible to the ledger). So, once live, the next MOMO loss closed with no MOMO win in between fences MOMO_HOD_V2 until the owner resumes it.

## Defects and observations

**No defect was found in the candidate.** Each observation below is either expected behaviour or a rehearsal-method note.

- **O-1 (method; not a code defect).** The pinned bridge reads the canonical DB from `Path.home()` (paper_bridge.py:159-160) and ignores `DB_PATH`. `autotraderctl run` refuses `--db-path` (cli.py:1046).
  - A rehearsal that points only `DB_PATH` at a copy outside the fake home would show a different fence state in the dry run than in the notifier.
  - This rehearsal kept one DB per fake home at the canonical path, with `DB_PATH` equal to it.
- **O-2 (expected; worth a runbook line).** After the undo, a fence row written while live stays. The log-only code never reads the store. So on such a day the owner can see both:
  - `⛔ Fenced at start … ORB_V2: sentinel: …`;
  - a `🔎 … would stop ORB_V2` line for the same identity.

  Both lines are harmless. The undo does not lift the fence; `paper resume` does (5c, 5e).
- **O-3 (expected, documented in N16 Q3).** If the notifier loses its per-date memory while a Sentinel row is stopped, it sends `⛔ … new entries already blocked (fenced earlier; its reason is kept)` once. The row is never rewritten (3c).
- **O-4 (method).** In scenario 4 the CLI stamps the resume with the real wall clock (06:48 ET on 10-21), while the fence carries the simulated 10:30 ET. `owner_resumed_on` compares ET dates only, so the session date was set to the real ET date (2026-10-21). On a real day both stamps are wall-clock and ordered.
- **Pre-existing (N16 I-6).** The phone text's `autotraderctl paper resume --identity <ID>` is shorthand. The owner runs `$CTL paper resume --identity <ID> --reason "…"` from the backend folder.

## Isolation evidence (out/07-live-untouched.txt)

- **Live checkout.** HEAD is still `9558442b4e0d200f6244e1b7b7a62fb757ad5648`. `status --porcelain=v1` shows only ` M .claude/settings.local.json`, which was last modified 2026-10-12 16:51, before this rehearsal. The sandbox denies writes to `/Users/salomon/aiautomation`.
- **Candidate worktree.** HEAD is `666f06d…` and its status is clean.
- **Live DB.**
  - `autotrader.db` mtime is Oct 21 00:37.
  - `-wal` is 0 bytes (00:37).
  - `-shm` mtime is 13:39, before the 13:43 backup.
  - None of them changed.
- **Account-id scan.** `grep -cE 'DU[A-Z0-9]{5,}'` was run over every text file written here (report, `sb.sh`, `out/`, `scripts/`, `bridge-real/`, `notify-real/`, the fake homes' configs and states).
  - The only hits are the documented fake `DU1234567`: in the fake `config.toml` files, `scripts/mkhome.py` (a comment) and this report.
  - **The binary DB copies hold the real account id as copied data**, one line each, never printed: `data/autotrader.db` and the four `fakehome*/.local/share/autotrader-v1/autotrader.db`. Together they are about 1.3 GB. To delete them when done: `rm -f ~/autotrader-work/rehearsal-p3a/data/autotrader.db ~/autotrader-work/rehearsal-p3a/fakehome*/.local/share/autotrader-v1/autotrader.db*`.
- **Network and launchd.** No network, no Telegram send, no Keychain read (the sandbox blocks `/usr/bin/security`; the dry run shows `KeychainReadError`), no `launchctl`, and no port 7496/7497/4000-4002.

## Left for after 23:15

Nothing. Every scenario finished on copies before 14:00 Israel time. The insurance venv copy (`venv-lib-copy/`) was not needed.
