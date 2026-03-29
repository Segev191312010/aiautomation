import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('@/store', () => ({
  useAccountStore: () => ({ account: null }),
  useBotStore: () => ({ simMode: false }),
  useSimStore: () => ({ simAccount: null }),
}))

vi.mock('@/components/tradebot/TradeBotTabs', () => ({
  default: ({
    tabs,
    activeTab,
    onTabChange,
  }: {
    tabs: Array<{ id: string; label: string }>
    activeTab: string
    onTabChange: (tabId: string) => void
  }) => (
    <div data-testid="tradebot-tabs">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          aria-pressed={tab.id === activeTab}
          onClick={() => onTabChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  ),
}))

const chartApi = {
  addLineSeries: vi.fn(() => ({ setData: vi.fn() })),
  addHistogramSeries: vi.fn(() => ({ setData: vi.fn() })),
  applyOptions: vi.fn(),
  remove: vi.fn(),
  timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
}

vi.mock('lightweight-charts', () => ({
  createChart: vi.fn(() => chartApi),
}))

vi.mock('@/services/api', () => ({
  fetchPortfolioAnalytics: vi.fn(),
  fetchDailyPnL: vi.fn(),
  fetchExposureBreakdown: vi.fn(),
  fetchRiskLimits: vi.fn(),
  fetchTradeHistory: vi.fn(),
  fetchCorrelationMatrix: vi.fn(),
}))

import AnalyticsPage from '../AnalyticsPage'
import * as api from '@/services/api'

const portfolioAnalytics = {
  total_value: 123456,
  day_pnl: 1234,
  day_pnl_pct: 1.01,
  total_pnl: 4567,
  total_pnl_pct: 3.7,
  win_rate: 58.3,
  sharpe_ratio: 1.42,
  max_drawdown_pct: -8.7,
  equity_curve: [
    { time: 1711497600, value: 122222 },
    { time: 1711584000, value: 123456 },
  ],
  benchmark_curve: [
    { time: 1711497600, value: 100000 },
    { time: 1711584000, value: 100500 },
  ],
}

const dailyPnl = [
  { date: '2026-03-25', pnl: 100, trades: 2 },
  { date: '2026-03-26', pnl: -50, trades: 1 },
]

const exposure = {
  positions: [
    { symbol: 'NVDA', sector: 'Technology', value: 24500, weight_pct: 24.5, pnl: 3200, pnl_pct: 15.1 },
  ],
  sector_weights: {
    Technology: 24.5,
  },
}

const riskLimits = {
  max_position_size_pct: 25,
  daily_loss_limit: 2000,
  drawdown_limit_pct: 15,
  max_open_positions: 10,
  limits: [
    { label: 'Max Position Size', used: 24.5, limit: 25, unit: '%' },
  ],
}

const tradeHistory = [
  {
    id: 't1',
    symbol: 'NVDA',
    action: 'SELL',
    quantity: 10,
    fill_price: 890.4,
    pnl: 3200,
    timestamp: '2026-03-26T10:00:00Z',
    holding_days: 14,
  },
]

const correlation = {
  symbols: ['NVDA', 'AAPL', 'MSFT'],
  matrix: [
    [1, 0.7, 0.6],
    [0.7, 1, 0.65],
    [0.6, 0.65, 1],
  ],
}

class ResizeObserverMock {
  observe() {}
  disconnect() {}
  unobserve() {}
}

describe('AnalyticsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.defineProperty(globalThis, 'ResizeObserver', {
      configurable: true,
      writable: true,
      value: ResizeObserverMock,
    })

    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.mocked(api.fetchPortfolioAnalytics).mockResolvedValue(portfolioAnalytics as never)
    vi.mocked(api.fetchDailyPnL).mockResolvedValue(dailyPnl as never)
    vi.mocked(api.fetchExposureBreakdown).mockResolvedValue(exposure as never)
    vi.mocked(api.fetchRiskLimits).mockResolvedValue(riskLimits as never)
    vi.mocked(api.fetchTradeHistory).mockResolvedValue(tradeHistory as never)
    vi.mocked(api.fetchCorrelationMatrix).mockResolvedValue(correlation as never)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    delete (globalThis as { ResizeObserver?: typeof ResizeObserver }).ResizeObserver
  })

  it('renders live analytics metrics when API calls succeed', async () => {
    render(<AnalyticsPage />)

    expect(await screen.findByText('$123.5K')).toBeInTheDocument()
    expect(screen.getByText('+$1,234.00')).toBeInTheDocument()
    expect(screen.getByText('58.3%')).toBeInTheDocument()
    expect(screen.queryByText(/Analytics data partially unavailable/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Portfolio KPIs unavailable/i)).not.toBeInTheDocument()
  })

  it('renders degraded cards instead of fake numbers when requests fail', async () => {
    vi.mocked(api.fetchPortfolioAnalytics).mockRejectedValue(new Error('portfolio failed'))
    vi.mocked(api.fetchDailyPnL).mockRejectedValue(new Error('daily failed'))

    render(<AnalyticsPage />)

    expect(await screen.findByText(/Analytics data partially unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/Portfolio KPIs unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/Daily P&L unavailable/i)).toBeInTheDocument()
    expect(screen.queryByText('$123.5K')).not.toBeInTheDocument()

    await waitFor(() => {
      expect(console.warn).toHaveBeenCalledWith('[AnalyticsPage] portfolio fetch failed', expect.any(Error))
      expect(console.warn).toHaveBeenCalledWith('[AnalyticsPage] daily_pnl fetch failed', expect.any(Error))
    })
  })

  it('renders degraded states on the non-default tabs when those sections fail', async () => {
    vi.mocked(api.fetchExposureBreakdown).mockRejectedValue(new Error('exposure failed'))
    vi.mocked(api.fetchRiskLimits).mockRejectedValue(new Error('risk failed'))
    vi.mocked(api.fetchTradeHistory).mockRejectedValue(new Error('history failed'))
    vi.mocked(api.fetchCorrelationMatrix).mockRejectedValue(new Error('correlation failed'))

    render(<AnalyticsPage />)
    expect(await screen.findByText(/Analytics data partially unavailable/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Positions' }))
    expect(await screen.findByText(/Position exposure unavailable/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Risk' }))
    expect(await screen.findByText(/Risk limits unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/Correlation matrix unavailable/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    expect(await screen.findByText(/Trade history unavailable/i)).toBeInTheDocument()
  })

  it('treats an invalid fulfilled correlation payload as unavailable', async () => {
    vi.mocked(api.fetchCorrelationMatrix).mockResolvedValue({
      symbols: ['NVDA', 'AAPL', 'MSFT'],
      matrix: [],
    } as never)

    render(<AnalyticsPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Risk' }))

    expect(await screen.findByText(/Correlation matrix unavailable/i)).toBeInTheDocument()
    await waitFor(() => {
      expect(console.warn).toHaveBeenCalledWith(
        '[AnalyticsPage] correlation fetch failed',
        expect.objectContaining({ message: 'Invalid correlation payload' }),
      )
    })
  })
})

