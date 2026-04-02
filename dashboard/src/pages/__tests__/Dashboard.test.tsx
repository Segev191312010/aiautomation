import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

// ── Store mock ────────────────────────────────────────────────────────────────
const mockSetRoute = vi.fn()
const mockToggleCompMode = vi.fn()
const mockSetCompSymbol = vi.fn()

vi.mock('@/store', () => ({
  useMarketStore: (sel: (s: object) => unknown) =>
    sel({
      selectedSymbol: 'AAPL',
      quotes: {
        AAPL: {
          price: 210.5,
          change: 3.2,
          change_pct: 1.55,
          market_cap: 3_200_000_000_000,
          volume: 54_000_000,
          live_source: 'ibkr',
          market_state: 'open',
          stale_s: 0,
        },
      },
      compMode: false,
      compSymbol: '',
      toggleCompMode: mockToggleCompMode,
      setCompSymbol: mockSetCompSymbol,
      chartType: 'candlestick',
    }),
  useAccountStore: (sel: (s: object) => unknown) =>
    sel({
      account: {
        balance: 55_000,
        cash: 20_000,
        unrealized_pnl: 1_200,
        realized_pnl: 800,
      },
    }),
  useBotStore: (sel: (s: object) => unknown) =>
    sel({
      simMode: false,
      status: { features: { market_diagnostics: false } },
    }),
  useDiagnosticsStore: (sel: (s: object) => unknown) =>
    sel({
      lookbackDays: 90,
      setLookbackDays: vi.fn(),
      loadAll: vi.fn(),
      refreshNow: vi.fn(),
      loading: false,
      error: null,
      overview: null,
      indicators: [],
      marketMap: [],
      projections: null,
      news: [],
      refreshing: false,
      refreshRun: null,
      setEnabled: vi.fn(),
      pollRefreshRun: vi.fn(),
    }),
  useUIStore: (sel: (s: object) => unknown) =>
    sel({
      setRoute: mockSetRoute,
    }),
}))

// ── Hook mock ─────────────────────────────────────────────────────────────────
vi.mock('@/hooks/useDiagnostics', () => ({ useDiagnostics: vi.fn() }))

// ── Heavy component mocks ─────────────────────────────────────────────────────
vi.mock('@/components/chart/TradingChart', () => ({
  default: ({ symbol }: { symbol: string }) => (
    <div data-testid="trading-chart">{symbol}</div>
  ),
}))

vi.mock('@/components/ticker/WatchlistGrid', () => ({
  default: () => <div data-testid="watchlist-grid" />,
}))

vi.mock('@/components/insights/OpportunityBoard', () => ({
  default: () => <div data-testid="opportunity-board" />,
}))

vi.mock('@/components/insights/diagnostics/DiagnosticHeaderRow', () => ({
  default: () => <div data-testid="diagnostic-header-row" />,
}))
vi.mock('@/components/insights/diagnostics/OverallSummaryCard', () => ({
  default: () => <div data-testid="overall-summary-card" />,
}))
vi.mock('@/components/insights/diagnostics/SystemOverviewWidget', () => ({
  default: () => <div data-testid="system-overview-widget" />,
}))
vi.mock('@/components/insights/diagnostics/DowTheoryWidget', () => ({
  default: () => <div data-testid="dow-theory-widget" />,
}))
vi.mock('@/components/insights/diagnostics/SectorDivergenceWidget', () => ({
  default: () => <div data-testid="sector-divergence-widget" />,
}))
vi.mock('@/components/insights/diagnostics/AASWidget', () => ({
  default: () => <div data-testid="aas-widget" />,
}))
vi.mock('@/components/insights/diagnostics/IndicatorCardGrid', () => ({
  default: () => <div data-testid="indicator-card-grid" />,
}))
vi.mock('@/components/insights/diagnostics/BubbleMarketMap', () => ({
  default: () => <div data-testid="bubble-market-map" />,
}))
vi.mock('@/components/insights/diagnostics/SectorProjectionsPanel', () => ({
  default: () => <div data-testid="sector-projections-panel" />,
}))
vi.mock('@/components/insights/diagnostics/NewsStrip', () => ({
  default: () => <div data-testid="news-strip" />,
}))

import Dashboard from '../Dashboard'

describe('Dashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders without crashing and shows the active symbol', () => {
    render(<Dashboard />)
    // AAPL appears in the hero heading and the chart header — use getAllByText
    const symbols = screen.getAllByText('AAPL')
    expect(symbols.length).toBeGreaterThan(0)
  })

  it('shows account snapshot KPI cards when account data is present', () => {
    render(<Dashboard />)
    expect(screen.getByText('Account Snapshot')).toBeInTheDocument()
    expect(screen.getByText('Net Liquidation')).toBeInTheDocument()
    expect(screen.getByText('$55,000.00')).toBeInTheDocument()
  })

  it('shows the launchpad action cards', () => {
    render(<Dashboard />)
    expect(screen.getByText('TradeBot')).toBeInTheDocument()
    expect(screen.getByText('Screener')).toBeInTheDocument()
    expect(screen.getByText('Stock Profile')).toBeInTheDocument()
    expect(screen.getByText('Autopilot')).toBeInTheDocument()
  })

  it('navigates to market workspace when button is clicked', () => {
    render(<Dashboard />)
    fireEvent.click(screen.getByRole('button', { name: /open market workspace/i }))
    expect(mockSetRoute).toHaveBeenCalledWith('market')
  })

  it('navigates to screener when Run screener is clicked', () => {
    render(<Dashboard />)
    fireEvent.click(screen.getByRole('button', { name: /run screener/i }))
    expect(mockSetRoute).toHaveBeenCalledWith('screener')
  })

  it('renders the live chart section heading', () => {
    render(<Dashboard />)
    expect(screen.getByText('Live chart')).toBeInTheDocument()
  })

  it('renders trading chart with the active symbol', () => {
    render(<Dashboard />)
    expect(screen.getByTestId('trading-chart')).toHaveTextContent('AAPL')
  })

  it('does not render diagnostics section when market_diagnostics feature is off', () => {
    render(<Dashboard />)
    expect(screen.queryByText('Market Diagnostics')).not.toBeInTheDocument()
  })
})
