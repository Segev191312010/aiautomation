import { useBacktestStore } from '@/store'

interface ParamInputProps {
  label: string
  value: number
  onChange: (v: number) => void
  help?: string
  min?: number
  max?: number
  step?: number
}

function ParamInput({ label, value, onChange, help, min = 0, max, step = 1 }: ParamInputProps) {
  return (
    <div>
      <label className="text-xs font-sans font-medium text-terminal-dim block mb-1.5">{label}</label>
      <input
        type="number"
        className="w-full bg-terminal-input border border-white/[0.06] rounded-xl px-3 py-1.5 text-sm font-mono text-terminal-text focus:outline-none focus:border-terminal-blue/40 focus:ring-1 focus:ring-terminal-blue/20 transition-colors"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        min={min}
        max={max}
        step={step}
      />
      {help && <span className="text-[10px] font-sans text-terminal-ghost mt-1 block">{help}</span>}
    </div>
  )
}

export function BacktestParams() {
  const {
    initialCapital, positionSizePct, stopLossPct, takeProfitPct,
    setInitialCapital, setPositionSizePct, setStopLossPct, setTakeProfitPct,
  } = useBacktestStore()

  return (
    <div className="glass rounded-2xl shadow-glass p-5">
      <h3 className="text-sm font-sans font-medium text-terminal-text mb-4">Parameters</h3>
      <div className="grid grid-cols-2 gap-4">
        <ParamInput
          label="Initial Capital ($)"
          value={initialCapital}
          onChange={setInitialCapital}
          min={1000}
          step={1000}
        />
        <ParamInput
          label="Position Size (%)"
          value={positionSizePct}
          onChange={setPositionSizePct}
          help="% of equity per trade"
          min={1}
          max={100}
        />
        <ParamInput
          label="Stop Loss (%)"
          value={stopLossPct}
          onChange={setStopLossPct}
          help="0 = disabled"
          min={0}
          max={50}
          step={0.5}
        />
        <ParamInput
          label="Take Profit (%)"
          value={takeProfitPct}
          onChange={setTakeProfitPct}
          help="0 = disabled"
          min={0}
          max={100}
          step={0.5}
        />
      </div>
    </div>
  )
}
