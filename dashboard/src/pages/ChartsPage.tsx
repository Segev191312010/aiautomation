import { useState } from 'react'
import TradingChart from '@/components/chart/TradingChart'
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import { useMarketStore } from '@/store'
import { validateSymbol } from '@/utils/validateSymbol'

export default function ChartsPage() {
  const selectedSymbol = useMarketStore((state) => state.selectedSymbol) || 'AAPL'
  const setSelectedSymbol = useMarketStore((state) => state.setSelectedSymbol)
  const [symbolInput, setSymbolInput] = useState(selectedSymbol)
  const [symbolError, setSymbolError] = useState<string | null>(null)

  const loadSymbol = () => {
    const normalized = symbolInput.trim().toUpperCase()
    const validation = validateSymbol(normalized)
    if (!validation.ok) {
      setSymbolError(validation.reason ?? 'Invalid symbol')
      return
    }
    setSymbolError(null)
    setSelectedSymbol(normalized)
  }

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-[var(--border)] bg-white px-4 py-2.5">
        <div className="flex items-center gap-2">
          <input
            aria-label="Chart symbol"
            value={symbolInput}
            onChange={(event) => setSymbolInput(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') loadSymbol() }}
            className="w-36 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm font-mono uppercase"
          />
          <button type="button" onClick={loadSymbol} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700">
            Load
          </button>
        </div>
        <span className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs text-[var(--text-muted)]">Daily bars</span>
        <span className="text-sm text-[var(--text-muted)]">
          Showing <strong className="font-mono text-[var(--text-primary)]">{selectedSymbol}</strong>
        </span>
        <span className="ml-auto text-xs text-[var(--text-muted)]" role="status">
          Multi-chart pop-out is unavailable in the local-only build.
        </span>
      </div>
      {symbolError && <div className="text-sm text-red-500" role="alert">{symbolError}</div>}
      <ErrorBoundary>
        <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-[var(--border)] bg-white">
          <TradingChart key={selectedSymbol} symbol={selectedSymbol} timeframe="1d" className="h-full" />
        </div>
      </ErrorBoundary>
    </div>
  )
}
