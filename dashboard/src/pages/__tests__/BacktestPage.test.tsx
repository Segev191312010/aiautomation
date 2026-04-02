import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

// ── API mock ──────────────────────────────────────────────────────────────────
vi.mock('@/services/api', () => ({
  runBacktest: vi.fn(),
  saveBacktest: vi.fn(),
  fetchBacktestHistory: vi.fn().mockResolvedValue([]),
}))

// ── Store mock ────────────────────────────────────────────────────────────────
const mockSetSymbol = vi.fn()
const mockSetPeriod = vi.fn()
const mockSetInterval = vi.fn()
const mockSetLoading = vi.fn()
const mockSetError = vi.fn()
const mockSetResult = vi.fn()
const mockSetSavedBacktests = vi.fn()

const backtestStoreBase = {
  entryConditions: [],
  exitConditions: [],
  conditionLogic: 'AND',
  symbol: 'SPY',
  period: '1y',
  interval: '1d',
  initialCapital: 10_000,
  positionSizePct: 10,
  stopLossPct: 2,
  takeProfitPct: 4,
  exitMode: 'simple',
  atrStopMult: 2,
  atrTrailMult: 3,
  startDate: null,
  endDate: null,
  result: null,
  loading: false,
  error: null,
  savedBacktests: [],
  setSymbol: mockSetSymbol,
  setPeriod: mockSetPeriod,
  setInterval: mockSetInterval,
  setLoading: mockSetLoading,
  setError: mockSetError,
  setResult: mockSetResult,
  setSavedBacktests: mockSetSavedBacktests,
}

// Mutable so individual tests can override slices
let storeOverrides: Partial<typeof backtestStoreBase> = {}

vi.mock('@/store', () => ({
  useBacktestStore: (sel: (s: object) => unknown) =>
    sel({ ...backtestStoreBase, ...storeOverrides }),
}))

// ── Component mocks ───────────────────────────────────────────────────────────
vi.mock('@/components/backtest/StrategyBuilder', () => ({
  StrategyBuilder: () => <div data-testid="strategy-builder" />,
}))

vi.mock('@/components/backtest/BacktestParams', () => ({
  BacktestParams: () => <div data-testid="backtest-params" />,
}))

vi.mock('@/components/backtest/EquityCurve', () => ({
  EquityCurve: () => <div data-testid="equity-curve" />,
}))

vi.mock('@/components/backtest/MetricsPanel', () => ({
  MetricsPanel: () => <div data-testid="metrics-panel" />,
}))

vi.mock('@/components/backtest/BacktestTradeLog', () => ({
  BacktestTradeLog: () => <div data-testid="backtest-trade-log" />,
}))

import BacktestPage from '../BacktestPage'
import * as api from '@/services/api'

const sampleResult = {
  symbol: 'SPY',
  period: '1y',
  interval: '1d',
  exit_mode: 'simple',
  total_bars: 252,
  num_trades: 18,
  metrics: { total_return_pct: 12.4, sharpe_ratio: 1.1, max_drawdown_pct: -8.2, win_rate: 55.6 },
  trades: [],
  equity_curve: [],
  created_at: '2026-03-31T10:00:00Z',
}

describe('BacktestPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    storeOverrides = {}
  })

  it('renders without crashing and shows Strategy Configuration heading', () => {
    render(<BacktestPage />)
    expect(screen.getByText('Strategy Configuration')).toBeInTheDocument()
  })

  it('shows the symbol input pre-filled with the store value', () => {
    render(<BacktestPage />)
    const input = screen.getByPlaceholderText('SPY') as HTMLInputElement
    expect(input.value).toBe('SPY')
  })

  it('renders the Run Backtest button and it is enabled when symbol is set', () => {
    render(<BacktestPage />)
    const btn = screen.getByRole('button', { name: /run backtest/i })
    expect(btn).toBeInTheDocument()
    expect(btn).not.toBeDisabled()
  })

  it('disables Run Backtest button when symbol is empty', () => {
    storeOverrides = { symbol: '' }
    render(<BacktestPage />)
    expect(screen.getByRole('button', { name: /run backtest/i })).toBeDisabled()
  })

  it('renders the StrategyBuilder and BacktestParams sub-components', () => {
    render(<BacktestPage />)
    expect(screen.getByTestId('strategy-builder')).toBeInTheDocument()
    expect(screen.getByTestId('backtest-params')).toBeInTheDocument()
  })

  it('shows the empty state when there are no results, no error, and not loading', () => {
    render(<BacktestPage />)
    expect(screen.getByText('No Results Yet')).toBeInTheDocument()
  })

  it('shows loading state while backtest is running', () => {
    storeOverrides = { loading: true, result: null }
    render(<BacktestPage />)
    expect(screen.getByText(/running backtest for/i)).toBeInTheDocument()
    expect(screen.getByText('Processing bar-by-bar simulation...')).toBeInTheDocument()
    // Run Backtest button shows "Running..." label
    expect(screen.getByRole('button', { name: /running/i })).toBeInTheDocument()
  })

  it('shows error state when backtest fails', () => {
    ;(storeOverrides as Record<string, unknown>).error = 'Not enough data for the requested period'
    render(<BacktestPage />)
    expect(screen.getByText('Backtest Failed')).toBeInTheDocument()
    expect(screen.getByText('Not enough data for the requested period')).toBeInTheDocument()
  })

  it('calls runBacktest with correct params when Run Backtest is clicked', async () => {
    vi.mocked(api.runBacktest).mockResolvedValue(sampleResult as never)
    render(<BacktestPage />)
    fireEvent.click(screen.getByRole('button', { name: /run backtest/i }))
    await waitFor(() => {
      expect(api.runBacktest).toHaveBeenCalledWith(
        expect.objectContaining({
          symbol: 'SPY',
          period: '1y',
          interval: '1d',
        }),
      )
    })
  })

  it('shows results panel when result is available', () => {
    storeOverrides = { result: sampleResult as unknown as typeof backtestStoreBase['result'] }
    render(<BacktestPage />)
    expect(screen.getByText('Backtest Results')).toBeInTheDocument()
    expect(screen.getByTestId('equity-curve')).toBeInTheDocument()
    expect(screen.getByTestId('metrics-panel')).toBeInTheDocument()
    expect(screen.getByTestId('backtest-trade-log')).toBeInTheDocument()
  })

  it('shows result metadata — symbol, period, and bar count', () => {
    storeOverrides = { result: sampleResult as unknown as typeof backtestStoreBase['result'] }
    render(<BacktestPage />)
    expect(screen.getByText('SPY')).toBeInTheDocument()
    expect(screen.getByText('1y')).toBeInTheDocument()
    expect(screen.getByText('252 bars')).toBeInTheDocument()
  })

  it('shows Save and Export buttons when result is present', () => {
    storeOverrides = { result: sampleResult as unknown as typeof backtestStoreBase['result'] }
    render(<BacktestPage />)
    expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /export/i })).toBeInTheDocument()
  })

  it('Save button is disabled until a name is entered', () => {
    storeOverrides = { result: sampleResult as unknown as typeof backtestStoreBase['result'] }
    render(<BacktestPage />)
    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled()
    fireEvent.change(screen.getByPlaceholderText('Name to save...'), {
      target: { value: 'My test run' },
    })
    expect(screen.getByRole('button', { name: /^save$/i })).not.toBeDisabled()
  })

  it('shows saved backtests list when savedBacktests is non-empty', () => {
    storeOverrides = {
      savedBacktests: [
        { id: 'bt1', name: 'Bull run 2025', symbol: 'SPY', num_trades: 12 },
        { id: 'bt2', name: 'Bear test', symbol: 'QQQ', num_trades: 8 },
      ] as typeof backtestStoreBase['savedBacktests'],
    }
    render(<BacktestPage />)
    expect(screen.getByText('Saved Backtests')).toBeInTheDocument()
    expect(screen.getByText('Bull run 2025')).toBeInTheDocument()
    expect(screen.getByText('Bear test')).toBeInTheDocument()
  })

  it('does not show saved backtests section when the list is empty', () => {
    render(<BacktestPage />)
    expect(screen.queryByText('Saved Backtests')).not.toBeInTheDocument()
  })
})
