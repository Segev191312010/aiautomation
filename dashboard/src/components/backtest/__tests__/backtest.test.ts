import { describe, it, expect, beforeEach } from 'vitest'
import { useBacktestStore } from '@/store'
import type { BacktestMetrics, Condition } from '@/types'

describe('useBacktestStore', () => {
  beforeEach(() => {
    useBacktestStore.getState().reset()
  })

  it('has correct defaults', () => {
    const s = useBacktestStore.getState()
    expect(s.symbol).toBe('AAPL')
    expect(s.period).toBe('2y')
    expect(s.interval).toBe('1d')
    expect(s.initialCapital).toBe(100_000)
    expect(s.positionSizePct).toBe(100)
    expect(s.stopLossPct).toBe(0)
    expect(s.takeProfitPct).toBe(0)
    expect(s.conditionLogic).toBe('AND')
    expect(s.entryConditions).toHaveLength(1)
    expect(s.exitConditions).toHaveLength(1)
    expect(s.result).toBeNull()
    expect(s.loading).toBe(false)
    expect(s.error).toBeNull()
  })

  it('sets symbol', () => {
    useBacktestStore.getState().setSymbol('TSLA')
    expect(useBacktestStore.getState().symbol).toBe('TSLA')
  })

  it('sets entry conditions', () => {
    const conds: Condition[] = [
      { indicator: 'SMA', params: { length: 50 }, operator: '>', value: 'SMA_200' },
    ]
    useBacktestStore.getState().setEntryConditions(conds)
    expect(useBacktestStore.getState().entryConditions).toEqual(conds)
  })

  it('resets to defaults', () => {
    const s = useBacktestStore.getState()
    s.setSymbol('MSFT')
    s.setPeriod('5y')
    s.setInitialCapital(50_000)
    s.reset()

    const after = useBacktestStore.getState()
    expect(after.symbol).toBe('AAPL')
    expect(after.period).toBe('2y')
    expect(after.initialCapital).toBe(100_000)
  })

  it('sets loading and error', () => {
    const s = useBacktestStore.getState()
    s.setLoading(true)
    expect(useBacktestStore.getState().loading).toBe(true)

    s.setError('Something failed')
    expect(useBacktestStore.getState().error).toBe('Something failed')
  })

  it('default entry condition is RSI < 30', () => {
    const entry = useBacktestStore.getState().entryConditions[0]
    expect(entry.indicator).toBe('RSI')
    expect(entry.params).toEqual({ length: 14 })
    expect(entry.operator).toBe('<')
    expect(entry.value).toBe(30)
  })

  it('default exit condition is RSI > 70', () => {
    const exit = useBacktestStore.getState().exitConditions[0]
    expect(exit.indicator).toBe('RSI')
    expect(exit.params).toEqual({ length: 14 })
    expect(exit.operator).toBe('>')
    expect(exit.value).toBe(70)
  })
})

describe('MetricsPanel formatting', () => {
  it('formats metrics values correctly', () => {
    const metrics: BacktestMetrics = {
      total_return_pct: 25.5,
      cagr: 12.3,
      sharpe_ratio: 1.45,
      sortino_ratio: 2.1,
      calmar_ratio: 0.8,
      max_drawdown_pct: 15.2,
      win_rate: 55.0,
      profit_factor: 2.3,
      num_trades: 12,
      avg_win: 150.5,
      avg_loss: -75.2,
      longest_win_streak: 4,
      longest_lose_streak: 2,
      avg_trade_duration_days: 8.5,
    }

    // Verify value formatting
    expect(metrics.total_return_pct.toFixed(1)).toBe('25.5')
    expect(metrics.sharpe_ratio.toFixed(2)).toBe('1.45')
    expect(metrics.max_drawdown_pct.toFixed(1)).toBe('15.2')
    expect(metrics.profit_factor.toFixed(2)).toBe('2.30')
    expect(metrics.num_trades).toBe(12)

    // Verify positive/negative logic
    expect(metrics.total_return_pct > 0).toBe(true)
    expect(metrics.sharpe_ratio > 1).toBe(true)
    expect(metrics.win_rate > 50).toBe(true)
  })
})
