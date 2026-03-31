import type {
  CorrelationMatrix,
  DailyPnL,
  ExposureBreakdown,
  Position,
  PortfolioAnalytics,
  RiskLimits,
  SimPosition,
  Trade,
  TradeHistoryRow,
} from '@/types'
import { get } from './client'
// Import from trading directly (not from barrel) to avoid circular deps
import { fetchPositions, fetchTrades, fetchAccountSummary } from './trading'

type RiskPortfolioResponse = {
  pnl?: {
    total_pnl?: number
    win_rate?: number
  }
  daily_pnl?: Array<{
    date: string
    pnl: number
    cumulative: number
  }>
  performance?: {
    total_return?: number
    total_return_pct?: number
    sharpe_ratio?: number
    win_rate?: number
  }
  drawdown?: {
    current_pct?: number
    max_pct?: number
  }
}

type RiskSettingsResponse = {
  max_position_pct: number
  max_sector_pct: number
  max_daily_loss_pct: number
  max_drawdown_pct: number
  max_open_positions: number
}

type CorrelationApiResponse = CorrelationMatrix & { error?: string }

const ANALYTICS_BASELINE_EQUITY = 100_000

function toChartTime(date: string): number {
  const parsed = Date.parse(`${date}T00:00:00Z`)
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : 0
}

function buildEquityCurve(
  rows: Array<{ date: string; cumulative: number }> | undefined,
): PortfolioAnalytics['equity_curve'] {
  return (rows ?? []).map((row) => ({
    time: toChartTime(row.date),
    value: ANALYTICS_BASELINE_EQUITY + Number(row.cumulative ?? 0),
  }))
}

function mapPortfolioAnalytics(payload: RiskPortfolioResponse): PortfolioAnalytics {
  const daily = payload.daily_pnl ?? []
  const equityCurve = buildEquityCurve(daily)
  const latest = daily[daily.length - 1]
  const previousValue = equityCurve.length >= 2
    ? equityCurve[equityCurve.length - 2].value
    : ANALYTICS_BASELINE_EQUITY
  const dayPnl = Number(latest?.pnl ?? 0)
  const totalPnl = Number(payload.pnl?.total_pnl ?? payload.performance?.total_return ?? 0)

  return {
    total_value: equityCurve.length > 0
      ? equityCurve[equityCurve.length - 1].value
      : ANALYTICS_BASELINE_EQUITY + totalPnl,
    day_pnl: dayPnl,
    day_pnl_pct: previousValue > 0 ? (dayPnl / previousValue) * 100 : 0,
    total_pnl: totalPnl,
    total_pnl_pct: Number(payload.performance?.total_return_pct ?? 0),
    win_rate: Number(payload.performance?.win_rate ?? payload.pnl?.win_rate ?? 0),
    sharpe_ratio: Number(payload.performance?.sharpe_ratio ?? 0),
    max_drawdown_pct: -Math.abs(Number(payload.drawdown?.max_pct ?? 0)),
    equity_curve: equityCurve,
    benchmark_curve: [],
  }
}

function mapDailyPnL(rows: Array<{ date: string; pnl: number }> | undefined): DailyPnL[] {
  return (rows ?? []).map((row) => ({
    date: row.date,
    pnl: Number(row.pnl ?? 0),
    trades: 0,
  }))
}

function normalizePositionExposure(position: Position | SimPosition) {
  const value = Math.abs(Number(position.market_value ?? 0))
  const pnl = Number(position.unrealized_pnl ?? 0) + Number('realized_pnl' in position ? position.realized_pnl ?? 0 : 0)
  const qty = Math.abs(Number(position.qty ?? 0))
  const avgCost = Number(position.avg_cost ?? 0)
  const costBasis = qty * avgCost
  const pnlPct = 'pnl_pct' in position && typeof position.pnl_pct === 'number'
    ? position.pnl_pct
    : costBasis > 0 ? (pnl / costBasis) * 100 : 0

  return {
    symbol: position.symbol,
    sector: 'Unknown',
    value,
    weight_pct: 0,
    pnl,
    pnl_pct: pnlPct,
  }
}

function toTradeHistoryRow(trade: Trade, index: number): TradeHistoryRow {
  const rawMetadata = (trade.metadata ?? {}) as Record<string, unknown>
  const rawRealized = typeof trade.realized_pnl === 'number'
    ? trade.realized_pnl
    : typeof rawMetadata.realized_pnl === 'number'
      ? rawMetadata.realized_pnl
      : typeof rawMetadata.pnl === 'number'
        ? rawMetadata.pnl
        : undefined

  const openedAt = typeof trade.opened_at === 'string' ? trade.opened_at : undefined
  const closedAt = typeof trade.closed_at === 'string' ? trade.closed_at : undefined
  const holdingDays = openedAt && closedAt
    ? Math.max(0, Math.round((Date.parse(closedAt) - Date.parse(openedAt)) / 86400000))
    : undefined

  return {
    id: trade.id || `${trade.symbol}-${trade.timestamp}-${index}`,
    symbol: trade.symbol,
    action: trade.action,
    quantity: trade.quantity,
    fill_price: Number(trade.fill_price ?? trade.exit_price ?? trade.entry_price ?? 0),
    pnl: rawRealized,
    timestamp: closedAt ?? trade.timestamp,
    holding_days: holdingDays,
  }
}

export const fetchPortfolioAnalytics = async (
  range: '1W' | '1M' | '3M' | '6M' | '1Y' | 'ALL' = '3M',
) => {
  const payload = await get<RiskPortfolioResponse>(`/api/risk/portfolio?range=${range}`)
  return mapPortfolioAnalytics(payload)
}

export const fetchDailyPnL = async (days = 90) => {
  const rows = await get<Array<{ date: string; pnl: number; cumulative?: number }>>(`/api/analytics/pnl/daily?days=${days}`)
  return mapDailyPnL(rows)
}

export const fetchExposureBreakdown = async () => {
  const positions = await fetchPositions()
  const normalized = positions.map(normalizePositionExposure)
  const totalValue = normalized.reduce((sum, position) => sum + position.value, 0)
  const sectorWeights: Record<string, number> = {}

  return {
    positions: normalized.map((position) => ({
      ...position,
      weight_pct: totalValue > 0 ? (position.value / totalValue) * 100 : 0,
    })),
    sector_weights: sectorWeights,
  } satisfies ExposureBreakdown
}

export const fetchRiskLimits = async () => {
  const [settings, positions, account, dailyPnl, drawdown] = await Promise.all([
    get<RiskSettingsResponse>('/api/risk/settings'),
    fetchPositions(),
    fetchAccountSummary(),
    get<Array<{ date: string; pnl: number }>>('/api/analytics/pnl/daily?days=1'),
    get<{ current_pct?: number }>('/api/risk/drawdown'),
  ])

  const accountValue = 'net_liquidation' in account
    ? Number(account.net_liquidation ?? 0)
    : Number(account.balance ?? 0)
  const largestPositionValue = positions.reduce(
    (largest, position) => Math.max(largest, Math.abs(Number(position.market_value ?? 0))),
    0,
  )
  const latestDailyPnl = Number(dailyPnl[dailyPnl.length - 1]?.pnl ?? 0)
  const dailyLossLimit = accountValue > 0
    ? (accountValue * Number(settings.max_daily_loss_pct ?? 0)) / 100
    : 0
  const currentDrawdown = Math.abs(Number(drawdown.current_pct ?? 0))

  return {
    limits: [
      {
        label: 'Max Position Size',
        used: accountValue > 0 ? (largestPositionValue / accountValue) * 100 : 0,
        limit: Number(settings.max_position_pct ?? 0),
        unit: '%',
      },
      {
        label: 'Daily Loss Limit',
        used: latestDailyPnl < 0 ? Math.abs(latestDailyPnl) : 0,
        limit: dailyLossLimit,
        unit: '$',
      },
      {
        label: 'Max Drawdown',
        used: currentDrawdown,
        limit: Number(settings.max_drawdown_pct ?? 0),
        unit: '%',
      },
      {
        label: 'Open Positions',
        used: positions.length,
        limit: Number(settings.max_open_positions ?? 0),
        unit: 'count',
      },
    ],
    max_position_size_pct: Number(settings.max_position_pct ?? 0),
    daily_loss_limit: dailyLossLimit,
    drawdown_limit_pct: Number(settings.max_drawdown_pct ?? 0),
    max_open_positions: Number(settings.max_open_positions ?? 0),
  } satisfies RiskLimits
}

export const fetchTradeHistory = async (limit = 20) => {
  const trades = await fetchTrades(limit)
  return trades.map(toTradeHistoryRow)
}

export const fetchCorrelationMatrix = async () => {
  const positions = await fetchPositions()
  const symbols = Array.from(
    new Set(
      positions
        .map((position) => position.symbol?.trim().toUpperCase())
        .filter((symbol): symbol is string => Boolean(symbol)),
    ),
  )

  if (symbols.length < 3) {
    return { symbols, matrix: [] } satisfies CorrelationMatrix
  }

  const payload = await get<CorrelationApiResponse>(`/api/risk/correlation?symbols=${encodeURIComponent(symbols.join(','))}`)
  if (payload.error) {
    throw new Error(payload.error)
  }
  if (!Array.isArray(payload.symbols) || !Array.isArray(payload.matrix)) {
    throw new Error('Invalid correlation payload')
  }

  const expectedSize = payload.symbols.length
  const hasValidMatrix =
    expectedSize >= 3 &&
    payload.matrix.length === expectedSize &&
    payload.matrix.every(
      (row) => Array.isArray(row) && row.length === expectedSize && row.every((value) => typeof value === 'number' && Number.isFinite(value)),
    )

  if (!hasValidMatrix) {
    throw new Error('Invalid correlation payload')
  }

  return payload satisfies CorrelationMatrix
}
