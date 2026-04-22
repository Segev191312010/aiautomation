import type {
  SwingDashboard,
  BreadthMetrics,
  GuruScreenerResult,
  ATRMatrixRow,
  Club97Entry,
  StockbeeMover,
  IndustryGroup,
  StageDistribution,
  TrendGradeDistribution,
  GuruScreenerName,
  StockbeeScanName,
} from '@/types'

// ── Breadth ──────────────────────────────────────────────────────────────────

export function getMockBreadth(): BreadthMetrics {
  return {
    timestamp: new Date().toISOString(),
    rows: [
      { label: 'Stocks Up (Day)',         nasdaq100: 62, sp500: 285, composite: 312, billion_plus: 1420 },
      { label: 'Stocks Down (Day)',       nasdaq100: 38, sp500: 215, composite: 204, billion_plus: 1066 },
      { label: 'Up/Down Ratio (Day)',     nasdaq100: 1.63, sp500: 1.33, composite: 1.53, billion_plus: 1.33 },
      { label: 'Stocks Up (Week)',        nasdaq100: 55, sp500: 260, composite: 280, billion_plus: 1290 },
      { label: 'Stocks Down (Week)',      nasdaq100: 45, sp500: 240, composite: 236, billion_plus: 1196 },
      { label: 'Up/Down Ratio (Week)',    nasdaq100: 1.22, sp500: 1.08, composite: 1.19, billion_plus: 1.08 },
      { label: 'Stocks Up (Month)',       nasdaq100: 48, sp500: 230, composite: 248, billion_plus: 1140 },
      { label: 'Stocks Down (Month)',     nasdaq100: 52, sp500: 270, composite: 268, billion_plus: 1346 },
      { label: 'Up/Down Ratio (Month)',   nasdaq100: 0.92, sp500: 0.85, composite: 0.93, billion_plus: 0.85 },
      { label: '% Above SMA 20',         nasdaq100: 58.0, sp500: 52.4, composite: 54.1, billion_plus: 49.8 },
      { label: '% Above SMA 50',         nasdaq100: 51.0, sp500: 47.2, composite: 48.6, billion_plus: 44.1 },
      { label: '% Above SMA 200',        nasdaq100: 62.0, sp500: 58.6, composite: 59.3, billion_plus: 55.2 },
      { label: 'New 20-Day Highs',       nasdaq100: 12, sp500: 48, composite: 55, billion_plus: 185 },
      { label: 'New 20-Day Lows',        nasdaq100: 8, sp500: 35, composite: 40, billion_plus: 142 },
    ],
  }
}

// ── Guru Screener Results ────────────────────────────────────────────────────

const QULLAMAGGIE_RESULTS: GuruScreenerResult[] = [
  { symbol: 'PLTR', price: 98.42, change_pct: 3.21, volume: 48200000, rs_rank: 99, vcs: 82, setup_notes: ['RS 99 (1M)', 'MA stack aligned', '3.2x ATR ext'] },
  { symbol: 'APP',  price: 412.30, change_pct: 2.15, volume: 5100000, rs_rank: 98, vcs: 78, setup_notes: ['RS 98 (3M)', 'MA stack aligned', '2.8x ATR ext'] },
  { symbol: 'AXON', price: 645.10, change_pct: 1.88, volume: 1200000, rs_rank: 98, vcs: 75, setup_notes: ['RS 98 (1M)', 'ATR RS 72', '1.9x ATR ext'] },
  { symbol: 'CRWD', price: 388.50, change_pct: 1.45, volume: 3800000, rs_rank: 97, vcs: 71, setup_notes: ['RS 97 (6M)', 'ADR% 4.1', '2.1x ATR ext'] },
  { symbol: 'TTD',  price: 112.80, change_pct: 2.67, volume: 6200000, rs_rank: 97, vcs: 68, setup_notes: ['RS 97 (1W)', 'ATR RS 65', '1.5x ATR ext'] },
  { symbol: 'ANET', price: 98.75, change_pct: 1.12, volume: 4500000, rs_rank: 97, vcs: 72, setup_notes: ['RS 97 (3M)', 'MA stack aligned', '0.8x ATR ext'] },
  { symbol: 'COIN', price: 265.40, change_pct: 4.55, volume: 12300000, rs_rank: 98, vcs: 65, setup_notes: ['RS 98 (1W)', 'ADR% 5.8', '4.2x ATR ext'] },
]

const MINERVINI_RESULTS: GuruScreenerResult[] = [
  { symbol: 'NVDA', price: 142.50, change_pct: 2.80, volume: 62000000, rs_rank: 97, setup_notes: ['8/8 template', '85% above 52W low', 'SMA200 rising 5mo'] },
  { symbol: 'META', price: 595.20, change_pct: 1.55, volume: 18500000, rs_rank: 95, setup_notes: ['8/8 template', '62% above 52W low', 'Within 5% of 52W high'] },
  { symbol: 'AVGO', price: 198.60, change_pct: 1.22, volume: 9800000, rs_rank: 94, setup_notes: ['8/8 template', 'SMA50>150>200', 'Green candle'] },
  { symbol: 'LLY',  price: 872.30, change_pct: 0.95, volume: 3200000, rs_rank: 92, setup_notes: ['8/8 template', '45% above 52W low', 'SMA200 rising 4mo'] },
  { symbol: 'COST', price: 945.80, change_pct: 0.78, volume: 2100000, rs_rank: 90, setup_notes: ['8/8 template', 'Within 3% of 52W high', 'Steady trend'] },
  { symbol: 'GE',   price: 205.40, change_pct: 1.35, volume: 7600000, rs_rank: 88, setup_notes: ['8/8 template', 'SMA200 rising 6mo', '55% above 52W low'] },
  { symbol: 'UBER', price: 82.90, change_pct: 2.10, volume: 15200000, rs_rank: 86, setup_notes: ['8/8 template', '38% above 52W low', 'Green candle'] },
  { symbol: 'NOW',  price: 935.60, change_pct: 0.88, volume: 1800000, rs_rank: 85, setup_notes: ['8/8 template', 'SMA50>150>200 aligned', 'Within 8% of 52W high'] },
]

const ONEIL_RESULTS: GuruScreenerResult[] = [
  { symbol: 'NVDA', price: 142.50, change_pct: 2.80, volume: 62000000, rs_rank: 99, setup_notes: ['EPS +42% TTM', 'ROE 115%', 'NOPM 56%'] },
  { symbol: 'PLTR', price: 98.42, change_pct: 3.21, volume: 48200000, rs_rank: 97, setup_notes: ['EPS +68% TTM', 'ROE+NOPM 48%', 'Growth 35%'] },
  { symbol: 'APP',  price: 412.30, change_pct: 2.15, volume: 5100000, rs_rank: 95, setup_notes: ['EPS +85% TTM', 'Growth 45%', 'ROE 95%'] },
  { symbol: 'CRWD', price: 388.50, change_pct: 1.45, volume: 3800000, rs_rank: 93, setup_notes: ['EPS +52% TTM', 'ROE+NOPM 38%', 'Margin 22%'] },
  { symbol: 'PANW', price: 198.20, change_pct: 1.78, volume: 4100000, rs_rank: 91, setup_notes: ['EPS +28% TTM', 'Growth 28%', 'ROE 42%'] },
  { symbol: 'DASH', price: 195.30, change_pct: 5.42, volume: 8500000, rs_rank: 89, setup_notes: ['EPS +120% TTM', 'ROE+NOPM 32%', 'Growth 30%'] },
]

export function getMockGuruResults(name: GuruScreenerName): GuruScreenerResult[] {
  switch (name) {
    case 'qullamaggie': return QULLAMAGGIE_RESULTS
    case 'minervini':   return MINERVINI_RESULTS
    case 'oneil':       return ONEIL_RESULTS
  }
}

// ── ATR Matrix ───────────────────────────────────────────────────────────────

export function getMockATRMatrix(): ATRMatrixRow[] {
  return [
    { symbol: 'XLK',  name: 'Technology',           close: 228.50, atr_pct: 1.42, price_vs_21ema_atr: 1.85,  atr_14: 3.24 },
    { symbol: 'XLY',  name: 'Cons. Discretionary',  close: 198.30, atr_pct: 1.65, price_vs_21ema_atr: 1.42,  atr_14: 3.27 },
    { symbol: 'XLC',  name: 'Communication',        close: 92.40,  atr_pct: 1.38, price_vs_21ema_atr: 1.12,  atr_14: 1.28 },
    { symbol: 'XLI',  name: 'Industrials',          close: 132.80, atr_pct: 1.18, price_vs_21ema_atr: 0.75,  atr_14: 1.57 },
    { symbol: 'QQQE', name: 'Nasdaq-100 EW',        close: 95.20,  atr_pct: 1.55, price_vs_21ema_atr: 0.68,  atr_14: 1.48 },
    { symbol: 'XLF',  name: 'Financials',           close: 45.60,  atr_pct: 1.05, price_vs_21ema_atr: 0.45,  atr_14: 0.48 },
    { symbol: 'RSP',  name: 'S&P 500 EW',           close: 168.90, atr_pct: 1.12, price_vs_21ema_atr: 0.32,  atr_14: 1.89 },
    { symbol: 'XLB',  name: 'Materials',            close: 88.20,  atr_pct: 1.25, price_vs_21ema_atr: -0.15, atr_14: 1.10 },
    { symbol: 'XLP',  name: 'Cons. Staples',        close: 80.10,  atr_pct: 0.85, price_vs_21ema_atr: -0.42, atr_14: 0.68 },
    { symbol: 'XLRE', name: 'Real Estate',          close: 41.30,  atr_pct: 1.32, price_vs_21ema_atr: -0.65, atr_14: 0.55 },
    { symbol: 'XLV',  name: 'Healthcare',           close: 148.70, atr_pct: 0.92, price_vs_21ema_atr: -0.88, atr_14: 1.37 },
    { symbol: 'XLE',  name: 'Energy',               close: 85.40,  atr_pct: 1.78, price_vs_21ema_atr: -1.22, atr_14: 1.52 },
    { symbol: 'XLU',  name: 'Utilities',            close: 72.90,  atr_pct: 0.95, price_vs_21ema_atr: -1.55, atr_14: 0.69 },
  ]
}

// ── 97 Club ──────────────────────────────────────────────────────────────────

export function getMockClub97(): Club97Entry[] {
  return [
    { symbol: 'NVDA',  price: 142.50, rs_day_pctile: 98.5, rs_week_pctile: 99.1, rs_month_pctile: 98.8, is_tml: true },
    { symbol: 'PLTR',  price: 98.42,  rs_day_pctile: 98.2, rs_week_pctile: 97.8, rs_month_pctile: 99.2, is_tml: true },
    { symbol: 'APP',   price: 412.30, rs_day_pctile: 97.8, rs_week_pctile: 98.5, rs_month_pctile: 97.5, is_tml: false },
    { symbol: 'AXON',  price: 645.10, rs_day_pctile: 97.5, rs_week_pctile: 97.2, rs_month_pctile: 98.1, is_tml: false },
    { symbol: 'VST',   price: 142.80, rs_day_pctile: 98.8, rs_week_pctile: 97.1, rs_month_pctile: 97.4, is_tml: false },
    { symbol: 'COIN',  price: 265.40, rs_day_pctile: 97.2, rs_week_pctile: 98.0, rs_month_pctile: 97.3, is_tml: false },
    { symbol: 'CRWD',  price: 388.50, rs_day_pctile: 97.1, rs_week_pctile: 97.5, rs_month_pctile: 97.8, is_tml: false },
    { symbol: 'TTD',   price: 112.80, rs_day_pctile: 97.9, rs_week_pctile: 97.3, rs_month_pctile: 97.1, is_tml: false },
    { symbol: 'ANET',  price: 98.75,  rs_day_pctile: 97.0, rs_week_pctile: 97.8, rs_month_pctile: 97.6, is_tml: false },
  ]
}

// ── Stockbee Scans ───────────────────────────────────────────────────────────

const STOCKBEE_9M: StockbeeMover[] = [
  { symbol: 'NVDA', price: 142.50, change_pct: 2.80, volume: 62000000, avg_volume: 45000000 },
  { symbol: 'PLTR', price: 98.42,  change_pct: 3.21, volume: 48200000, avg_volume: 38000000 },
  { symbol: 'TSLA', price: 285.60, change_pct: -1.25, volume: 52000000, avg_volume: 42000000 },
  { symbol: 'AAPL', price: 218.30, change_pct: 0.45, volume: 38500000, avg_volume: 35000000 },
  { symbol: 'AMD',  price: 168.20, change_pct: 1.88, volume: 32100000, avg_volume: 28000000 },
  { symbol: 'AMZN', price: 198.40, change_pct: 1.12, volume: 28500000, avg_volume: 22000000 },
  { symbol: 'COIN', price: 265.40, change_pct: 4.55, volume: 12300000, avg_volume: 8500000 },
]

const STOCKBEE_WEEKLY_20: StockbeeMover[] = [
  { symbol: 'SMCI', price: 45.80,  change_pct: 28.5, volume: 18200000, avg_volume: 12000000 },
  { symbol: 'MSTR', price: 398.20, change_pct: 24.2, volume: 9800000,  avg_volume: 6500000 },
  { symbol: 'IONQ', price: 42.50,  change_pct: 22.1, volume: 15600000, avg_volume: 8200000 },
  { symbol: 'RIOT', price: 14.80,  change_pct: 21.8, volume: 28500000, avg_volume: 18000000 },
]

const STOCKBEE_DAILY_4: StockbeeMover[] = [
  { symbol: 'COIN', price: 265.40, change_pct: 4.55, volume: 12300000, avg_volume: 8500000 },
  { symbol: 'DASH', price: 195.30, change_pct: 5.42, volume: 8500000,  avg_volume: 4200000 },
  { symbol: 'SNAP', price: 14.20,  change_pct: 6.80, volume: 22100000, avg_volume: 15000000 },
  { symbol: 'PLTR', price: 98.42,  change_pct: 3.21, volume: 48200000, avg_volume: 38000000 },  // borderline but included for mock
  { symbol: 'SMCI', price: 45.80,  change_pct: 8.20, volume: 18200000, avg_volume: 12000000 },
  { symbol: 'MARA', price: 22.40,  change_pct: 5.10, volume: 16800000, avg_volume: 10500000 },
]

export function getMockStockbeeMovers(scan: StockbeeScanName): StockbeeMover[] {
  switch (scan) {
    case '9m_movers':    return STOCKBEE_9M
    case 'weekly_20pct': return STOCKBEE_WEEKLY_20
    case 'daily_4pct':   return STOCKBEE_DAILY_4
  }
}

// ── Leading Industries ───────────────────────────────────────────────────────

export function getMockIndustries(): IndustryGroup[] {
  return [
    { industry: 'Semiconductors',          stock_count: 28, avg_weekly_return: 4.2,  avg_monthly_return: 12.8, rs_vs_spy: 2.85, top_stocks: ['NVDA', 'AVGO', 'AMD', 'MRVL'] },
    { industry: 'Software - Infrastructure', stock_count: 35, avg_weekly_return: 3.1,  avg_monthly_return: 9.5,  rs_vs_spy: 2.12, top_stocks: ['PLTR', 'NOW', 'CRWD', 'PANW'] },
    { industry: 'Internet Content',        stock_count: 18, avg_weekly_return: 2.8,  avg_monthly_return: 8.2,  rs_vs_spy: 1.95, top_stocks: ['META', 'GOOG', 'SNAP', 'PINS'] },
    { industry: 'Aerospace & Defense',     stock_count: 22, avg_weekly_return: 2.5,  avg_monthly_return: 7.8,  rs_vs_spy: 1.82, top_stocks: ['GE', 'RTX', 'LMT', 'AXON'] },
    { industry: 'Auto Manufacturers',      stock_count: 8,  avg_weekly_return: 2.4,  avg_monthly_return: 6.5,  rs_vs_spy: 1.65, top_stocks: ['TSLA', 'GM', 'F', 'RIVN'] },
    { industry: 'Financial Data',          stock_count: 12, avg_weekly_return: 2.2,  avg_monthly_return: 7.1,  rs_vs_spy: 1.58, top_stocks: ['COIN', 'ICE', 'CME', 'NDAQ'] },
    { industry: 'Drug Manufacturers',      stock_count: 25, avg_weekly_return: 1.8,  avg_monthly_return: 6.8,  rs_vs_spy: 1.45, top_stocks: ['LLY', 'NVO', 'ABBV', 'MRK'] },
    { industry: 'Cloud Computing',         stock_count: 15, avg_weekly_return: 2.1,  avg_monthly_return: 5.9,  rs_vs_spy: 1.42, top_stocks: ['AMZN', 'MSFT', 'GOOG', 'CRM'] },
    { industry: 'Specialty Retail',        stock_count: 20, avg_weekly_return: 1.9,  avg_monthly_return: 5.5,  rs_vs_spy: 1.35, top_stocks: ['COST', 'TJX', 'HD', 'ORLY'] },
    { industry: 'Banks - Diversified',     stock_count: 18, avg_weekly_return: 1.5,  avg_monthly_return: 4.8,  rs_vs_spy: 1.22, top_stocks: ['JPM', 'BAC', 'WFC', 'GS'] },
    { industry: 'Insurance',              stock_count: 22, avg_weekly_return: 1.4,  avg_monthly_return: 4.5,  rs_vs_spy: 1.18, top_stocks: ['BRK.B', 'PGR', 'CB', 'MET'] },
    { industry: 'Medical Devices',        stock_count: 16, avg_weekly_return: 1.2,  avg_monthly_return: 3.8,  rs_vs_spy: 1.10, top_stocks: ['ISRG', 'SYK', 'MDT', 'BSX'] },
  ]
}

// ── Stage Analysis ───────────────────────────────────────────────────────────

export function getMockStages(): StageDistribution {
  return {
    stage_1: 420,
    stage_2: 890,
    stage_3: 380,
    stage_4: 796,
    stage_1_symbols: ['INTC', 'BA', 'DIS', 'PFE', 'NKE', 'PYPL', 'VZ'],
    stage_2_symbols: ['NVDA', 'META', 'PLTR', 'AVGO', 'LLY', 'COST', 'GE', 'UBER', 'CRWD', 'APP'],
    stage_3_symbols: ['AAPL', 'MSFT', 'JNJ', 'PG', 'KO', 'PEP', 'WMT'],
    stage_4_symbols: ['MRNA', 'ZM', 'SNAP', 'RIVN', 'LCID', 'HOOD', 'SQ'],
  }
}

// ── Trend Grades ─────────────────────────────────────────────────────────────

export function getMockGrades(): TrendGradeDistribution {
  return {
    grades: {
      'A+': 32,  'A': 68,   'A-': 95,
      'B+': 120, 'B': 165,  'B-': 180,
      'C+': 210, 'C': 248,  'C-': 225,
      'D+': 205, 'D': 195,  'D-': 165,
      'E+': 128, 'E': 105,  'E-': 85,
      'F': 60,
    },
    top_graded: [
      { symbol: 'NVDA',  price: 142.50, change_pct: 2.80, grade: 'A+', rs_composite: 98.5 },
      { symbol: 'PLTR',  price: 98.42,  change_pct: 3.21, grade: 'A+', rs_composite: 97.8 },
      { symbol: 'APP',   price: 412.30, change_pct: 2.15, grade: 'A+', rs_composite: 96.5 },
      { symbol: 'AXON',  price: 645.10, change_pct: 1.88, grade: 'A+', rs_composite: 95.2 },
      { symbol: 'VST',   price: 142.80, change_pct: 4.12, grade: 'A+', rs_composite: 94.8 },
      { symbol: 'META',  price: 595.20, change_pct: 1.55, grade: 'A+', rs_composite: 94.1 },
      { symbol: 'AVGO',  price: 198.60, change_pct: 1.22, grade: 'A+', rs_composite: 93.5 },
      { symbol: 'CRWD',  price: 388.50, change_pct: 1.45, grade: 'A+', rs_composite: 92.8 },
      { symbol: 'TTD',   price: 112.80, change_pct: 2.67, grade: 'A+', rs_composite: 92.1 },
      { symbol: 'COIN',  price: 265.40, change_pct: 4.55, grade: 'A+', rs_composite: 91.4 },
    ],
  }
}

// ── Composite ────────────────────────────────────────────────────────────────

export function getMockSwingDashboard(): SwingDashboard {
  return {
    breadth:      getMockBreadth(),
    guru_results: {
      qullamaggie: getMockGuruResults('qullamaggie'),
      minervini:   getMockGuruResults('minervini'),
      oneil:       getMockGuruResults('oneil'),
    },
    atr_matrix:   getMockATRMatrix(),
    club97:       getMockClub97(),
    stockbee: {
      '9m_movers':    getMockStockbeeMovers('9m_movers'),
      'weekly_20pct': getMockStockbeeMovers('weekly_20pct'),
      'daily_4pct':   getMockStockbeeMovers('daily_4pct'),
    },
    industries:   getMockIndustries(),
    stages:       getMockStages(),
    grades:       getMockGrades(),
  }
}
