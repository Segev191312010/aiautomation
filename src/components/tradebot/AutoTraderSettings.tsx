/**
 * AutoTraderSettings — visible only in MOCK / SIM mode, since this is
 * what configures the client-side AutoTrader engine. In live mode the
 * backend owns the rule configuration.
 */
import React, { useState } from 'react'
import { autoTrader, type AutoTraderConfig } from '@/services/autoTrader'
import { useBotStore } from '@/store'

type NumericKey = {
  [K in keyof AutoTraderConfig]: AutoTraderConfig[K] extends number ? K : never
}[keyof AutoTraderConfig]

interface FieldDef {
  key:    NumericKey
  label:  string
  step:   number
  min?:   number
  max?:   number
  suffix?: string
  format?: (v: number) => string
}

const PCT = (v: number) => `${(v * 100).toFixed(2)}%`

const FIELDS: FieldDef[] = [
  { key: 'entryFraction',    label: 'Entry size (of cash)', step: 0.01, min: 0.01, max: 0.5, format: PCT },
  { key: 'stopLossPct',      label: 'Stop loss',            step: 0.005, min: 0.001, max: 0.5, format: PCT },
  { key: 'takeProfitPct',    label: 'Take profit',          step: 0.005, min: 0.001, max: 1.0, format: PCT },
  { key: 'trailingStopPct',  label: 'Trailing stop',        step: 0.005, min: 0.001, max: 0.5, format: PCT },
  { key: 'maxHoldTicks',     label: 'Max hold (ticks)',     step: 1,    min: 1,     max: 10_000 },
  { key: 'maxOpenPositions', label: 'Max open positions',   step: 1,    min: 1,     max: 50 },
  { key: 'rsiOverbought',    label: 'RSI overbought',       step: 1,    min: 50,    max: 95 },
  { key: 'shortWindow',      label: 'SMA short window',     step: 1,    min: 2,     max: 50 },
  { key: 'longWindow',       label: 'SMA long window',      step: 1,    min: 5,     max: 200 },
]

export default function AutoTraderSettings() {
  const { mockMode, simMode, botRunning } = useBotStore()
  const [config, setConfig] = useState<AutoTraderConfig>(() => autoTrader.getConfig())

  if (!mockMode && !simMode) return null

  const update = (key: NumericKey, value: number) => {
    if (!Number.isFinite(value)) return
    const next = { ...config, [key]: value }
    setConfig(next)
    autoTrader.setConfig({ [key]: value } as Partial<AutoTraderConfig>)
  }

  return (
    <div className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
      <div className="flex items-center gap-2 mb-3">
        <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">
          Auto-Trader Rules
        </h2>
        <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-terminal-amber/15 text-terminal-amber">
          {botRunning ? 'ACTIVE' : 'IDLE'}
        </span>
      </div>
      <p className="text-[11px] font-mono text-terminal-dim mb-3">
        Long-only SMA crossover entry, exits on stop-loss / take-profit / trailing-stop / max-hold.
      </p>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {FIELDS.map((f) => (
          <label key={f.key} className="flex flex-col gap-1">
            <span className="text-[10px] font-mono text-terminal-ghost uppercase">
              {f.label}
              {f.format && (
                <span className="ml-1 text-terminal-amber">
                  {f.format(config[f.key])}
                </span>
              )}
            </span>
            <input
              type="number"
              value={config[f.key]}
              step={f.step}
              min={f.min}
              max={f.max}
              onChange={(e) => update(f.key, Number(e.target.value))}
              className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1 text-terminal-text focus:border-terminal-blue focus:outline-none"
            />
          </label>
        ))}
      </div>
    </div>
  )
}
