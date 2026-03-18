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

// ── Inline SVG icons ──────────────────────────────────────────────────────────

function IconBeaker({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 3h6M9 3v7l-5 9h16l-5-9V3" />
      <path d="M7 16h10" strokeOpacity="0.5" />
    </svg>
  )
}

function IconChart({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  )
}

function IconTicker({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="6" width="20" height="12" rx="2" />
      <path d="M6 10h2M10 10h2M14 10h2M6 14h8" strokeOpacity="0.7" />
    </svg>
  )
}

function IconPlay({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M8 5.14v14.72a1 1 0 001.5.86l11-7.36a1 1 0 000-1.72l-11-7.36A1 1 0 008 5.14z" />
    </svg>
  )
}

function IconSave({ className = 'w-3.5 h-3.5' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z" />
      <polyline points="17 21 17 13 7 13 7 21" />
      <polyline points="7 3 7 8 15 8" />
    </svg>
  )
}

function IconDownload({ className = 'w-3.5 h-3.5' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  )
}

function IconWarning({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  )
}

// ── Pill selector ─────────────────────────────────────────────────────────────

interface PillSelectorProps {
  options: string[]
  value: string
  onChange: (v: string) => void
}

function PillSelector({ options, value, onChange }: PillSelectorProps) {
  return (
    <div className="flex gap-1 bg-white border border-gray-200 rounded-xl p-0.5">
      {options.map((opt) => (
        <button
          key={opt}
          onClick={() => onChange(opt)}
          className={`px-2.5 py-1 rounded-[0.6rem] text-xs font-mono font-medium transition-all ${
            value === opt
              ? 'bg-indigo-100 text-indigo-600 shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]'
              : 'text-gray-500 hover:text-gray-800 hover:bg-gray-100/60'
          }`}
        >
          {opt.toUpperCase()}
        </button>
      ))}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function BacktestPage() {
  // State slices — individual selectors prevent full-store re-renders
  const entryConditions  = useBacktestStore((s) => s.entryConditions)
  const exitConditions   = useBacktestStore((s) => s.exitConditions)
  const conditionLogic   = useBacktestStore((s) => s.conditionLogic)
  const symbol           = useBacktestStore((s) => s.symbol)
  const period           = useBacktestStore((s) => s.period)
  const interval         = useBacktestStore((s) => s.interval)
  const initialCapital   = useBacktestStore((s) => s.initialCapital)
  const positionSizePct  = useBacktestStore((s) => s.positionSizePct)
  const stopLossPct      = useBacktestStore((s) => s.stopLossPct)
  const takeProfitPct    = useBacktestStore((s) => s.takeProfitPct)
  const result           = useBacktestStore((s) => s.result)
  const loading          = useBacktestStore((s) => s.loading)
  const error            = useBacktestStore((s) => s.error)
  const savedBacktests   = useBacktestStore((s) => s.savedBacktests)

  // Setters
  const setSymbol          = useBacktestStore((s) => s.setSymbol)
  const setPeriod          = useBacktestStore((s) => s.setPeriod)
  const setInterval        = useBacktestStore((s) => s.setInterval)
  const setLoading         = useBacktestStore((s) => s.setLoading)
  const setError           = useBacktestStore((s) => s.setError)
  const setResult          = useBacktestStore((s) => s.setResult)
  const setSavedBacktests  = useBacktestStore((s) => s.setSavedBacktests)

  const [saveName, setSaveName] = useState('')
  const [saving, setSaving] = useState(false)

  const handleRun = async () => {
    setLoading(true)
    setError(null)
    try {
      const backtestResult = await runBacktest({
        symbol: symbol.toUpperCase(),
        period,
        interval,
        entry_conditions: entryConditions,
        exit_conditions: exitConditions,
        condition_logic: conditionLogic,
        initial_capital: initialCapital,
        position_size_pct: positionSizePct,
        stop_loss_pct: stopLossPct,
        take_profit_pct: takeProfitPct,
      })
      setResult(backtestResult)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Backtest failed')
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async () => {
    if (!result || !saveName.trim()) return
    setSaving(true)
    try {
      await saveBacktest(saveName.trim(), result)
      setSaveName('')
      const history = await fetchBacktestHistory()
      setSavedBacktests(history)
    } catch {
      // Silent — save is best-effort
    } finally {
      setSaving(false)
    }
  }

  const handleExport = () => {
    if (!result) return
    const json = JSON.stringify(result, null, 2)
    const blob = new Blob([json], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `backtest-${result.symbol}-${Date.now()}.json`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const isReady = symbol.trim().length > 0 && !loading

  return (
    <div className="flex gap-5 h-full min-h-0 p-5">

      {/* ── Left panel — Strategy config (40%) ─────────────────────────── */}
      <div className="w-[40%] flex flex-col gap-5 overflow-y-auto pr-2">

        {/* Config card */}
        <div className="card rounded-2xl shadow-card p-5">
          {/* Section header */}
          <div className="flex items-center gap-2 mb-5 pb-4 border-b border-gray-100">
            <span className="flex items-center justify-center w-7 h-7 rounded-lg bg-indigo-50 text-indigo-600">
              <IconBeaker className="w-4 h-4" />
            </span>
            <h2 className="text-sm font-sans font-semibold text-gray-800">Strategy Configuration</h2>
          </div>

          {/* Inputs row */}
          <div className="flex items-end gap-4 flex-wrap">

            {/* Symbol */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-sans font-medium text-gray-500 tracking-wide">Symbol</label>
              <div className="relative flex items-center">
                <span className="absolute left-2.5 text-gray-400 pointer-events-none">
                  <IconTicker className="w-3.5 h-3.5" />
                </span>
                <input
                  className="bg-white border border-gray-200 rounded-xl pl-8 pr-3 py-1.5 text-sm font-mono text-gray-800 w-24 uppercase focus:outline-none focus:border-indigo-100 focus:ring-1 focus:ring-indigo-300 transition-all placeholder:text-gray-400"
                  placeholder="SPY"
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                />
              </div>
            </div>

            {/* Period */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-sans font-medium text-gray-500 tracking-wide">Period</label>
              <PillSelector
                options={PERIODS}
                value={period}
                onChange={setPeriod}
              />
            </div>

            {/* Interval */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-sans font-medium text-gray-500 tracking-wide">Interval</label>
              <PillSelector
                options={INTERVALS}
                value={interval}
                onChange={setInterval}
              />
            </div>
          </div>

          {/* Divider */}
          <div className="mt-5 pt-4 border-t border-gray-100">
            <button
              onClick={handleRun}
              disabled={!isReady}
              className={`
                w-full flex items-center justify-center gap-2.5 px-6 py-2.5 rounded-xl text-sm font-sans font-semibold
                transition-all duration-200 select-none
                ${isReady
                  ? 'bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-400 hover:to-purple-400 text-white shadow-glow-blue hover:shadow-[0_0_28px_rgba(99,102,241,0.35)]'
                  : 'bg-gradient-to-r from-indigo-700 to-purple-800 text-white/40 cursor-not-allowed'
                }
              `}
            >
              {loading ? (
                <svg className="animate-spin h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              ) : (
                <IconPlay className="w-4 h-4 shrink-0" />
              )}
              {loading ? 'Running...' : 'Run Backtest'}
            </button>
          </div>
        </div>

        {/* Strategy builder + params */}
        <StrategyBuilder />
        <BacktestParams />

        {/* Saved backtests */}
        {savedBacktests.length > 0 && (
          <div className="card rounded-2xl shadow-card p-5">
            <div className="flex items-center gap-2 mb-3 pb-3 border-b border-gray-100">
              <h3 className="text-xs font-sans font-medium text-gray-500 tracking-wide uppercase">Saved Backtests</h3>
              <span className="ml-auto text-xs font-mono text-gray-400">{savedBacktests.length}</span>
            </div>
            <div className="space-y-1.5">
              {savedBacktests.map((bt) => (
                <div key={bt.id} className="flex items-center justify-between bg-gray-50/50 hover:bg-gray-50/80 rounded-xl px-3 py-2 text-xs transition-colors group">
                  <span className="font-sans text-gray-800 group-hover:text-white transition-colors">{bt.name}</span>
                  <span className="font-mono text-gray-400">{bt.symbol} &bull; {bt.num_trades} trades</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── Right panel — Results (60%) ──────────────────────────────────── */}
      <div className="w-[60%] flex flex-col gap-5 overflow-y-auto pl-2">

        {/* Error state */}
        {error && (
          <div className="card rounded-2xl border border-red-300/25 bg-red-600/[0.06] p-5 animate-fade-in-up">
            <div className="flex items-start gap-3">
              <span className="shrink-0 text-red-600 mt-0.5">
                <IconWarning className="w-5 h-5" />
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-sans font-medium text-red-600 mb-0.5">Backtest Failed</p>
                <p className="text-xs font-mono text-red-600/70 break-words">{error}</p>
              </div>
              <button
                onClick={handleRun}
                className="shrink-0 flex items-center gap-1.5 text-xs font-sans font-medium text-red-600/80 hover:text-red-600 bg-red-600/10 hover:bg-red-600/20 px-3 py-1.5 rounded-lg border border-red-300/20 transition-all"
              >
                <IconPlay className="w-3 h-3" />
                Retry
              </button>
            </div>
          </div>
        )}

        {/* Loading state */}
        {loading && !result && (
          <div className="flex-1 flex flex-col items-center justify-center gap-5 animate-fade-in-up">
            {/* Animated ring */}
            <div className="relative flex items-center justify-center w-16 h-16">
              <div className="absolute inset-0 rounded-full border-2 border-indigo-600/10" />
              <div className="absolute inset-0 rounded-full border-2 border-transparent border-t-indigo-400 animate-spin" />
              <div className="absolute inset-2 rounded-full border border-purple-500/20 border-t-purple-400 animate-[spin_1.5s_linear_infinite_reverse]" />
              <IconChart className="w-5 h-5 text-indigo-600/60" />
            </div>
            <div className="text-center space-y-1.5">
              <p className="text-sm font-sans font-medium text-gray-500">
                Running backtest for{' '}
                <span className="font-mono text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded-md">
                  {symbol || 'symbol'}
                </span>
              </p>
              <p className="text-xs font-sans text-gray-400">Processing bar-by-bar simulation...</p>
            </div>
          </div>
        )}

        {/* Empty state */}
        {!result && !error && !loading && (
          <div className="flex-1 flex items-center justify-center animate-fade-in-up">
            <div className="flex flex-col items-center gap-4 p-10 rounded-2xl border border-gray-100 bg-white/[0.01] max-w-sm text-center">
              <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-gray-50 border border-gray-200">
                <IconBeaker className="w-7 h-7 text-gray-400" />
              </div>
              <div className="space-y-1.5">
                <p className="text-sm font-sans font-medium text-gray-500">No Results Yet</p>
                <p className="text-xs font-sans text-gray-400 leading-relaxed">
                  Configure a strategy on the left and run a backtest to see your equity curve, metrics, and trade log here.
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Results */}
        {result && (
          <div className="flex flex-col gap-5 animate-fade-in-up">

            {/* Results header */}
            <div className="card rounded-2xl shadow-card p-4">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                {/* Title + metadata */}
                <div className="flex items-center gap-3">
                  <span className="flex items-center justify-center w-7 h-7 rounded-lg bg-emerald-500/15 text-emerald-400">
                    <IconChart className="w-4 h-4" />
                  </span>
                  <div>
                    <h2 className="text-sm font-sans font-semibold text-gray-800">Backtest Results</h2>
                    <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
                      <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-indigo-50 text-indigo-600 text-xs font-mono font-medium">
                        {result.symbol}
                      </span>
                      <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-gray-50 text-gray-500 text-xs font-mono">
                        {result.period}
                      </span>
                      <span className="text-xs font-mono text-gray-400">
                        {result.total_bars.toLocaleString()} bars
                      </span>
                      {result.created_at && (
                        <>
                          <span className="text-gray-400/40 text-xs">&bull;</span>
                          <span className="text-xs font-mono text-gray-400">
                            {new Date(result.created_at).toLocaleString()}
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                </div>

                {/* Save / Export controls */}
                <div className="flex items-center gap-2">
                  <input
                    className="bg-white border border-gray-200 rounded-xl px-3 py-1 text-xs font-sans text-gray-800 w-36 placeholder:text-gray-400 focus:outline-none focus:border-indigo-100 focus:ring-1 focus:ring-indigo-300 transition-all"
                    placeholder="Name to save..."
                    value={saveName}
                    onChange={(e) => setSaveName(e.target.value)}
                  />
                  <button
                    onClick={handleSave}
                    disabled={saving || !saveName.trim()}
                    className="flex items-center gap-1.5 text-xs font-sans font-medium bg-gray-50 hover:bg-gray-50/80 disabled:opacity-40 disabled:cursor-not-allowed text-gray-800 px-3 py-1.5 rounded-xl border border-gray-200 transition-all hover:border-white/10"
                    title="Save backtest"
                  >
                    <IconSave />
                    {saving ? 'Saving...' : 'Save'}
                  </button>
                  <button
                    onClick={handleExport}
                    className="flex items-center gap-1.5 text-xs font-sans font-medium bg-gray-50 hover:bg-gray-50/80 text-gray-800 px-3 py-1.5 rounded-xl border border-gray-200 transition-all hover:border-white/10"
                    title="Export as JSON"
                  >
                    <IconDownload />
                    Export
                  </button>
                </div>
              </div>
            </div>

            {/* Charts + tables */}
            <EquityCurve result={result} />
            <MetricsPanel metrics={result.metrics} />
            <BacktestTradeLog trades={result.trades} />
          </div>
        )}
      </div>
    </div>
  )
}
