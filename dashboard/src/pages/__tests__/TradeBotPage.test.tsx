import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// ── API mock ──────────────────────────────────────────────────────────────────
vi.mock('@/services/api', () => ({
  fetchTrades: vi.fn().mockResolvedValue([]),
  fetchSimAccount: vi.fn().mockResolvedValue(null),
  fetchSimPositions: vi.fn().mockResolvedValue([]),
  fetchAccountSummary: vi.fn().mockResolvedValue(null),
  fetchPositions: vi.fn().mockResolvedValue([]),
}))

// ── Store mock ────────────────────────────────────────────────────────────────
// vi.mock is hoisted — all state must be defined inside the factory.
vi.mock('@/store', () => {
  const accountState = {
    account: {
      balance: 55_000,
      cash: 20_000,
      unrealized_pnl: 1_200,
      realized_pnl: 800,
    },
    positions: [
      { symbol: 'NVDA', quantity: 10, avg_cost: 800, current_price: 900, unrealized_pnl: 1000 },
    ],
    trades: [
      { id: 't1', symbol: 'AAPL', action: 'BUY', quantity: 5, fill_price: 210, timestamp: '2026-03-30T10:00:00Z' },
    ],
    setTrades: vi.fn(),
    setAccount: vi.fn(),
    setPositions: vi.fn(),
  }

  const botState = { simMode: false, botRunning: true, ibkrConnected: true }
  const simState = { simAccount: null, setSimAccount: vi.fn(), setSimPositions: vi.fn() }
  const uiState = {
    tradebotTab: 'positions' as 'positions' | 'rules' | 'insights' | 'activity',
    setTradebotTab: vi.fn(),
  }

  // Support both: useStore(sel) and const { ... } = useStore()
  const ms =
    <T extends object>(state: T) =>
    (sel?: (s: T) => unknown) =>
      sel ? sel(state) : state

  return {
    useAccountStore: Object.assign(ms(accountState), {
      getState: () => accountState,
    }),
    useBotStore: ms(botState),
    useSimStore: ms(simState),
    useUIStore: ms(uiState),
  }
})

// ── Component mocks ───────────────────────────────────────────────────────────
vi.mock('@/components/tradebot/BotToggle', () => ({
  default: () => <div data-testid="bot-toggle" />,
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

vi.mock('@/components/tradebot/PositionsContent', () => ({
  PositionsContent: ({
    positions,
  }: {
    positions: Array<{ symbol: string }>
    initialLoad: boolean
  }) => (
    <div data-testid="positions-content">
      {positions.map((p) => (
        <div key={p.symbol}>{p.symbol}</div>
      ))}
    </div>
  ),
}))

vi.mock('@/components/tradebot/ActivityContent', () => ({
  ActivityContent: ({
    trades,
  }: {
    trades: Array<{ id: string; symbol: string }>
    initialLoad: boolean
  }) => (
    <div data-testid="activity-content">
      {trades.map((t) => (
        <div key={t.id}>{t.symbol}</div>
      ))}
    </div>
  ),
}))

// Lazy-loaded pages — stub them so Suspense resolves immediately
vi.mock('@/pages/RulesPage', () => ({ default: () => <div data-testid="rules-page" /> }))
vi.mock('@/pages/AutopilotPage', () => ({ default: () => <div data-testid="autopilot-page" /> }))

import TradeBotPage from '../TradeBotPage'

describe('TradeBotPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders without crashing and shows the TradeBot heading', () => {
    render(<TradeBotPage />)
    expect(screen.getByText('TradeBot')).toBeInTheDocument()
  })

  it('shows bot status chips', () => {
    render(<TradeBotPage />)
    expect(screen.getByText('Bot active')).toBeInTheDocument()
    expect(screen.getByText('IBKR connected')).toBeInTheDocument()
    expect(screen.getByText('Live account')).toBeInTheDocument()
  })

  it('shows account KPI cards when account data is available', async () => {
    render(<TradeBotPage />)
    await waitFor(() => {
      expect(screen.getByText('Net Liquidation')).toBeInTheDocument()
    })
    expect(screen.getByText('$55,000.00')).toBeInTheDocument()
  })

  it('shows position count chip', () => {
    render(<TradeBotPage />)
    expect(screen.getByText('1 open position')).toBeInTheDocument()
  })

  it('shows recent trades count chip', () => {
    render(<TradeBotPage />)
    expect(screen.getByText('1 recent trade')).toBeInTheDocument()
  })

  it('renders the positions tab content by default', () => {
    render(<TradeBotPage />)
    expect(screen.getByTestId('positions-content')).toBeInTheDocument()
    expect(screen.getByText('NVDA')).toBeInTheDocument()
  })

  it('renders the BotToggle component', () => {
    render(<TradeBotPage />)
    expect(screen.getByTestId('bot-toggle')).toBeInTheDocument()
  })

  it('renders tab navigation with all four tabs', () => {
    render(<TradeBotPage />)
    const tabs = screen.getByTestId('tradebot-tabs')
    expect(tabs).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Positions' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rules' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Autopilot' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Activity' })).toBeInTheDocument()
  })

  it('shows refreshes-every chip', () => {
    render(<TradeBotPage />)
    expect(screen.getByText('Refreshes every 10s')).toBeInTheDocument()
  })
})
