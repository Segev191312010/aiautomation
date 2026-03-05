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
    <div className="flex gap-5 h-full min-h-0 p-5">
      {/* Left panel — Strategy config (40%) */}
      <div className="w-[40%] flex flex-col gap-5 overflow-y-auto pr-2">
        {/* Top bar — symbol / period / interval / run */}
        <div className="glass rounded-2xl shadow-glass p-5 flex items-end gap-3 flex-wrap">
          <div>
            <label className="text-xs font-sans font-medium text-terminal-dim block mb-1.5">Symbol</label>
            <input
              className="bg-terminal-input border border-white/[0.06] rounded-xl px-3 py-1.5 text-sm font-mono text-terminal-text w-24 uppercase focus:outline-none focus:border-terminal-blue/40 focus:ring-1 focus:ring-terminal-blue/20 transition-colors"
              value={store.symbol}
              onChange={(e) => store.setSymbol(e.target.value.toUpperCase())}
            />
          </div>
          <div>
            <label className="text-xs font-sans font-medium text-terminal-dim block mb-1.5">Period</label>
            <select
              className="bg-terminal-input border border-white/[0.06] rounded-xl px-3 py-1.5 text-sm font-sans text-terminal-text focus:outline-none focus:border-terminal-blue/40 focus:ring-1 focus:ring-terminal-blue/20 transition-colors"
              value={store.period}
              onChange={(e) => store.setPeriod(e.target.value)}
            >
              {PERIODS.map((p) => <option key={p} value={p}>{p.toUpperCase()}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs font-sans font-medium text-terminal-dim block mb-1.5">Interval</label>
            <select
              className="bg-terminal-input border border-white/[0.06] rounded-xl px-3 py-1.5 text-sm font-sans text-terminal-text focus:outline-none focus:border-terminal-blue/40 focus:ring-1 focus:ring-terminal-blue/20 transition-colors"
              value={store.interval}
              onChange={(e) => store.setInterval(e.target.value)}
            >
              {INTERVALS.map((i) => <option key={i} value={i}>{i.toUpperCase()}</option>)}
            </select>
          </div>
          <button
            onClick={handleRun}
            disabled={store.loading}
            className="bg-gradient-to-r from-indigo-500 to-purple-500 hover:from-indigo-400 hover:to-purple-400 disabled:from-indigo-700 disabled:to-purple-800 disabled:cursor-not-allowed text-white px-5 py-1.5 rounded-xl text-sm font-sans font-medium flex items-center gap-2 transition-all shadow-glow-blue"
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
          <div className="glass rounded-2xl shadow-glass p-5">
            <h3 className="text-sm font-sans font-medium text-terminal-dim mb-3">Saved Backtests</h3>
            <div className="space-y-1.5">
              {store.savedBacktests.map((bt) => (
                <div key={bt.id} className="flex items-center justify-between bg-terminal-elevated/50 rounded-xl px-3 py-2 text-xs">
                  <span className="font-sans text-terminal-text">{bt.name}</span>
                  <span className="font-mono text-terminal-ghost">{bt.symbol} &bull; {bt.num_trades} trades</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Right panel — Results (60%) */}
      <div className="w-[60%] flex flex-col gap-5 overflow-y-auto pl-2">
        {store.error && (
          <div className="bg-terminal-red/10 border border-terminal-red/20 rounded-2xl p-4 flex items-center justify-between">
            <span className="text-sm font-sans text-terminal-red">{store.error}</span>
            <button
              onClick={handleRun}
              className="text-xs font-sans text-terminal-red/70 hover:text-terminal-red underline ml-2 transition-colors"
            >
              Retry
            </button>
          </div>
        )}

        {!store.result && !store.error && !store.loading && (
          <div className="flex-1 flex items-center justify-center text-terminal-ghost text-sm font-sans">
            Configure a strategy and run a backtest
          </div>
        )}

        {store.loading && !store.result && (
          <div className="flex-1 flex items-center justify-center text-terminal-dim text-sm font-sans gap-2">
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
              <div className="text-xs font-mono text-terminal-ghost">
                {store.result.symbol} &bull; {store.result.period} &bull; {store.result.total_bars} bars
                {store.result.created_at && ` \u2022 ${new Date(store.result.created_at).toLocaleString()}`}
              </div>
              <div className="flex items-center gap-2">
                <input
                  className="bg-terminal-input border border-white/[0.06] rounded-xl px-3 py-1 text-xs font-sans text-terminal-text w-36 placeholder:text-terminal-ghost focus:outline-none focus:border-terminal-blue/40 focus:ring-1 focus:ring-terminal-blue/20 transition-colors"
                  placeholder="Name to save..."
                  value={saveName}
                  onChange={(e) => setSaveName(e.target.value)}
                />
                <button
                  onClick={handleSave}
                  disabled={saving || !saveName.trim()}
                  className="text-xs font-sans bg-terminal-elevated hover:bg-terminal-elevated/80 disabled:opacity-40 text-terminal-text px-3 py-1 rounded-xl border border-white/[0.06] transition-colors"
                >
                  Save
                </button>
                <button
                  onClick={handleExport}
                  className="text-xs font-sans bg-terminal-elevated hover:bg-terminal-elevated/80 text-terminal-text px-3 py-1 rounded-xl border border-white/[0.06] transition-colors"
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
