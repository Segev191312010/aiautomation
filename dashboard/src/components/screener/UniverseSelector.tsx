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
    <div className="space-y-3">
      <div className="flex gap-2 flex-wrap">
        {UNIVERSES.map((u) => {
          const active = selectedUniverse === u.id
          const count = getCount(u.id)
          return (
            <button
              key={u.id}
              onClick={() => setUniverse(u.id)}
              className={clsx(
                'px-3.5 py-1.5 rounded-xl text-xs font-sans font-medium transition-colors',
                active
                  ? 'bg-indigo-500/15 text-indigo-400 border border-indigo-500/25'
                  : 'bg-white/[0.04] text-terminal-dim border border-white/[0.06] hover:text-terminal-text hover:bg-white/[0.07]',
              )}
            >
              {u.label}
              {count !== null && (
                <span className="ml-1.5 text-[10px] font-mono opacity-60">({count})</span>
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
          className="w-full px-3.5 py-2 bg-terminal-input border border-white/[0.06] rounded-xl text-xs font-mono text-terminal-text placeholder-terminal-ghost focus:border-indigo-500/50 focus:outline-none transition-colors"
        />
      )}
    </div>
  )
}
