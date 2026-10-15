v7b 866bca8 verdict=GO addendum: rebase onto 2b81eee + tests/docs only since 12fc726, bridge sha 7d313fd0 unchanged

Reviewer: independent execution-safety (read-only). Worktree .claude/worktrees/n2-bridge-v7b, HEAD 866bca8, clean (0 porcelain lines).

Commits 2b81eee..866bca8: 784c2bd (v7b data), a8cba98 (poll slots/tests), e0e5ae6 (review LOWs), 227b8b3 (ledger), 866bca8 (OPERATOR_PROMPT, CLAUDE.md, decisions).

Range-diff 726b970..12fc726 vs 2b81eee..a8cba98:
- b3e8f9a, e5997af dropped from the range (now in the base, 2b81eee).
- 59d006e ! 784c2bd: the only difference is docs/PAPER_EXECUTION_STATUS.md (the v7a.3 "PENDING" paragraph replaced by 2b81eee's GO text). No backend difference.
- 12fc726 = a8cba98 (identical).
- Cross-check: backend patch e5997af..12fc726 vs 2b81eee..a8cba98 differs only in two hunk-header line numbers (shadow_service.py +1 line from 2b81eee's comment).

Backend since 12fc726 (4 files):
1. trading/operations/shadow_service.py and trading/orchestration/session_runner.py: 2b81eee comment-only content (covered by v7a-head-addendum GO). ast.dump at 12fc726 == at 866bca8 for both.
2. tests/test_day_universe.py (e0e5ae6): test-only. The expected held set adds ORBT iff "ORB_V2" is in the shipped tuple; the monkeypatched widened case is kept. No production code touched.
3. tests/test_v7b_quantities.py (e0e5ae6): test-only, stricter. Net liquidation is sized on the binding leg (max of risk and notional). The skip on unapproved base is replaced by assert approved and base_quantity == recorded. A synthetic $900/$880 row of 3 shares forces floor(3x0.25)=0, and the test asserts the zero branch runs only for multiplier <0.5 (ORB_V2 0.25; the others 0.5/1).
227b8b3 and 866bca8 touch no backend file.

Bridge/pin:
- git diff 12fc726..866bca8 for paper_bridge.py and supervisor.py: empty (blobs 47c75bc / 53e759a identical).
- shasum -a 256 paper_bridge.py (worktree file, blob at 12fc726, blob at 866bca8) = 7d313fd075d29e1e62c11eab52fd7832a0aaab21fd0b616c0ce2ee3f47962af4, which equals supervisor.py:154 REVIEWED_BRIDGE_SHA256.

v7a.3 underneath: git diff e5997af..866bca8 for sendable_identities.py shows only SENDABLE_IDENTITIES ("MOMO_HOD_V2",) changed to ("MOMO_HOD_V2","ORB_V2","VWAP_PB_V2","GAP_GO_V2"). screener.py: empty. e5997af and 2b81eee are both ancestors of 866bca8.

Tests (cwd backend, backend/.venv, -p no:cacheprovider): test_paper_bridge, test_v7b_quantities, test_day_universe, test_sendable_identities, test_supervisor, test_bridge_imports, giving 369 passed in 32.08s. 227b8b3 to 866bca8 changes no backend file, so the 2556-pass full suite still applies.

OPERATOR_PROMPT vs AGENTS.md (branch copy, which 784c2bd widened to the four V2 identities; part 2.5 matches it). Contradictions not listed in Appendix D, all docs-only, none blocking. The precedence clause makes AGENTS.md win.
- Part 5 gives AGENT+ the "paper stop (global) / /closeall" row. That conflicts with AGENTS.md "orders come only from deterministic code" and owner manual orders from the pinned chat. It also contradicts 4.3.5 (/closeall: OWNER) and D-10 ("agents never run stop/resume"), while 4.3.2-4.3.4 grant AGENT+ stop and conditional resume.
- Part 5 has the unattended AGENT editing paper_bridge.py with "re-pin in the same commit" (I-5), and the 6.1 lifecycle has no owner-ask gate. Both conflict with AGENTS.md "Do not commit ... unless the owner asks".
- 4.8/6.1 test only with pytest. AGENTS.md requires SKIP_DOCKER=1 scripts/run_quality_gates.sh before any commit, and Docker gates before shipping.
Suggest fixing these in the next docs commit. This head needs no change.

No backend hunk since 12fc726 is anything other than a test, a comment, or reviewed 2b81eee content. GO.
