import React from 'react'
import clsx from 'clsx'
import { useScreenerStore } from '@/store'

const UNIVERSES: { id: string; label: string; description: string }[] = [
  { id: 'sp500', label: 'S&P 500', description: 'Large-cap US equities' },
  { id: 'nasdaq100', label: 'NASDAQ 100', description: 'Growth and tech heavy' },
  { id: 'etfs', label: 'ETFs', description: 'Broad fund universe' },
  { id: 'custom', label: 'Custom', description: 'Your own ticker list' },
]

export default function UniverseSelector() {
  const { selectedUniverse, setUniverse, customSymbols, setCustomSymbols, universes } = useScreenerStore()

  const getCount = (id: string) => {
    const universe = universes.find((u) => u.id === id)
    return universe ? universe.count : null
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2">
        {UNIVERSES.map((universe) => {
          const active = selectedUniverse === universe.id
          const count = getCount(universe.id)

          return (
            <button
              key={universe.id}
              type="button"
              onClick={() => setUniverse(universe.id)}
              className={clsx(
                'rounded-lg border px-4 py-4 text-left transition-colors',
                active
                  ? 'border-gray-900 bg-gray-100'
                  : 'border-gray-200 bg-white hover:border-gray-400 hover:bg-gray-50',
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-sans font-semibold text-gray-900">{universe.label}</div>
                  <div className="mt-1 text-xs font-sans text-gray-500">{universe.description}</div>
                </div>
                <span
                  className={clsx(
                    'rounded-full px-2 py-1 text-[10px] font-mono',
                    active ? 'bg-white text-gray-900' : 'bg-gray-100 text-gray-500',
                  )}
                >
                  {count != null ? (count >= 1000 ? `${(count / 1000).toFixed(1)}k` : count) : '--'}
                </span>
              </div>
            </button>
          )
        })}
      </div>

      {selectedUniverse === 'custom' && (
        <div className="rounded-lg border border-[#E8E4DF] bg-[#FAF8F5] p-4">
          <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-gray-400">Custom Symbols</div>
          <div className="mt-3">
            <input
              type="text"
              value={customSymbols}
              onChange={(e) => setCustomSymbols(e.target.value.toUpperCase())}
              placeholder="AAPL, MSFT, TSLA, NVDA"
              className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-mono text-gray-800 placeholder:text-gray-400 focus:border-gray-400 focus:outline-none"
            />
          </div>
          <p className="mt-2 text-[11px] font-sans text-gray-500">
            Separate tickers with commas. Current count: {customSymbols.split(',').filter((item) => item.trim()).length}
          </p>
        </div>
      )}
    </div>
  )
}
