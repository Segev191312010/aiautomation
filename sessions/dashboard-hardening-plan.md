# Dashboard Hardening Plan — Comprehensive Phased Approach

**Created:** 2026-04-17
**Source:** 8 review agents + Codex + user refinement
**Approach:** Maintain Zustand routing, backward compatible, phased execution

---

## Phase 1: Critical Security Fixes (Priority: Immediate) — DONE 2026-04-17

**Goal:** Eliminate high-risk vulnerabilities and enforce secure defaults
**Status:** ALL 3 TASKS COMPLETE — typecheck clean, build clean

### Task 1: Secure WebSocket Token Handling
- **Action:** Modify WebSocket URL logging to exclude JWT
- **File:** `dashboard/src/services/ws.ts`
- **Code Change:** Replace `console.info('[WS] connected to', this.url)` with `console.info('[WS] connected to', this.url.split('?')[0])`
- **Agent:** security-auditor (verify no JWT exposure in logs)

### Task 2: Restore 401 Token Revocation
- **Action:** Add 401 response handler in API client
- **File:** `dashboard/src/services/api/client.ts`
- **Code Change:**
  ```typescript
  if (resp.status === 401) {
    localStorage.removeItem(AUTH_TOKEN_KEY);
    window.dispatchEvent(new Event('api:unauthorized'));
  }
  ```
- **Agent:** react-typescript (ensure type safety)

### Task 3: Secure Bootstrap Secret Management
- **Action:**
  1. Add `.env.local` to `.gitignore`
  2. Rotate `JWT_BOOTSTRAP_SECRET` in backend
  3. Audit other sensitive env vars
- **Agent:** security-auditor (scan for exposed secrets)

---

## Phase 2: Functional Improvements (Priority: High)

**Goal:** Enhance reliability and user trust

### Task 4: Symbol Validation & Error Handling
- **Action:**
  1. Create `validateSymbol()` utility with regex `/^[A-Z0-9\-\.]{1,20}$/`
  2. Apply to all symbol inputs (QuickOrderForm, TradeBotPage)
  3. Show error state on invalid symbols
- **Agent:** ux-reviewer (design error UI)

### Task 5: Order Confirmation Modal
- **Action:**
  1. Develop `<ConfirmModal>` component with:
     - Order summary (symbol, quantity, type)
     - "CONFIRM" text input requirement
     - Cancel/Confirm buttons
  2. Integrate into QuickOrderForm flow
- **Agent:** react-typescript (implement modal logic)

---

## Phase 3: Performance Optimization (Priority: Medium-High)

**Goal:** Reduce latency and resource usage

### Task 6: Adaptive Polling System
- **Action:**
  1. Create `useWebSocketHealth()` hook to monitor connection status
  2. Adjust REST polling intervals:
     - 5s when WS disconnected
     - 30s when WS connected
     - 5m during market hours off
- **Agent:** performance-engineer (benchmark request rates)

### Task 7: Indicator Algorithm Optimization
- **Action:** Replace O(n^2) implementations with sliding windows:
  ```typescript
  // Original SMA (O(n*k))
  const sma = bars.map((_, i) =>
    bars.slice(i-period+1, i+1).reduce((sum, b) => sum + b.close, 0)/period
  );

  // Optimized SMA (O(n))
  let sum = bars.slice(0, period).reduce((sum, b) => sum + b.close, 0);
  const sma = [sum/period];
  for (let i = period; i < bars.length; i++) {
    sum += bars[i].close - bars[i-period].close;
    sma.push(sum/period);
  }
  ```
- **Agent:** refactor (audit all indicator functions)

---

## Phase 4: Backend Enhancements (Priority: Medium)

**Goal:** Strengthen server-side defenses

### Task 8: CORS Configuration
- **Action:**
  1. Verify CORS middleware configuration
  2. Ensure allowed origins match deployment:
     ```python
     app.add_middleware(
       CORSMiddleware,
       allow_origins=["http://localhost:5173", os.getenv("FRONTEND_ORIGIN")],
       allow_credentials=True,
       allow_methods=["*"],
       allow_headers=["*"],
     )
     ```
- **Agent:** backend-architect (verify middleware setup)

### Task 9: WebSocket Origin Checks
- **Action:** Add origin validation middleware:
  ```python
  async def validate_origin(request: Request):
    origin = request.headers.get("origin")
    if origin not in ALLOWED_ORIGINS:
      raise HTTPException(status_code=403, detail="Invalid origin")
  ```
- **Agent:** security-auditor (test edge cases)

---

## Phase 5: Testing & Validation (Priority: High)

**Goal:** Ensure reliability across changes

### Task 10: Unit Test Coverage
- **Action:**
  1. Write tests for:
     - Indicator calculations (SMA, EMA, RSI, MACD, BB, VWAP)
     - Auth token revocation flow
     - WebSocket reconnection logic
  2. Achieve 100% coverage for `utils/indicators.ts`
- **Agent:** test-automator (generate test matrix)

### Task 11: Integration Tests
- **Action:**
  1. Test full order lifecycle (place/cancel)
  2. Verify watchlist persistence across sessions
  3. Simulate WebSocket disconnections/reconnections
- **Agent:** test-automator (execute test scenarios)

---

## Implementation Roadmap

| Phase | Task | Estimated Time | Agent |
|-------|------|---------------|-------|
| 1 | Secure WebSocket Logging | 1h | security-auditor |
| 1 | Restore 401 Handler | 2h | react-typescript |
| 1 | Secure Bootstrap Secret | 1h | security-auditor |
| 2 | Symbol Validation | 3h | ux-reviewer |
| 2 | Confirmation Modal | 4h | react-typescript |
| 3 | Adaptive Polling | 2h | performance-engineer |
| 3 | Indicator Optimization | 5h | refactor |
| 4 | CORS Configuration | 1h | backend-architect |
| 4 | WebSocket Origin Checks | 2h | security-auditor |
| 5 | Unit Tests | 4h | test-automator |
| 5 | Integration Tests | 3h | test-automator |
| **Total** | | **~28 hours** | |
