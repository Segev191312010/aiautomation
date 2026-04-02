import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// ── API mock ──────────────────────────────────────────────────────────────────
vi.mock('@/services/api', () => ({
  fetchYahooBars: vi.fn().mockResolvedValue([]),
  fetchIBKRBars: vi.fn().mockResolvedValue([]),
  fetchSettings: vi.fn().mockResolvedValue({}),
}))

// ── Store mock ────────────────────────────────────────────────────────────────
// vi.mock is hoisted — all state must be defined inside the factory.
vi.mock('@/store', () => {
  const marketState = {
    selectedSymbol: 'AAPL',
    setSelectedSymbol: vi.fn(),
    quotes: {
      AAPL: {
        price: 210.5,
        change: 3.2,
        change_pct: 1.55,
        market_cap: 3_200_000_000_000,
        volume: 54_000_000,
        year_low: 164,
        year_high: 237,
        live_source: 'ibkr',
        market_state: 'open',
        stale_s: 0,
      },
    },
    setBars: vi.fn(),
    compMode: false,
    compSymbol: '',
    setCompSymbol: vi.fn(),
    setCompBars: vi.fn(),
    toggleCompMode: vi.fn(),
    bars: { AAPL: [] },
    selectedIndicators: [],
  }

  // Support both: useStore(sel) and const { ... } = useStore()
  const ms = (sel?: (s: typeof marketState) => unknown) => (sel ? sel(marketState) : marketState)

  return {
    useMarketStore: ms,
    useBotStore: (sel?: (s: object) => unknown) => {
      const state = { ibkrConnected: false }
      return sel ? sel(state) : state
    },
    useUIStore: (sel?: (s: object) => unknown) => {
      const state = { setRoute: vi.fn() }
      return sel ? sel(state) : state
    },
    useDrawingStore: (sel?: (s: object) => unknown) => {
      const state = { loadDrawings: vi.fn() }
      return sel ? sel(state) : state
    },
  }
})

// ── Hooks mock ────────────────────────────────────────────────────────────────
vi.mock('@/hooks/useCrosshairSync', () => ({ useCrosshairSync: vi.fn() }))

// ── Component mocks ───────────────────────────────────────────────────────────
vi.mock('@/components/chart/TradingChart', () => ({
  default: ({ symbol }: { symbol: string }) => (
    <div data-testid="trading-chart">{symbol}</div>
  ),
}))

vi.mock('@/components/chart/VolumePanel', () => ({
  default: () => <div data-testid="volume-panel" />,
}))

vi.mock('@/components/chart/IndicatorPanel', () => ({
  default: () => <div data-testid="indicator-panel" />,
}))

vi.mock('@/components/chart/ChartToolbar', () => ({
  default: ({ isLoading }: { isLoading: boolean }) => (
    <div data-testid="chart-toolbar" data-loading={String(isLoading)} />
  ),
  TOOLBAR_TIMEFRAMES: [
    { label: '1D', period: '1y', interval: '1d' },
    { label: '5D', period: '5d', interval: '5m' },
    { label: '1M', period: '1mo', interval: '1h' },
    { label: '3M', period: '3mo', interval: '1h' },
    { label: '6M', period: '6mo', interval: '1d' },
    { label: '1Y', period: '1y', interval: '1d' },
    { label: '2Y', period: '2y', interval: '1wk' },
    { label: '5Y', period: '5y', interval: '1mo' },
  ],
}))

vi.mock('@/components/chart/DrawingTools', () => ({
  default: () => <div data-testid="drawing-tools" />,
}))

vi.mock('@/components/chart/ResizeHandle', () => ({
  default: () => <div data-testid="resize-handle" />,
}))

vi.mock('@/components/ticker/TickerCard', () => ({
  default: () => <div data-testid="ticker-card" />,
}))

vi.mock('@/components/alerts/AlertForm', () => ({
  default: ({ onClose }: { initialSymbol: string; onClose: () => void }) => (
    <div data-testid="alert-form">
      <button type="button" onClick={onClose}>
        Close alert
      </button>
    </div>
  ),
}))

vi.mock('@/components/ui/ToastProvider', () => ({
  useToast: () => ({ info: vi.fn(), error: vi.fn(), success: vi.fn() }),
}))

import MarketPage from '../MarketPage'
import * as api from '@/services/api'

class ResizeObserverMock {
  observe() {}
  disconnect() {}
  unobserve() {}
}

describe('MarketPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.defineProperty(globalThis, 'ResizeObserver', {
      configurable: true,
      writable: true,
      value: ResizeObserverMock,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    delete (globalThis as { ResizeObserver?: typeof ResizeObserver }).ResizeObserver
  })

  it('renders without crashing and shows the Live market workspace label', () => {
    render(<MarketPage />)
    expect(screen.getByText('Live market workspace')).toBeInTheDocument()
  })

  it('shows the selected symbol in the hero header', () => {
    render(<MarketPage />)
    // Symbol appears multiple times in the page (hero + chart header + fundamentals)
    const symbols = screen.getAllByText('AAPL')
    expect(symbols.length).toBeGreaterThan(0)
  })

  it('renders the trading chart', () => {
    render(<MarketPage />)
    expect(screen.getByTestId('trading-chart')).toBeInTheDocument()
  })

  it('renders the chart toolbar', () => {
    render(<MarketPage />)
    expect(screen.getByTestId('chart-toolbar')).toBeInTheDocument()
  })

  it('renders the drawing tools rail', () => {
    render(<MarketPage />)
    expect(screen.getByTestId('drawing-tools')).toBeInTheDocument()
  })

  it('renders volume and indicator panels', () => {
    render(<MarketPage />)
    expect(screen.getByTestId('volume-panel')).toBeInTheDocument()
    expect(screen.getByTestId('indicator-panel')).toBeInTheDocument()
  })

  it('renders the ticker card for the active symbol quote', () => {
    render(<MarketPage />)
    expect(screen.getByTestId('ticker-card')).toBeInTheDocument()
  })

  it('shows market cap in the right-column stats', () => {
    render(<MarketPage />)
    // $3.20T for 3_200_000_000_000 — appears in both signal cards and sidebar
    expect(screen.getAllByText('$3.20T').length).toBeGreaterThan(0)
  })

  it('shows the 52W range signal card', () => {
    render(<MarketPage />)
    expect(screen.getByText('$164 - $237')).toBeInTheDocument()
  })

  it('shows "Add compare" button when not in compare mode', () => {
    render(<MarketPage />)
    expect(screen.getByRole('button', { name: /add compare/i })).toBeInTheDocument()
  })

  it('shows the symbol search input', () => {
    render(<MarketPage />)
    expect(screen.getByPlaceholderText('Enter symbol...')).toBeInTheDocument()
  })

  it('calls fetchSettings on mount to load drawings', async () => {
    render(<MarketPage />)
    await waitFor(() => {
      expect(api.fetchSettings).toHaveBeenCalled()
    })
  })

  it('shows the Chart command rail heading', () => {
    render(<MarketPage />)
    expect(screen.getByText('Chart command rail')).toBeInTheDocument()
  })

  it('shows Yahoo history label when IBKR is not connected', () => {
    render(<MarketPage />)
    // historyFeedLabel = 'Yahoo history' when ibkrConnected === false
    expect(screen.getAllByText('Yahoo history').length).toBeGreaterThan(0)
  })
})
