# Learning Log

Chronological record of sessions, discoveries, and decisions.

---

### 2026-02-24 — Stage 1: Foundation, Auth Scaffold & Polish
- Completed: Auth scaffold (JWT + bcrypt), settings system (JSON blob + deep merge), toast notifications, error boundaries, loading skeletons, 15 backend tests
- Learned: bcrypt 5.x dropped passlib compatibility — use bcrypt directly, not passlib[bcrypt]
- Learned: All CRUD functions default user_id="demo" so existing callers need zero changes
- Gotchas: ALTER TABLE ADD COLUMN fails silently if column exists — desired behavior for safe migration
- Next: Stage 2a (Chart Core & Volume)

### 2026-02-25 — Stage 2a: Chart Core & Volume
- Completed: ChartToolbar (9 timeframes, 6 chart types, indicator dropdown), VolumePanel, useChart hook, Heikin-Ashi utility, trade marker helpers, indicator endpoint
- Learned: React 18 useRef<HTMLDivElement | null> type incompatibility with JSX ref prop — cast with `as React.RefObject<HTMLDivElement>`
- Learned: lightweight-charts v4.2 takeScreenshot() returns canvas element — use canvas.toBlob() + URL.createObjectURL() for download
- Learned: Bidirectional time-axis sync needs syncingRef + setTimeout(0) to prevent infinite loops
- Gotchas: yfinance does not support native 4h interval — use 1h with 3mo period instead
- Next: Stage 2b (Drawing Tools)

### 2026-02-25 — Stage 2b: Drawing Tools
- Completed: H-line, trendline, Fibonacci retracement via HTML5 Canvas overlay on lightweight-charts
- Learned: Canvas overlay must re-render on chart scroll/zoom via subscribeVisibleTimeRangeChange
- Next: Stage 2c (Multi-Pane Sync)

### 2026-02-26 — Stage 2c: Multi-Pane Sync
- Completed: Crosshair sync across panes, time-axis sync, resizable pane heights via drag handles
- Next: Stage 3 (Stock Screener & Scanner)
