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
      <label className="text-xs text-gray-400 block mb-1">{label}</label>
      <input
        type="number"
        className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        min={min}
        max={max}
        step={step}
      />
      {help && <span className="text-[10px] text-gray-600 mt-0.5 block">{help}</span>}
    </div>
  )
}

export function BacktestParams() {
  const {
    initialCapital, positionSizePct, stopLossPct, takeProfitPct,
    setInitialCapital, setPositionSizePct, setStopLossPct, setTakeProfitPct,
  } = useBacktestStore()

  return (
    <div>
      <h3 className="text-sm font-semibold text-gray-200 mb-3">Parameters</h3>
      <div className="grid grid-cols-2 gap-3">
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
