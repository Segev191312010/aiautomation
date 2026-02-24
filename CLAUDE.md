# CLAUDE.md - Project Rules & Workflow

## GOD RULE

**NEVER commit or push anything without explicit user approval.** No exceptions. Always ask first.

---

## Development Method: Relay Race

We develop in a **relay race method**:

1. **Master session** plans features in a waterfall method
2. For each stage, create a **session prompt** using `@session-prompt-generator` to paste into a new isolated session
3. Each isolated session does the work on its designated branch
4. At the end of each stage, create a **handoff note** using `@handoff-generator`

---

## Handoff Document Generator

**Single-use prompt. Copy, fill in, paste into NEW Claude conversation, get output, close.**

### How to Use

1. Copy the prompt below (START to END)
2. Fill in YOUR information about what you built
3. Paste into NEW Claude conversation
4. Get your handoff document
5. Save to `handoffs/` folder
6. Close conversation (done)

### THE PROMPT (COPY FROM HERE)

```
Generate a handoff document for the work I just completed.

DATE: [Today - e.g., 2025-01-03]
SESSION: [Type - e.g., backend-endpoint, frontend-component]
FEATURE: [What you built - e.g., "Video upload API"]

WHAT I BUILT:
[Summary in 2-3 sentences]

FILES:
- [File 1 - e.g., "app/api/videos/upload/route.ts (created)"]
- [File 2 - e.g., "lib/supabase-storage.ts (modified)"]

API/INTERFACE:
[If you built an API or interface, describe it]

TESTED:
- [Test 1 - e.g., "✅ 45-second video uploads successfully"]
- [Test 2 - e.g., "❌ Not tested on iOS yet"]

NOT DONE:
- [Todo 1 - e.g., "Video compression"]
- [Todo 2 - e.g., "Duplicate detection"]

QUESTIONS:
- [Question 1 - e.g., "Should we compress client-side or server-side?"]

NEXT:
[What to work on next]

---

Generate complete handoff document with:
- Proper markdown formatting
- All standard sections
- Code examples where relevant
- Clear and actionable
- Filename: handoffs/[date]-[brief-description].md

Make it ready to copy-paste and save immediately.
```

### (COPY TO HERE)

### Handoff Document Format

Every handoff document must include these sections:

```markdown
# HANDOFF: [What Was Done] → [What's Next]

**Date:** YYYY-MM-DD
**Session:** [Type]
**Status:** ✅ Complete | 🔄 Partial | ❌ Blocked

## What We Built
[2-3 sentence summary]

## Files Created/Modified
- [file list with (created) or (modified)]

## API Specification
[If applicable - endpoint, request, response]

## Testing Completed
- ✅ [what passed]
- ❌ [what wasn't tested]

## What's NOT Done (Future Work)
- [ ] [actionable items]

## Open Questions
- [ ] [decisions needed]

## Next Session Should Be
[Specific tasks + files to modify]
```

### Tips for Better Handoffs

1. **Be specific about what works** - "Upload endpoint handles 100MB files correctly" not just "Upload works"
2. **Document your testing** - Future you needs to know what was actually tested
3. **List open questions** - Capture things you're unsure about while they're fresh
4. **Make "next session" actionable** - Specific enough that anyone can start immediately
5. **Include code examples** - Show exactly how to use what you built

---

## Project Info

- **Name:** TradeBot Dashboard (`trading-dashboard` v2.0.0)
- **Stack:** React 18, TypeScript 5.5, Vite 5.4, Zustand 4.5, Tailwind 3.4, lightweight-charts 4.2
- **Backend:** FastAPI (expected at `:8000`, not in this repo)
- **Handoffs directory:** `handoffs/`
