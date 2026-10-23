**VERDICT: GO for 4788049c24b58a4ad8e205fbfedf8457f8ee90a8** (ops-p3-sizes, P3-C fix 3; one commit on 27a9d34; tree 55fecb9dde5a9a56b5f2f834a3059d18900ab37e). Every finding of mine on 27a9d34 is closed in code, and each closure is pinned by a behavioural test:
- the 2 MEDIUMs: the lot-to-share conversion; resending a failed night's batch;
- the 7 LOWs.

I found two new items, one LOW and one informational. Neither blocks.

# N19b-OPS-2: re-read of fix 3 (27a9d34..4788049)

**Who and when**
- Reviewer: N19b-OPS (the author of the 27a9d34 review), re-reading independently. I did not write the fix.
- Run: 2026-10-23 07:15 to 07:24 Israel. No heavy run fell inside 13:05–13:50 or 14:50–23:15.

**How it ran**
- On fresh `git archive` extracts of 4788049, all under `sandbox-exec -f ~/autotrader-work/sandbox-n19.sb` with a fake HOME:
  - `n19b-ops/fix3`: the baseline;
  - `n19b-ops/probe3`: the probes;
  - `n19b-ops/mut3/*`: one extract per mutant.
- Not touched: no port, launchctl, live state, Keychain, worktree, ledger or AGENTS.md. No real Telegram message was sent.

**Builder account checked:** the "Fix 3" section of `~/autotrader-work/p3-sizes/P3C-REPORT.md`, items 1–9. Items 10–12 are ES findings; I read them for their operations effects only.

## 1. Closure table (my findings on 27a9d34)

| My finding | Fix at 4788049 (file:line) | Behavioural proof | Status |
|---|---|---|---|
| **MEDIUM-1:** share of bar volume 100× too high (shares ÷ round lots) | The single constant `contracts.TWS_VOLUME_LOT_SHARES = Decimal("100")` (contracts.py:496); `shadow_service.IBKR_API_VOLUME_MULTIPLIER` is that same object (:114). The sidecar keeps `bar_volume` in raw TWS lots and converts in exactly one place: `size_pct_of_bar_volume` (decision_sizing.py:203). The writer's payload and `from_payload`'s re-derivation both go through that one property. Grep: no other share-of-volume computation in trading/. The strategy configs' `volume_multiplier = Decimal("100")` literals (momo, gap_go, vwap_pb) predate this layer, sit in fingerprinted specs, and are pinned against the constant by the new test. | `test_sidecar_v2_fields_and_flags`: BF B ×0.5 gives 0.6014 %, flag False; ×1 gives 1.2027 %, flag True, and it stays `shadow_accepted`. `test_the_share_of_volume_counts_shares_in_the_fixtures_unit` reads the fixture's `volume_unit`. `test_the_realism_flags_are_evidence_only`: no production module outside the sidecar reads a flag. Mutants F01 (no conversion) and F02 (converted twice at the factory): KILLED. Intraday bars are `"5 mins"` (tws_actor.py:48), so the "5-minute bar" in the docs is true. | **Closed** |
| *(sub-check)* `SIZING_VERSION` stays 2 | No production v2 line exists: live runs pre-P3-F code, which writes v1. Lines that rehearsal copies made at 27a9d34 would now read as unreadable. Those are copies only; no impact. | `test_sidecar_reads_v1_and_v2` passes. | OK |
| **MEDIUM-2:** a failed night batch is never resent | `unsent_items` reads `p.as_of BETWEEN night − RESEND_NIGHTS(7) AND night`, minus the delivery record (size_notify.py:84, 238-243, 383-390). The query orders by `as_of, created_at, proposal_id`. A late item is marked "(from the <day> night)" (`SizeItem.line(batch_night=)`). Still silent, still one message. | **Probe A:** Monday 502 ×3 gives FAILED; Tuesday's batch is SENT (silent) with `STEP DOWN VWAP_PB_V2 … (from the Mon 2026-10-19 night)` plus GAP's proposal; Wednesday is NOTHING_NEW. **Probe G (no regression):** nights that delivered normally never repeat an earlier night's item, and carry no "(from the" marker. Tests: `test_a_failed_night_goes_out_with_the_next_nights_batch_once`, `test_a_batch_that_fails_two_nights_running_goes_out_once_on_the_third`. Mutants F03 (tonight only), F04 (window 6), F05 (older items re-sent every night, against once per id), F06 (late item unmarked): all KILLED. | **Closed** |
| **LOW-1:** unreadable store with no SENT reads PASS | `blind` = every sendable identity whose size is unknown; rule 4 WARNs `size_in_force_unknown` with or without a SENT (session_check.py:1409-1430). | **Probe F:** checksum mismatch or dropped events table gives rule 4 WARN "no SENT; size_in_force_unknown: … GAP_GO_V2, MOMO_HOD_V2, ORB_V2, VWAP_PB_V2", exit 0. A clean store with no SENT still PASSes. Test `test_session_check_store_unreadable_without_a_sent_is_a_warn`; mutant F12 KILLED. | **Closed** |
| **LOW-2:** de-dup per token hides other identities | Keys are `size_tripwire:<token>:<identity>` (notify_service.py:2861) and `replay_size_tripwire:<token>:<identity>` (overnight_watch.py:375). The identity is the uppercase word after `<token>: `; a line that names none uses `*`. | **Probe K:** the real read line gives `('identity_size_store_unreadable', 'MOMO_HOD_V2')`; the whole-night line gives `('size_writer_night_failed', '*')`; a writer skip gives `('size_writer_identity_skipped', 'VWAP_PB_V2')`. Tests `test_two_identities_blocked_for_one_reason_are_two_pages_each_once` and `test_two_identities_skipped_at_night_are_two_silent_pages`. Mutants F07 (notifier key per token), F08 (night key per token), F14 (identity never read): KILLED. At most 10 tokens × 4 identities, plus `*`: no flood. | **Closed** |
| **LOW-3:** whole-writer failure pages nothing | nightly logs one CRITICAL `size_writer_night_failed: … (<Error>) …` (nightly.py:514). The token is in `SIZE_TRIPWIRES` (notify_service.py:354) and `identity_sizing`. | `test_a_whole_night_writer_failure_is_one_critical_size_token_on_the_phone`: replay exit 0, `not_written`, exactly one CRITICAL, the sender still called, one silent page. M06 and F13 (logged as ERROR) KILLED. | **Closed** |
| **LOW-4:** 13 docs mismatches | All 13 rewritten; see §3. | Re-checked against the code, §3. | **Closed** (one new overstatement: N2-LOW-1) |
| **LOW-5:** 8 surviving mutants | New tests for each one. | Re-run on fresh fix-3 extracts: M04, M05, M06, M08, M09, M11, M13 and M14 all **KILLED** (§4). | **Closed** |
| **LOW-6:** texts imply an approval path | The proposal line now reads "no size change: a size up needs the owner's phone approval, which is not built yet (P3-D); the size stays as it is and this proposal lapses at …" (size_notify.py:160-166). The weekly header and `grants` follow `PromotionProposals.approval_live`, which nightly hands in from `PHONE_APPROVAL_LIVE` (nightly.py:450); the promoter stays pure. Weekly DEMOTION lines add "… unless it waits for new trades or the owner's resume". | Probe A's text shows the new wording. Test expectations were rewritten explicitly. `PHONE_APPROVAL_LIVE = False` (:71). | **Closed** |
| **LOW-7:** night pages ring | `OvernightWatch(quiet=…)`. The composition root wires `quiet=lambda text: notifier.send(text, silent=True)` (telegram_bot.py:1957). Replay-log pages use it (overnight_watch.py:383). The notifier's daytime pages keep `notify()` with sound. | `test_the_night_page_is_silent_and_the_session_start_read_pages_with_sound`: the wiring is pinned, and the next start read pages through the notifier under its own per-date key. Mutants F09 (night page with sound), F10 (quiet wired with sound), F11 (day page silent): KILLED. | **Closed** |
| I-2 (CLI verb runs the writer) | Docs 3.1:278; Part 5:855 makes a full `research replay` against the live DB OWNER-only. | — | Closed (docs) |
| I-1 (child down across the 18:35 rename), I-4, I-5 | Unchanged, by design. M09's new test covers a restart **inside** a file. | — | Accepted |
| I-3 (move `telegram-proposals.json` aside on an undo) | Not in this commit (builder's note). | — | Open: deploy note |

## 2. New findings

| ID | Sev | file:line (4788049) | What | Scenario | Fix |
|---|---|---|---|---|---|
| N2-LOW-1 | LOW (docs, alerts) | docs/OPERATOR_PROMPT.md:745 ("A size problem that lasts is paged again, with sound, by the next session's start read"); notify_service.py:339-343; overnight_watch.py:44-46 | The sound re-page at the session start covers only **read-side** problems: an unreadable store, the ceiling, a broken chain. Writer-only problems never reach a session read, so they only ever page silently, once per night: `size_writer_night_failed`, and `size_writer_identity_skipped` with `step_down_would_break_chain`, `no_fence_store`, `fence_failed_*`, `current_not_in_force` or `session_started`. | A writer failing every night means no automatic step-down ever happens: the demotion safety net is off. The only sign is one silent message a night. The 4.6 rows correctly say "Next morning: read evidence.json sizes … report", but 4.9 promises a sound re-page that never comes. | Correct the sentence and the two comments ("a read-side problem…; a writer problem pages silent each night it lasts"). Owner call: page with sound when the same (token, identity) writer page repeats on a second consecutive night, or let the notifier's 07:55 start announce last night's `sizes.failed` / `not_written` from evidence.json. |
| N2-I-1 | INFO (claim) | builder report Fix 3 ("`trading/contracts.py` (new constant) is not imported by the bridge") | False as worded. Sandboxed `import config, trading.scheduler.sessions, trading.execution.manual_orders` loads `trading.contracts`, as it does `trading.storage.migrations` and `size_schema`. The change is one additive module constant the bridge never uses, so bridge behaviour is unchanged. The imports pin covers only the three files by design (N18 LOW-6, N19a I-5). | None in behaviour. | ES to acknowledge; correct the sentence in the report. No code change. |

Things I looked for that the fix could have broken. None found:
- **Re-delivery of already-delivered items:** the record filters every night in the window. Probe G: none. F05 is killed by the once-per-id test.
- **An old fence notice arriving as if new:** it is marked "(from the <day> night)" inside tonight's silent batch, and fences are still announced at the notifier's 07:55 start. One residue, harmless: a late fence line still says "fenced, its new entries are stopped" even if the owner resumed in between.
- **The sidecar reader on v2 lines from before the fix:** none exist in production. The conversion lives in exactly one place, used by writer and reader alike.
- **Callers of the new 3-tuple `size_tripwire`:** both updated (notify_service.py:2841-2843, overnight_watch.py:371-383).
- **`size_writer_night_failed` and its stack:** only the CRITICAL line matches; stack lines do not.
- **A record that can never be saved** (a symlink): each night re-sends that window's undelivered items, silently, with a WARNING. Accepted; documented in 4.6.
- **Pins:**
  - bridge sha256 b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4, equal to `REVIEWED_BRIDGE_SHA256`;
  - config.py, sessions.py and manual_orders.py match the imports map, so imports_sha stays 7b1df1d4824e;
  - `b5f2339..4788049` touches no bridge, pinned-import, supervisor or deploy-tool file and no AGENTS.md; `migrations.py` and `size_schema.py` changed in earlier commits only, and v18 is unchanged in fix 3.

## 3. Docs re-check (docs/OPERATOR_PROMPT.md at 4788049)

| # (my 27a9d34 list) | Now at | Matches the code? |
|---|---|---|
| 1, 2 (8.2 delivery; "buttons do nothing") | 1185-1203 | Yes. The silent batch, once per id, the 7-night resend with its marker and the new proposal wording are `[NOW]`. Buttons, the 05:55 reminder and the 07:55 note are `[PLANNED]`: "None of the three is sent now". |
| 3 ("nothing is sent silently") | 745 | Yes for the two silent kinds: the batch and the night tripwires. The sentence "a size problem that lasts is paged again, with sound" is overstated: N2-LOW-1. |
| 4 (4.9 rows) | 738-740 | Yes: the night batch, size tripwires (day with sound, night silent, per (token, identity)), and reminder/expiry/buttons `[PLANNED]`. |
| 5 (4.6 rows) | 617-626 | Yes: one row per token with what to do; the writer's reasons; delivery warnings and the resend. The `account_mode_without_ceiling` / `sendable_without_ceiling` row correctly says LAUNCH_REFUSED in supervisor.log; it does not claim a page. |
| 6 (Part 5) | 853-855 | Yes: the runtime step-down is `[NOW]`; a decrease by commit breaks the chain; a full live `research replay` is OWNER-only. |
| 7 (CLI verb) | 278 | Yes. It also describes the ES fix's `step_down_would_break_chain`. |
| 8 (one CRITICAL; ceiling) | 280 | Yes: two CRITICALs, so two pages; LAUNCH_REFUSED for ceiling rows. |
| 9 (rule 4) | 281 | Yes: WARN with or without a SENT (probe F). |
| 10 (realism) | 1159-1169 | Yes: 5-minute bar, stored in lots, `quantity / (lots × 100) × 100`, one constant, evidence only. |
| 11 (10.3) | 1347 | Yes. |
| 12 (timeline) | 209 | Yes, including 23:35–00:30 Israel in the 25 Oct–1 Nov week. |
| 13 (D-15) | 1416 | Yes: a step-down does disagree with AGENTS.md's written size; Appendix A comes before L-sizes. |

## 4. Tests and mutants

- **Baseline** of the mutant set on the fix-3 extract (10 files: size_notify, size_writer, nightly_evidence, overnight_watch, notifier, research_ops, session_check, decision_sizing, promotion_proposals, size_store): **362 passed**.
- **Broader run** (21 files: those plus size_history, shadow_service, sendable_identities, setup_persistence, migration_freeze, live_manager_routes, notify_manager, notify_service, telegram_bot, deploy_check, bridge_imports), 07:20–07:24: **982 passed, 1 skipped**.
- **Probes** (`n19b-ops/probe3/backend/tests/test_n19b_probe3*.py`): A, F (3 cases), G and K, as above.

Mutants: 22. Each ran on a fresh extract of 4788049, sandboxed, with its own HOME. Runner `n19b-ops/scripts/mutate3.py`; logs in `n19b-ops/logs3/`. **22/22 killed.**

| # | Mutant | Killed by (first) |
|---|---|---|
| M04 | record written non-atomically, 0600 | test_the_delivery_record_is_replaced_whole_never_rewritten_in_place |
| M05 | a sender exception aborts the nightly | test_a_raising_sender_never_stops_the_replay |
| M06 | the nightly's writer guard narrowed to ValueError | test_a_whole_night_writer_failure_is_one_critical_size_token_on_the_phone |
| M08 | night de-dup in process memory only | test_a_size_writer_skip_in_the_replay_log_reaches_the_phone_once_a_night |
| M09 | replay log restarts at its end | test_a_size_tripwire_written_while_the_telegram_child_was_down_is_paged_at_its_restart |
| M11 | a corrupt record raises | test_an_unreadable_delivery_record_costs_one_message_never_a_night[corrupt] |
| M13 | ProposalWatch unguarded | test_a_raising_proposal_store_never_stops_the_port_watch |
| M14 | only the first line of each replay-log poll | test_a_size_writer_skip_in_the_replay_log_reaches_the_phone_once_a_night |
| F01 | lots not converted to shares | test_the_share_of_volume_counts_shares_in_the_fixtures_unit |
| F02 | converted twice (shares stored at the factory) | test_sidecar_v2_fields_and_flags |
| F03 | resend window = tonight only | test_a_failed_night_goes_out_with_the_next_nights_batch_once |
| F04 | `RESEND_NIGHTS` 7 → 6 | same |
| F05 | older items re-sent every night (once-per-id broken) | test_batch_sent_once_silent_with_buttons |
| F06 | late item unmarked | test_a_failed_night_goes_out_with_the_next_nights_batch_once |
| F07 | notifier key per token | test_size_criticals_reach_the_phone |
| F08 | night key per token | test_a_size_writer_skip_in_the_replay_log_reaches_the_phone_once_a_night |
| F09 | night page with sound (watch's own sender) | test_a_whole_night_writer_failure_is_one_critical_size_token_on_the_phone |
| F10 | `quiet` wired with `silent=False` | test_the_night_page_is_silent_and_the_session_start_read_pages_with_sound |
| F11 | day page silent | test_size_criticals_reach_the_phone |
| F12 | rule 4 PASS with no SENT and an unknown size | test_session_check_store_unreadable_without_a_sent_is_a_warn[checksum] |
| F13 | night failure logged as ERROR | test_a_whole_night_writer_failure_is_one_critical_size_token_on_the_phone |
| F14 | identity never read (always `*`) | test_size_criticals_reach_the_phone |

## 5. Deploy notes (unchanged from N19b-OPS §5, plus these)

**Still required before L-sizes:**
- live DB at v17;
- a GO verdict naming the deploy head, with `migration=18`, the bridge pin and `imports_sha=7b1df1d4824e`;
- the owner's approval of the Appendix A wording, with the AGENTS.md commit in the deploy head (an AGENTS.md commit after this verdict needs its own re-read);
- rehearsal S1–S5 PASS, with S5 including a failed send followed by the next night's resend;
- Docker gates PASS;
- the pre-mortem read of the latest `proposals.json` for standing demotions, which the first night applies at once.

**Undo:** also move `telegram-proposals.json` aside (I-3, still open).

**What the owner sees on the first night after install:** as in N19b-OPS §5, with three differences:
- the proposal wording is "no size change: … not built yet (P3-D) … lapses at …";
- a writer problem pages **silently**, once per (token, identity);
- a failed batch is sent the next night, marked with its night.

## 6. Suggested ledger line

```
2026-10-23T07:24:31+03:00 | P3-sizes 4788049c24b58a4ad8e205fbfedf8457f8ee90a8 verdict=GO scope=P3-C,P3-E,P3-F,N19a,N19a2,N19b-es,N19b-ops,N19b-ops2 bridge_sha=b40fa40b4acda4d75115c59a1c391e7b639b4dd1d1329381b07b3c6c966df8e4 imports_sha=7b1df1d4824e migration=18 | reviewer N19b-ops2: re-read of 27a9d34..4788049 (P3-C fix 3); OPS MEDIUM-1 closed (contracts.TWS_VOLUME_LOT_SHARES, one conversion in DecisionSizing.size_pct_of_bar_volume: BF B x0.5 0.6014 % unflagged, x1 1.2027 % flagged; flags read by no gate); MEDIUM-2 closed (7-night resend minus the record, late items marked, probe A: Monday's step-down out Tuesday once, Wednesday nothing_new; probe G no repeat); LOW-1..LOW-7 closed (rule 4 WARN with no SENT, per (token, identity) keys, size_writer_night_failed CRITICAL, 13 docs rows re-checked, texts, silent night pages via quiet sender, day pages keep sound); my 8 surviving mutants now killed plus 14 new on the fixes: 22/22; 21 related test files 982 passed 1 skipped; new N2-LOW-1 docs 4.9 overstates the sound re-page (writer-only failures page silent only), N2-I-1 contracts.py is in the bridge's lazy import closure (additive constant, no behaviour change); bridge b40fa40b..., imports_sha 7b1df1d4824e, v18 unchanged | docs/reviews/2026-10-23-p3-sizes-n19b-ops2-review.md
```

The label `P3-sizes` is the coordinator's choice, as before.
