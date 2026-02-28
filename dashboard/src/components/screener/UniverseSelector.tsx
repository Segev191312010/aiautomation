import React from 'react'
import clsx from 'clsx'
import { useScreenerStore } from '@/store'

const UNIVERSES = [
  { id: 'sp500', label: 'S&P 500' },
  { id: 'nasdaq100', label: 'NASDAQ 100' },
  { id: 'etfs', label: 'ETFs' },
  { id: 'custom', label: 'Custom' },
]

export default function UniverseSelector() {
  const { selectedUniverse, setUniverse, customSymbols, setCustomSymbols, universes } = useScreenerStore()

  const getCount = (id: string) => {
    const u = universes.find((u) => u.id === id)
    return u ? u.count : null
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2 flex-wrap">
        {UNIVERSES.map((u) => {
          const active = selectedUniverse === u.id
          const count = getCount(u.id)
          return (
            <button
              key={u.id}
              onClick={() => setUniverse(u.id)}
              className={clsx(
                'px-3 py-1.5 rounded text-xs font-mono font-semibold transition-colors',
                active
                  ? 'bg-terminal-blue text-white'
                  : 'bg-terminal-muted text-terminal-dim hover:text-terminal-text hover:bg-terminal-surface',
              )}
            >
              {u.label}
              {count !== null && (
                <span className="ml-1.5 text-[10px] opacity-70">({count})</span>
              )}
            </button>
          )
        })}
      </div>
      {selectedUniverse === 'custom' && (
        <input
          type="text"
          value={customSymbols}
          onChange={(e) => setCustomSymbols(e.target.value.toUpperCase())}
          placeholder="AAPL, MSFT, TSLA, ..."
          className="w-full px-3 py-1.5 bg-terminal-bg border border-terminal-border rounded text-xs font-mono text-terminal-text placeholder-terminal-ghost focus:border-terminal-blue focus:outline-none"
        />
      )}
    </div>
  )
}
