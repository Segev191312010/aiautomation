# N9 — independent execution-safety review of bridge v7b (commit 12fc726, branch bridge-v7b)

v7b 12fc726 verdict=GO bridge_sha=7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4

Scope: `git diff e5997af..12fc726` (19 files, +398/−88; two commits: 59d006e data rows, 12fc726 poll slots + tests),
read in `/Users/salomon/aiautomation/.claude/worktrees/n2-bridge-v7b` (HEAD 12fc726, clean before and after every
run; only the gitignored `backend/.tmp/` was written). `shasum -a 256` of the worktree's bridge =
`git show 12fc726:backend/scripts_local/paper_bridge.py | shasum` = `7d313fd0…2af4` = `REVIEWED_BRIDGE_SHA256`
(`backend/trading/operations/supervisor.py:154`); the same digest is the one `docs/PAPER_EXECUTION_STATUS.md:160`
records. No TWS, Telegram or network; nothing written under `~/.local/share`, `~/.config`, `~/Library`; the live
checkout and the live DB were not opened. Probes ran under a scratch `HOME` (`scratchpad/es_review_v7b/`:
`test_probe_v7b.py` P1–P7, `probes.log`, `full_suite.log`, `run_mutations.py`); mutations ran on a byte-identical copy
of `backend/` (`es_review_v7b/mut/backend`, bridge sha verified equal before and after each restore). The real
`--preflight` subprocess ran against the scratch home only. The review ran during the 2026-10-15 session window
(the CLI's migrate guard said so); the session was not touched.

GO: the diff is data on the order path, every table equals the owner's decision, the fail-closed machinery of v7a
(caps row required, registry/size-table agreement at launch, `--preflight` at the dry run, identity fences) accepts
exactly the four names, and every mutation asked for is killed. No HIGH, no MEDIUM. Three LOW items (two docs/test
accuracy, one runbook text) and an owner-facing consequences list that the first four-sender session needs.

## Data-only judgement, hunk by hunk (`git diff e5997af..12fc726 -- backend/scripts_local backend/trading`)

Every changed non-comment line under the order path (mechanical listing, `grep -E '^[+-]'` minus comments; a second
grep for added `def|if|elif|else|for|while|return|raise|try|except|with|lambda|and|or|not` lines returns nothing):

1. `paper_bridge.py:1-3` docstring (three lines of prose). Data.
2. `paper_bridge.py:156` `BRIDGE_VERSION = "v7a"` → `"v7b"`. The version line.
3. `paper_bridge.py:228` `SENDABLE_STRATEGY_NAMES = frozenset({"MOMO_HOD_V2"})` → the four names. A name set.
4. `paper_bridge.py:232-237` three `Caps(...)` rows inside the existing `MappingProxyType({...})` literal. Table rows;
   `Caps.__post_init__` (v7a.1) validates them at import (1 ≥ 1, entries 1/2 ≥ 1, 30 min in (0, 6 h]).
5. `shadow_service.py:173` `SENDABLE_KEYS = frozenset({"momo"})` → four keys. A key set.
6. `shadow_service.py:263-285` comment block rewritten + `STRATEGY_POLL_OFFSETS` rows for `orb`/`gap`/`vwap`
   moved from 30/45/60 s to 120/135/150 s. Table rows; the momo/orb-v2/gap-v2/vwap-v2/momo-ap rows are byte-identical
   to e5997af (`git show e5997af:… | grep` vs HEAD, both print 15/75/90/105/165).
7. `supervisor.py:154` the pin. One string.
8. `identity_sizing.py:29-31` three `Decimal` rows inside the existing `MappingProxyType({...})` literal; the import-time
   loop (`require_size_multiplier`, `isinstance Decimal`) validates them.
9. `sendable_identities.py:28` the tuple.

No new function, branch, condition, import or call. `STATE_SCHEMA == "paper-bridge-state.v5"`, `Bracket` unchanged,
exit codes unchanged, `test_bridge_imports.py` green. Outside the order path: AGENTS.md, the spec, the status doc, the
fixture (6 rows, no identifiers), `test_v7b_quantities.py` (new) and nine test files whose expectations move from
"MOMO only" to "the four" (each read; `test_dynamic_candidates.py` replaces `poll_schedule("orb","watchlist")` with a
fixed 30 s `PollSchedule` so its restart/refresh assertions keep their timing — a test shortcut, the `orb` key is
unscheduled).

## Claims verified (probe for each; `probes.log`)

1. Tables = the owner's decision (P1): `SENDABLE_STRATEGY_NAMES == {MOMO_HOD_V2, ORB_V2, VWAP_PB_V2, GAP_GO_V2}`,
   `IDENTITY_CAPS` rows (3/none/60 m, 1/1/30 m, 1/2/30 m, 1/1/30 m), `caps_problem() is None`,
   `global_max_open() == 6`, `caps_lines()` = the five lines, `MAX_DAILY_LOSS_PCT/NOTIONAL/RISK = 2/25/1` unchanged,
   `IDENTITY_CAPS` still a read-only mappingproxy. Repo pins: `test_paper_bridge.py:3346-3370`,
   `test_v7b_quantities.py::test_the_shipped_tables_are_the_owners_tables`, `test_sendable_identities.py:114-116,161`.
2. `--preflight` as the supervisor runs it (P2, real subprocess `python scripts_local/paper_bridge.py --date 2026-10-19
   --preflight`, cwd backend, scratch HOME with a fake `config.toml` and a DB holding only an empty
   `autotrader_execution_fences`): exit 0; lines `bridge sha256=7d313fd0…`, the five caps lines, `fenced identities =
   none`; no `bridge/` state dir, no `-wal/-shm`. With `paper:ORB_V2` stopped: `CRITICAL fenced identities = ORB_V2`,
   still exit 0 (the gate skips, the owner is told). A dropped `ORB_V2` row (P2b, on the scratch copy): exit 78,
   `CRITICAL PREFLIGHT_REFUSED caps: ORB_V2`.
3. Sizing (P3, P4): `IDENTITY_SIZE_MULTIPLIER == {1, 0.25, 0.5, 0.5}`, `identity_multiplier(name, sendable=True)` equals
   each row, `ORB_V9` → `None`; `registry_problems() == []`, `registry_refusals() == []`; `SENDABLE_KEYS` names map to
   exactly the tuple; every sendable key is in `STRATEGY_STARTS`/`STRATEGY_UNIVERSES` (orb-v2/gap-v2/vwap-v2 09:28,
   momo 09:55); the V1 keys are not scheduled. Arithmetic: per-identity max risk 0.5/0.125/0.25/0.25 % and notional
   15/3.75/7.5/7.5 %; all caps full = **2.125 % risk, 63.75 % notional** (confirmed). The bridge's 1 %/25 % per-order
   ceiling cannot bind at the base policy for any identity (P4b: a full-size bracket of each identity at NLV 100k passes
   `_size_problem`; it binds at 501 sh of a 300-sh MOMO order, or only if the bridge's own net-liq reading is below 60 %
   (notional) / 50 % (risk) of the value the pipeline sized on — MOMO is the closest; ORB_V2 has an 8× margin).
4. The golden (P4c, P4d): the fixture has 6 rows (MOMO/V1 intents of 10-12/13, all ≈ $100k notional, no identifiers:
   regex for `DU\d{5,}`, 9+-digit numbers, `run-<hex>`, `chat_id`, `@` → nothing). `floor(q × m)` is exercised:
   `scaled_quantity` per row per multiplier (≥ 126 sh) and end to end through `size_and_evaluate_trade` (engine q =
   1405/335/1029/116/1781/2779 → ×0.25/0.5). The golden's own `size_scaled_to_zero` arm is **dead on this fixture**
   (`SCALED_TO_ZERO_COUNTS` all 0, `expected == 0` never true, P4d) — the zero branch for the shipped 0.25 is covered
   elsewhere: `test_identity_sizing.py::test_quantity_is_floor_of_policy_quantity_times_multiplier[ORB_V2-20000-900.00…]`
   (q = 3 → 0 → `codes == (size_scaled_to_zero,)`, verified by running its fixture). See F-2.
5. Poll slots (P5): table = momo 15, orb-v2 75, gap-v2 90, vwap-v2 105, orb 120, gap 135, vwap 150, momo-ap 165;
   `poll_offset_problems == []`; consecutive gaps ≥ 15 s; scheduled children's slots unchanged vs e5997af; for every V1
   key on every universe `bar_freshness_limit` < 300 s with ≥ 25 s pass budget (orb scanner 145 s, gap scanner 160 s,
   vwap scanner 175 s, watchlist/gappers 240 s) and `poll_schedule` constructs. The collision model
   (`test_managed_momo.py::test_no_identical_request_collides…[0.65|1.0|1.3]`) passes at HEAD. Production readers of
   `STRATEGY_POLL_OFFSETS`: `shadow_service.bar_freshness_limit`/`poll_schedule` only (→ `run_shadow_session:556` for
   the launched key); `bar_cache.py` mentions it in comments. Nothing else reads a V1 slot. Note: `vwap-v2` at 60 s
   passes `poll_offset_problems` (exactly 15 s from orb-v2) — the pacing safety of the V2 slots is a test-time
   invariant (the collision model), not a launch-time refusal; the mutation below confirms the test catches it.
6. Gate behaviour under the shipped tables (P6, FakeBroker harness from the suite): five holders (2 MOMO + ORB + VWAP +
   GAP) → second ORB/VWAP/GAP decisions skip `<ID>: max 1 positions already active`, a third MOMO is SENT (slot 6), a
   fourth MOMO skips `MOMO_HOD_V2: max 3 positions already active`, `ORB_V1` skips `shadow-only strategy`,
   `slots_used() == 6`. A foreign live order (owner's working order in TWS) takes a slot and blocks its symbol; a bare
   owner position with no working order takes **no** slot (`slots_used` = holders + unmanaged live *orders*) but its
   symbol is refused `account already holds …` (P6b); with 4 holders + 1 foreign order, GAP_GO_V2 takes slot 6 and MOMO
   skips `max 6 positions/entries already active`. Entries/day: a cancelled unfilled ORB_V2 parent counts
   (`entries_today` = `parent is not None`, `:1269-1271`) → `ORB_V2: max 1 entries/day already sent`; VWAP_PB_V2's second
   entry sends, a third is `max 1 positions` (P6c). Deadlines: 15:00/15:30 on 2026-10-19; 12:00/12:30 on the 13:00
   close of 2026-11-27 (P6d). Identity brake: `paper:ORB_V2` stopped → `identity_stopped:ORB_V2`, MOMO still sends; the
   SKIP line is forwarded by `bridge_event_text` ("identity" filter, v7a F-2 fix) and `is_real_decision` is True for the
   four and False for `ORB_V1` (P6e). CLI (P7, scratch DB): `paper stop --identity {ORB_V2,VWAP_PB_V2,GAP_GO_V2}` → 0,
   `ORB_V9` → 2, `resume --identity ORB_V2` → 0, bare `resume` → 2, rows `paper:<ID>` 0/1/1, `paper status` lists them.
7. Daily loss is shared: `realized_pnl` (`:1284-1286`) sums every bracket of the date regardless of identity; the
   pipeline's own `max_open_positions=3` / 45 % total notional never aggregate across children
   (`session_runner.py:735-737` builds the portfolio with `owned_con_ids=frozenset()` → `positions=()`), so the bridge's
   slots/daily-loss/per-symbol checks are the only cross-identity limits — as in v7a.
8. Identifier scan: `git diff e5997af..12fc726 | grep -nE 'DU[0-9]{5,}|[0-9]{9,}|@[a-z]|token|chat_id|api_key|secret'`
   → only `@pytest.mark.*`. The fixture note contains the word "owner" in prose, no ids.

## Findings (defects first; none on the order path)

F-1 LOW (docs) — `docs/PAPER_EXECUTION_STATUS.md:160-166` says "`git diff v7a.1..v7b` on the bridge and the trading
package is rows, names, the version line and the pin — no logic". v7a.2 (726b970) and v7a.3 (e5997af) are logic
commits inside that range (`all_session.py`, `notify_service.py`, `autotrader_routes.py`, `sendable_identities.py`,
`screener.py`). True statement: `git diff e5997af..12fc726` (v7a.3..v7b). Required fix: name the base commit in the
deploy commit's docs entry, with this verdict replacing PENDING.

F-2 LOW (test accuracy) — `tests/test_v7b_quantities.py:716-750` docstring/comment say the account is "sized so the
risk leg is binding at exactly `recorded` shares"; with `net_liq = recorded × unit_risk × 200` the notional leg binds
for all six rows (engine q 1405/335/1029/116/1781/2779 ≠ recorded; P4d), the `if not base.approved: continue` arm is
never taken, and the `expected == 0` arm is dead (`SCALED_TO_ZERO_COUNTS` all 0). The test still pins
`floor(base × m)` end to end and the tables, so it protects what matters; the zero branch for 0.25 is pinned by
`test_identity_sizing.py` (q = 3 → 0). Required fix (test-only, no re-pin): either size `net_liq` so the recorded
quantity is reproduced (`min(risk, notional)` legs) or reword; add one synthetic row (or reuse the 20000/900 fixture)
so the golden's zero arm runs. Also the plan's wording "the 10-14 recorded decisions of orb-v2/vwap-v2/gap-v2 replayed"
could not be met (no V2 decision was accepted by 10-15); the file says so.

F-3 LOW (runbook) — the R-B STEP 3/STEP 4 text in the plan expects `sendable identities = MOMO_HOD_V2` and one caps
line; v7b's dry run relays seven bridge lines (sha, five caps lines, fences). Required fix: the Monday runbook lists
the exact five caps lines (`V7B_CAPS_LINES` in `test_paper_bridge.py:3307-3313`) and `fenced identities = none`;
anything else at STEP 3 or at 16:25 is a stop.

Informational: `trading/manager/context.py:47 BRIDGE_MAX_OPEN = 3` is MOMO's row (v7a.1: the manager counts only
MOMO brackets) — consistent with v7b, the v7a review's open question 4 note is not in the commit; harmless.
Mutation-harness note for the next reviewer: a same-byte-size edit of the bridge (`1`→`2`) within the same second as
a restore is masked by the stale `scripts_local/__pycache__/paper_bridge*.pyc` (pyc validity is mtime-seconds + size);
my first M3 run "survived" for that reason only — run mutations with `PYTHONDONTWRITEBYTECODE=1` and caches removed.

## What changes for the owner on the first four-sender session (Monday 2026-10-19)

- Who sends: ORB_V2 (quarter size), VWAP_PB_V2 and GAP_GO_V2 (half size) from their 09:35 signal bar (children start
  09:28, decisions reach the bridge ≈ 09:36–09:37 after their +75/+90/+105 s polls); MOMO from 10:00 as before. Every
  V2 order carries `orderRef autotrader:<ID>:<12>`; their `SENT/FILL/EXIT` and reviewed-decision lines reach the phone
  at once (v7a.2), `ORB_V1`/`VWAP_PB_V1`/`GAP_GO_V1` stay in the digest.
- Before MOMO's first bar nothing account-level can be exceeded: at most three V2 positions (0.625 % risk, 18.75 %
  notional). After 10:00 the book can reach 6 positions, **2.125 % open risk and 63.75 % notional** (v7a: 1.5 % /
  45 %). The −2 % stop is realized-only and blocks, never flattens: the worst realized day is just under 2 % plus
  whatever is open at that moment, ≈ 4.1 % (v7a ≈ 3.5 %). No code caps aggregate notional across identities.
- Slots are first-come: a day where all three V2s fire early leaves MOMO three of six; any foreign live order
  (an owner working order in TWS, a stray bracket leg from an earlier session) takes one more, so MOMO can be capped
  at 2 without MOMO's own cap being reached (skip text `max 6 positions/entries already active`). A bare owner
  position without a working order takes no slot but blocks its symbol for every identity.
- Entries/day counts attempts: an ORB_V2 or GAP_GO_V2 parent that is sent and cancelled unfilled (`ENTRY_TTL` 10 min,
  or the window close) ends that identity's day (`max 1 entries/day already sent`); VWAP_PB_V2 gets a second attempt
  only after its first position is closed/cancelled (1 position). This is the "first signal only" limitation the plan
  accepted.
- `size_scaled_to_zero`: at the inferred account size (the recorded intents are all ≈ $100k = the 15 % notional leg
  → NLV ≈ $667k) ORB_V2 sizes to zero only for an entry above ≈ $6,250 or a unit risk above ≈ $208; none of the six
  recorded setups would have been zero at any multiplier (quarter size of the smallest, FANG 504 sh, is 126). It
  becomes real only if NLV falls near $10k (ORB_V2 zero above ≈ $94/share). Not a session-day concern.
- Last entries: MOMO 15:00, the three 15:30 (12:00 / 12:30 on a 13:00 close); the 15:45 flatten is unchanged and
  identity-blind.
- Brakes: `autotraderctl paper stop --identity <ID>` / `resume --identity <ID>` per identity (ORB_V9 → exit 2);
  `/closeall` + `paper stop` global. The connect line at 16:25 Israel must be followed by the five `caps …` lines and
  `fenced identities = none`; a `CRITICAL fenced identities = …` means a stale stop row; `PREFLIGHT_REFUSED caps` /
  `identity fences unreadable` means no entries will be sent (the dry run shows the same).
- Rollback unchanged: FLAT, no working bot orders, `git reset --keep $PREV_SHA`, dry run shows the v7a.1 pin
  `9790eb3c1e81`. Never mid-session (a V2 bracket placed by v7b has no v7a.1 caps row → `identity_caps_missing` skips
  are not the issue, but a same-date state file would be managed by a bridge whose allowlist does not know the name).

## Test tail and mutation results

Targeted (brief's command, worktree, `HOME` → scratch, `-p no:cacheprovider`): `tests/test_paper_bridge.py
tests/test_bridge_imports.py tests/test_v7b_quantities.py tests/test_sendable_identities.py tests/test_identity_sizing.py
tests/test_all_session.py tests/test_data_reliability.py tests/test_managed_momo.py tests/test_notify_manager.py
tests/test_supervisor.py` → `490 passed in 42.04s`.

Full suite (worktree, `HOME` → scratch, `-p no:cacheprovider`, `es_review_v7b/full_suite.log`):
`2556 passed, 1 skipped in 144.65s` — the commit's count reproduces isolated from the live home.

Own probes (`es_review_v7b/test_probe_v7b.py`, run with `-p tests.conftest`): `15 passed` (P1 tables, P2/P2b real
preflight 0/78, P3 rows + registry + schedule, P4/P4b/P4c/P4d arithmetic + ceiling + fixture + golden legs, P5 slots,
P6/P6b/P6c/P6d/P6e gate/slots/entries-per-day/deadlines/brake, P7 CLI).

Mutations (`es_review_v7b/run_mutations.py` on the scratch copy, caches cleared, `PYTHONDONTWRITEBYTECODE=1`):
M1 ORB_V2 dropped from the bridge set → KILLED (`test_v7a_exactly_the_sendable_names_send[ORB_V2]`,
`test_v7a_every_sendable_name_has_a_caps_row…`, and the connect line's `PREFLIGHT_REFUSED caps: ORB_V2` breaks
`test_a_ledger_error_is_not_a_restart`) · M2 ORB_V2 multiplier 0.5 → KILLED (golden `test_the_shipped_tables…`,
`test_identity_sizing` ×2) · M3 VWAP_PB_V2 caps 2 positions → KILLED (4: caps-row test, connect line, preflight lines,
golden) · M4 vwap-v2 slot 60 → KILLED (collision model ×3, `test_data_reliability` ×2, `test_all_session` slot test).
4/4. Bridge sha on the copy after every restore = `7d313fd0…`.

## Open questions

1. F-1/F-3 are docs; does the owner want them in the deploy commit (which replaces PENDING with this verdict anyway)?
2. The −2 % stop is realized-only; with six slots the owner should decide, awake, whether ≈ 4.1 % worst-case realized
   day is acceptable for PAPER evidence or whether a v7c logic commit should add an open-risk term (block entries when
   realized loss + open risk ≥ 2 %) or an aggregate notional cap — not for Monday, and not data-only.
3. Should `poll_offset_problems` also encode the collision-model constraint (`momo-ap − vwap-v2 ≥ 60 s`) so a slot
   edit is refused at launch rather than only by a test? Logic, future re-review.
4. The golden's "recorded V2 decisions replay equal except quantity" (plan v7b item 4) cannot be built until a V2
   decision is accepted; after the first four-sender session the fixture can gain real V2 rows (identifier-free).
