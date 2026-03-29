/**
 * ChartsPage — embeds ib_chart (single + multi-chart) via iframe.
 *
 * ib_chart runs on port 5001 as a sidecar Flask service.
 * Supports single-symbol charts and multi-chart grid from screener results.
 */
import { useState, useMemo } from 'react'
import { useMarketStore } from '@/store'

const IB_CHART_BASE = 'http://127.0.0.1:5001'

type ChartMode = 'single' | 'multi'
type Timeframe = 'D' | 'W' | 'M' | '5' | '1'

export default function ChartsPage() {
  const { selectedSymbol } = useMarketStore()
  const [mode, setMode] = useState<ChartMode>('single')
  const [timeframe, setTimeframe] = useState<Timeframe>('D')
  const [multiSymbols, setMultiSymbols] = useState(selectedSymbol || 'AAPL')
  const [symbolInput, setSymbolInput] = useState('')

  const chartUrl = useMemo(() => {
    if (mode === 'multi') {
      const syms = multiSymbols.split(',').map(s => s.trim().toUpperCase()).filter(Boolean).join(',')
      return `${IB_CHART_BASE}/ib_multichart.html?symbols=${syms}&tf=${timeframe}`
    }
    const sym = selectedSymbol || 'AAPL'
    return `${IB_CHART_BASE}/ib_chart.html?symbol=${sym}&tf=${timeframe}`
  }, [mode, selectedSymbol, multiSymbols, timeframe])

  const handleSymbolSubmit = () => {
    if (symbolInput.trim()) {
      setMultiSymbols(symbolInput.trim().toUpperCase())
      setMode('multi')
    }
  }

  return (
    <div className="flex flex-col h-full gap-3">
      {/* Toolbar */}
      <div className="flex items-center gap-3 rounded-xl border border-[var(--border)] bg-white px-4 py-2.5">
        {/* Mode toggle */}
        <div className="flex rounded-lg border border-[var(--border)] overflow-hidden">
          <button
            onClick={() => setMode('single')}
            className={`px-3 py-1.5 text-xs font-medium ${mode === 'single' ? 'bg-indigo-600 text-white' : 'hover:bg-slate-50'}`}
          >
            Single Chart
          </button>
          <button
            onClick={() => setMode('multi')}
            className={`px-3 py-1.5 text-xs font-medium ${mode === 'multi' ? 'bg-indigo-600 text-white' : 'hover:bg-slate-50'}`}
          >
            Multi Chart
          </button>
        </div>

        {/* Timeframe */}
        <div className="flex rounded-lg border border-[var(--border)] overflow-hidden">
          {(['1', '5', 'D', 'W', 'M'] as Timeframe[]).map(tf => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={`px-2.5 py-1.5 text-xs font-medium ${timeframe === tf ? 'bg-indigo-600 text-white' : 'hover:bg-slate-50'}`}
            >
              {tf === '1' ? '1m' : tf === '5' ? '5m' : tf}
            </button>
          ))}
        </div>

        {/* Multi-symbol input */}
        {mode === 'multi' && (
          <div className="flex items-center gap-2 flex-1">
            <input
              type="text"
              value={symbolInput}
              onChange={e => setSymbolInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSymbolSubmit()}
              placeholder="AAPL, MSFT, NVDA, TSLA..."
              className="flex-1 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm"
            />
            <button
              onClick={handleSymbolSubmit}
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700"
            >
              Load Grid
            </button>
          </div>
        )}

        {mode === 'single' && (
          <span className="text-sm text-[var(--text-muted)]">
            Showing: <span className="font-semibold text-[var(--text-primary)]">{selectedSymbol || 'AAPL'}</span>
          </span>
        )}

        {/* Open in new window */}
        <a
          href={chartUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs hover:bg-slate-50"
        >
          Pop Out
        </a>
      </div>

      {/* Chart iframe */}
      <div className="flex-1 rounded-xl border border-[var(--border)] overflow-hidden bg-white min-h-0">
        <iframe
          key={chartUrl}
          src={chartUrl}
          className="w-full h-full border-0"
          title="ib_chart"
          allow="fullscreen"
        />
      </div>
    </div>
  )
}
