/**
 * IndicatorSelector — pill buttons to toggle technical indicators on/off.
 *
 * Overlays (SMA, EMA, BB, VWAP) draw on the main chart.
 * Oscillators (RSI, MACD) appear as sub-panels below the chart.
 */
import React from 'react'
import clsx from 'clsx'
import { useMarketStore } from '@/store'
import { INDICATOR_DEFS, type IndicatorId } from '@/utils/indicators'

const OVERLAYS    = INDICATOR_DEFS.filter((d) => d.type === 'overlay')
const OSCILLATORS = INDICATOR_DEFS.filter((d) => d.type === 'oscillator')

interface PillProps {
  id:       IndicatorId
  label:    string
  color:    string
  active:   boolean
  onToggle: (id: IndicatorId) => void
}

function Pill({ id, label, color, active, onToggle }: PillProps) {
  return (
    <button
      onClick={() => onToggle(id)}
      title={active ? `Remove ${label}` : `Add ${label}`}
      className={clsx(
        'inline-flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded-full border transition-all',
        active
          ? 'border-transparent text-terminal-bg font-medium'
          : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim hover:border-terminal-muted',
      )}
      style={active ? { background: color, borderColor: color } : {}}
    >
      {active && (
        <span
          className="w-1.5 h-1.5 rounded-full bg-terminal-bg/60 flex-shrink-0"
        />
      )}
      {label}
    </button>
  )
}

export default function IndicatorSelector() {
  const selectedIndicators = useMarketStore((s) => s.selectedIndicators)
  const toggleIndicator    = useMarketStore((s) => s.toggleIndicator)

  const isActive = (id: IndicatorId) => selectedIndicators.includes(id)

  return (
    <div className="flex items-center gap-3 flex-wrap">
      {/* Overlay group */}
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] font-mono text-terminal-ghost uppercase tracking-wider mr-0.5">
          Overlay
        </span>
        {OVERLAYS.map((def) => (
          <Pill
            key={def.id}
            id={def.id}
            label={def.label}
            color={def.color}
            active={isActive(def.id)}
            onToggle={toggleIndicator}
          />
        ))}
      </div>

      {/* Divider */}
      <div className="w-px h-4 bg-terminal-border" />

      {/* Oscillator group */}
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] font-mono text-terminal-ghost uppercase tracking-wider mr-0.5">
          Oscillator
        </span>
        {OSCILLATORS.map((def) => (
          <Pill
            key={def.id}
            id={def.id}
            label={def.label}
            color={def.color}
            active={isActive(def.id)}
            onToggle={toggleIndicator}
          />
        ))}
      </div>
    </div>
  )
}
