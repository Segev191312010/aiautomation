import { useState } from 'react'
import { useBacktestStore } from '@/store'
import { runBacktest, saveBacktest, fetchBacktestHistory } from '@/services/api'
import { StrategyBuilder } from '@/components/backtest/StrategyBuilder'
import { BacktestParams } from '@/components/backtest/BacktestParams'
import { EquityCurve } from '@/components/backtest/EquityCurve'
import { MetricsPanel } from '@/components/backtest/MetricsPanel'
import { BacktestTradeLog } from '@/components/backtest/BacktestTradeLog'
import type { BacktestResult } from '@/types'

const PERIODS = ['6mo', '1y', '2y', '5y']
const INTERVALS = ['1d', '1h']

export default function BacktestPage() {
  const store = useBacktestStore()
  const [saveName, setSaveName] = useState('')
  const [saving, setSaving] = useState(false)

  const handleRun = async () => {
    store.setLoading(true)
    store.setError(null)
    try {
      const result = await runBacktest({
        symbol: store.symbol.toUpperCase(),
        period: store.period,
        interval: store.interval,
        entry_conditions: store.entryConditions,
        exit_conditions: store.exitConditions,
        condition_logic: store.conditionLogic,
        initial_capital: store.initialCapital,
        position_size_pct: store.positionSizePct,
        stop_loss_pct: store.stopLossPct,
        take_profit_pct: store.takeProfitPct,
      })
      store.setResult(result)
    } catch (err: unknown) {
      store.setError(err instanceof Error ? err.message : 'Backtest failed')
    } finally {
      store.setLoading(false)
    }
  }

  const handleSave = async () => {
    if (!store.result || !saveName.trim()) return
    setSaving(true)
    try {
      await saveBacktest(saveName.trim(), store.result)
      setSaveName('')
      const history = await fetchBacktestHistory()
      store.setSavedBacktests(history)
    } catch {
      // Silent — save is best-effort
    } finally {
      setSaving(false)
    }
  }

  const handleExport = () => {
    if (!store.result) return
    const json = JSON.stringify(store.result, null, 2)
    const blob = new Blob([json], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `backtest-${store.result.symbol}-${Date.now()}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex gap-4 h-full min-h-0 p-4">
      {/* Left panel — Strategy config (40%) */}
      <div className="w-[40%] flex flex-col gap-4 overflow-y-auto pr-2">
        {/* Top bar */}
        <div className="flex items-end gap-3">
          <div>
            <label className="text-xs text-gray-400 block mb-1">Symbol</label>
            <input
              className="bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 w-24 uppercase"
              value={store.symbol}
              onChange={(e) => store.setSymbol(e.target.value.toUpperCase())}
            />
          </div>
          <div>
            <label className="text-xs text-gray-400 block mb-1">Period</label>
            <select
              className="bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100"
              value={store.period}
              onChange={(e) => store.setPeriod(e.target.value)}
            >
              {PERIODS.map((p) => <option key={p} value={p}>{p.toUpperCase()}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-gray-400 block mb-1">Interval</label>
            <select
              className="bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100"
              value={store.interval}
              onChange={(e) => store.setInterval(e.target.value)}
            >
              {INTERVALS.map((i) => <option key={i} value={i}>{i.toUpperCase()}</option>)}
            </select>
          </div>
          <button
            onClick={handleRun}
            disabled={store.loading}
            className="bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 disabled:cursor-not-allowed text-white px-4 py-1.5 rounded text-sm font-medium flex items-center gap-2"
          >
            {store.loading && (
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
            Run Backtest
          </button>
        </div>

        <StrategyBuilder />
        <BacktestParams />

        {/* Saved backtests */}
        {store.savedBacktests.length > 0 && (
          <div>
            <h3 className="text-sm font-semibold text-gray-200 mb-2">Saved Backtests</h3>
            <div className="space-y-1">
              {store.savedBacktests.map((bt) => (
                <div key={bt.id} className="flex items-center justify-between bg-gray-800/50 rounded px-2 py-1 text-xs">
                  <span className="text-gray-300">{bt.name}</span>
                  <span className="text-gray-500">{bt.symbol} • {bt.num_trades} trades</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Right panel — Results (60%) */}
      <div className="w-[60%] flex flex-col gap-4 overflow-y-auto pl-2">
        {store.error && (
          <div className="bg-red-900/20 border border-red-700/30 rounded p-3 flex items-center justify-between">
            <span className="text-sm text-red-400">{store.error}</span>
            <button
              onClick={handleRun}
              className="text-xs text-red-300 hover:text-red-200 underline ml-2"
            >
              Retry
            </button>
          </div>
        )}

        {!store.result && !store.error && !store.loading && (
          <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
            Configure a strategy and run a backtest
          </div>
        )}

        {store.loading && !store.result && (
          <div className="flex-1 flex items-center justify-center text-gray-500 text-sm gap-2">
            <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            Running backtest...
          </div>
        )}

        {store.result && (
          <>
            {/* Results header */}
            <div className="flex items-center justify-between">
              <div className="text-xs text-gray-500">
                {store.result.symbol} • {store.result.period} • {store.result.total_bars} bars
                {store.result.created_at && ` • ${new Date(store.result.created_at).toLocaleString()}`}
              </div>
              <div className="flex items-center gap-2">
                <input
                  className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 w-32"
                  placeholder="Name to save..."
                  value={saveName}
                  onChange={(e) => setSaveName(e.target.value)}
                />
                <button
                  onClick={handleSave}
                  disabled={saving || !saveName.trim()}
                  className="text-xs bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-gray-200 px-2 py-1 rounded"
                >
                  Save
                </button>
                <button
                  onClick={handleExport}
                  className="text-xs bg-gray-700 hover:bg-gray-600 text-gray-200 px-2 py-1 rounded"
                  title="Export as JSON"
                >
                  Export
                </button>
              </div>
            </div>

            <EquityCurve result={store.result} />
            <MetricsPanel metrics={store.result.metrics} />
            <BacktestTradeLog trades={store.result.trades} />
          </>
        )}
      </div>
    </div>
  )
}
