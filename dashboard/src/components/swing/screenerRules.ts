/**
 * Screener methodology definitions — real criteria from SteveDJacobs' dashboard.
 * Used for UI labels, tooltips, and future backend implementation reference.
 */

// ── Qullamaggie Breakout Screener ────────────────────────────────────────────
// Source: Kristjan Kullamagi (Qullamaggie) streams + SteveDJacobs implementation
//
// CRITERIA:
//   1. Relative Strength >= 97 (top 3%) on at least one of: 1W, 1M, 3M, 6M
//      - RS = percentile rank of N-period return across entire universe
//      - NOT a ratio vs SPY — it's a universe-wide ranking
//   2. MA Stack: Price >= EMA10 >= SMA20 >= SMA50 >= SMA100 >= SMA200
//   3. ATR RS >= 50 (above-average ADR% vs universe)
//      - "High ADR is Gold, low ADR is shit" — Qullamaggie
//   4. Price-to-20-Day Range >= 50% (price in upper half of recent range)
//
// 3-STAGE FRAMEWORK:
//   Stage 1 (Big Move): 30-100%+ move in past 1-3 months
//   Stage 2 (Consolidation): Orderly pullback, higher lows, tightening range
//   Stage 3 (Breakout): Range expansion out of consolidation with volume
//
// ATR EXTENSION ZONES (vs SMA50):
//   0-4x ATR: Entry zone (initiate new positions)
//   5-7x ATR: Hold winners, tighten stops
//   7x+ ATR: Over-extended (bold red) — scale out 20% at each integer 7-11x
//   Negative: Below SMA50, no long setups

export const QULLAMAGGIE_CRITERIA = [
  'RS >= 97 (top 3% on 1W, 1M, 3M or 6M)',
  'Price >= EMA10 >= SMA20 >= SMA50 >= SMA100 >= SMA200',
  'ATR RS >= 50 (above average volatility)',
  'Price-to-20-Day Range >= 50%',
] as const

export const QULLAMAGGIE_DESCRIPTION =
  'Breakout scanner: finds stocks with elite relative strength, full MA alignment, above-average volatility, and price in the upper half of their 20-day range. Sorted by ATR Extension to SMA50.'

// ── Minervini Trend Template ─────────────────────────────────────────────────
// Source: Mark Minervini "Trade Like a Stock Market Wizard" (8-criteria template)
//
// CRITERIA (all must pass):
//   1. Price > SMA150 AND Price > SMA200
//   2. SMA150 > SMA200
//   3. SMA200 trending up for at least 1 month (22 trading days)
//   4. SMA50 > SMA150 AND SMA50 > SMA200
//   5. Price > SMA50
//   6. Price >= 30% above 52-week low
//   7. Price within 25% of 52-week high
//   8. RS Rank >= 70 (IBD-style weighted: 40% 3mo + 20% 6mo + 20% 9mo + 20% 12mo)
//   + Green candle (Close >= Open, Close >= prev Close)
//   + Market Cap >= $1B

export const MINERVINI_CRITERIA = [
  'Price > 150-day & 200-day SMA',
  'SMA150 > SMA200',
  'SMA200 rising for 1+ month',
  'SMA50 > SMA150 & SMA200',
  'Price > SMA50',
  '30%+ above 52-week low',
  'Within 25% of 52-week high',
  'RS Rank >= 70',
  'Green candle (up day)',
  'Market cap >= $1B',
] as const

export const MINERVINI_DESCRIPTION =
  'Trend template: identifies Stage 2 uptrends with rising moving averages, strong relative strength, and proximity to new highs. Green candle filter ensures momentum.'

// ── O'Neil / CANSLIM Screener ────────────────────────────────────────────────
// Source: William O'Neil "How to Make Money in Stocks" + SteveDJacobs criteria
//
// CRITERIA:
//   1. Positive trailing 12-month EPS
//   2. Forecast earnings growth 25%+ (this year or next, preferably both)
//   3. Positive ROE
//   4. Positive profit margin
//   5. ROE + Net Operating Profit Margin >= 25%
//
// CANSLIM FRAMEWORK:
//   C = Current quarterly EPS up 25%+ YoY
//   A = Annual earnings growth 25%+ over 3-5 years
//   N = New products/management/price highs
//   S = Supply & demand (volume patterns, float)
//   L = Leader (RS rank 80+)
//   I = Institutional sponsorship (increasing)
//   M = Market direction (follow general trend)

export const ONEIL_CRITERIA = [
  'Positive TTM EPS',
  'Forecast earnings growth 25%+',
  'Positive ROE',
  'Positive profit margin',
  'ROE + NOPM >= 25%',
] as const

export const ONEIL_DESCRIPTION =
  'Fundamental growth screen: finds profitable companies with strong earnings growth forecasts and efficient capital allocation (ROE + margins).'

// ── Weinstein Stage Analysis ─────────────────────────────────────────────────
// Source: Stan Weinstein "Secrets for Profiting in Bull and Bear Markets"
//
// Uses SMA150 (30-week MA) as the anchor:
//   Stage 1 (Base): Price near flat SMA150, low volume, range narrows
//   Stage 2 (Advance): Price above rising SMA150, volume increases
//   Stage 3 (Top): Price near flat/declining SMA150 from above, churning volume
//   Stage 4 (Decline): Price below declining SMA150, volume on down days

export const STAGE_DESCRIPTIONS = {
  1: 'Base — Price near flat 30-week MA, volume drying up, range narrowing',
  2: 'Advance — Price above rising 30-week MA, healthy volume, RS improving',
  3: 'Top — Price flattening near 30-week MA, distribution volume',
  4: 'Decline — Price below declining 30-week MA, selling pressure',
} as const

// ── ATR Matrix ───────────────────────────────────────────────────────────────
// Source: @jfsrevg, @RealSimpleAriel, Qullamaggie streams
//
// Measures price distance from SMA50 in ATR(14) units.
// Entry zone: 0-4x ATR from SMA50
// Winning: 5-7x ATR
// Over-extended: 7x+ ATR (scale out)
// Below SMA50: No long setups

export const ATR_ZONES = [
  { min: 7, label: 'Over-extended', action: 'Scale out 20% at each integer 7-11x', color: 'danger' },
  { min: 5, label: 'Extended', action: 'Hold winners, tighten stops', color: 'warning' },
  { min: 4, label: 'Holding', action: 'Hold, no new entries', color: 'neutral' },
  { min: 0, label: 'Entry zone', action: 'New positions valid (0-4x ATR)', color: 'success' },
  { min: -Infinity, label: 'Below SMA50', action: 'No long setups', color: 'danger' },
] as const

// ── 97 Club ──────────────────────────────────────────────────────────────────
// Top 3% of $1B+ stocks on ALL THREE RS timeframes (Day, Week, Month)
// RS = excess return vs SPY, ranked as percentile across universe
// TML (True Market Leader) = in 97 Club for 3+ consecutive weeks

export const CLUB97_DESCRIPTION =
  'The most exclusive club: $1B+ stocks in the top 3% on daily, weekly, AND monthly relative strength simultaneously.'

// ── Stockbee Scans (Pradeep Bonde) ───────────────────────────────────────────
export const STOCKBEE_DESCRIPTIONS = {
  '9m_movers': 'Volume > 50-day average AND > 9M shares traded. Captures institutional-size moves.',
  'weekly_20pct': 'Up or down 20%+ in 5 trading sessions. Major momentum events.',
  'daily_4pct': 'Up 4%+ today. Momentum burst — potential start of a bigger move.',
} as const

// ── Levers & Switches Concept ────────────────────────────────────────────────
// Switches: Binary true/false conditions
//   - Price above/below key MAs (SMA20/50/200)
//   - Green/red candle
//   - Volume record (highest in 1Y or ever)
//   - SMA20 >= SMA50
//
// Levers: Adjustable thresholds
//   - Sales/EPS growth rates
//   - Profit margin threshold
//   - ROE threshold
//   - ATR% range
//   - RS rank threshold
//
// Core "non-negotiables" must be met regardless of market conditions.
// If nothing passes non-negotiables in a bear market → DON'T TRADE.
