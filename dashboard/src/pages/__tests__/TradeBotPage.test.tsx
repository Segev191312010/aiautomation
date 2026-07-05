import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

vi.mock('@/services/api', () => ({
  fetchTrades: vi.fn().mockResolvedValue([]),
  fetchSimAccount: vi.fn().mockResolvedValue(null),
  fetchSimPositions: vi.fn().mockResolvedValue([]),
  fetchAccountSummary: vi.fn().mockResolvedValue(null),
  fetchPositions: vi.fn().mockResolvedValue([]),
}))

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
      {positions.map((position) => (
        <div key={position.symbol}>{position.symbol}</div>
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
      {trades.map((trade) => (
        <div key={trade.id}>{trade.symbol}</div>
      ))}
    </div>
  ),
}))

vi.mock('@/pages/RulesPage', () => ({ default: () => <div data-testid="rules-page" /> }))
vi.mock('@/pages/AutopilotPage', () => ({ default: () => <div data-testid="autopilot-page" /> }))

import TradeBotPage from '../TradeBotPage'
import * as api from '@/services/api'

async function renderTradeBotPage() {
  render(<TradeBotPage />)
  await waitFor(() => {
    expect(api.fetchTrades).toHaveBeenCalledTimes(1)
    expect(api.fetchAccountSummary).toHaveBeenCalledTimes(1)
    expect(api.fetchPositions).toHaveBeenCalledTimes(1)
  })
}

describe('TradeBotPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders without crashing and shows the TradeBot heading', async () => {
    await renderTradeBotPage()
    expect(screen.getByText('TradeBot')).toBeInTheDocument()
  })

  it('shows bot status chips', async () => {
    await renderTradeBotPage()
    expect(screen.getByText('Bot active')).toBeInTheDocument()
    expect(screen.getByText('IBKR connected')).toBeInTheDocument()
    expect(screen.getByText('Live account')).toBeInTheDocument()
  })

  it('shows account KPI cards when account data is available', async () => {
    await renderTradeBotPage()
    expect(screen.getByText('Net Liquidation')).toBeInTheDocument()
    expect(screen.getByText('$55,000.00')).toBeInTheDocument()
  })

  it('shows position count chip', async () => {
    await renderTradeBotPage()
    expect(screen.getByText('1 open position')).toBeInTheDocument()
  })

  it('shows recent trades count chip', async () => {
    await renderTradeBotPage()
    expect(screen.getByText('1 recent trade')).toBeInTheDocument()
  })

  it('renders the positions tab content by default', async () => {
    await renderTradeBotPage()
    expect(screen.getByTestId('positions-content')).toBeInTheDocument()
    expect(screen.getByText('NVDA')).toBeInTheDocument()
  })

  it('renders the BotToggle component', async () => {
    await renderTradeBotPage()
    expect(screen.getByTestId('bot-toggle')).toBeInTheDocument()
  })

  it('renders tab navigation with both tabs', async () => {
    await renderTradeBotPage()
    const tabs = screen.getByTestId('tradebot-tabs')
    expect(tabs).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Positions' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Activity' })).toBeInTheDocument()
  })

  it('shows refreshes-every chip', async () => {
    await renderTradeBotPage()
    expect(screen.getByText('Refreshes every 10s')).toBeInTheDocument()
  })
})
