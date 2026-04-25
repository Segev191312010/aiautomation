# Phase B — Functional Hardening — Codex Review

**Reviewed:** 2026-04-19
**Diff range:** commits 5b555a6, 28ed1d0, plus fix 845c874
**Reviewer:** codex (mcp__codex-review__codex)

## Commits reviewed

- 5b555a6 feat(dashboard): symbol validation on order entry (B1)
- 28ed1d0 feat(dashboard): order confirmation modal with typed phrase gate (B2)
- 845c874 fix(dashboard): harden ConfirmModal against codex-flagged safety holes (B2-fix)

## Verdict

- CRITICAL: none
- MAJOR (2 flagged, both FIXED in 845c874):
  - **Global Enter-on-window was firing onConfirm regardless of focus** — pressing Enter on Cancel button or any other focused element would submit the order if phrase had matched. FIXED: moved Enter to input's `onKeyDown`.
  - **Live-state confirm: no frozen snapshot** — user could Shift+Tab out of modal, edit order fields, then confirm different values. FIXED: `pendingOrder` state snapshot at submit-time; handleConfirm uses snapshot and modal summary renders from it.
- MINOR (1 flagged, FIXED in 845c874):
  - **No focus trap + no focus restore + instructions excluded from aria-describedby.** FIXED: added Tab/Shift+Tab focus-cycling among focusables inside the dialog; opener activeElement is remembered and refocused on close; instructions id is now part of aria-describedby.

## LGTM (confirmed)

- validateSymbol: clean allowlist regex, structured return, aria-invalid + aria-describedby on input, submit disabled until valid.
- ConfirmModal cleanup (Escape + backdrop): correct; no XSS sinks since React renders summary values.

## Follow-up

None required for this phase. The hardening applies to any future use of ConfirmModal (can be adopted by position close, bot-start, autopilot kill, etc.).
