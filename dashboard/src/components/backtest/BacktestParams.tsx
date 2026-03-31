import { useBacktestStore } from '@/store'
import type { ExitMode } from '@/types'

interface ParamInputProps {
  label: string
  icon: React.ReactNode
  value: number
  onChange: (v: number) => void
  help?: string
  min?: number
  max?: number
  step?: number
  isPct?: boolean
  prefix?: string
}

function ParamInput({ label, icon, value, onChange, help, min = 0, max, step = 1, isPct, prefix }: ParamInputProps) {
  // For percentage inputs, compute fill width for the slider-bar visual
  const fillPct = (isPct && max != null)
    ? Math.min(100, Math.max(0, ((value - (min ?? 0)) / (max - (min ?? 0))) * 100))
    : null

  return (
    <div className="bg-zinc-900/60 rounded-xl border border-zinc-800 p-3.5 hover:border-zinc-800 transition-colors group">
      {/* Label row */}
      <div className="flex items-center gap-2 mb-3">
        <div className="w-6 h-6 rounded-md bg-zinc-900 flex items-center justify-center text-zinc-400 group-hover:text-zinc-100 transition-colors flex-shrink-0">
          {icon}
        </div>
        <label className="text-xs font-sans font-medium text-zinc-400 group-hover:text-zinc-100 transition-colors">
          {label}
        </label>
      </div>

      {/* Input */}
      <div className="relative">
        {prefix && (
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm font-mono text-zinc-500 pointer-events-none select-none">
            {prefix}
          </span>
        )}
        <input
          type="number"
          className={`w-full bg-zinc-900 border border-zinc-800 rounded-lg py-1.5 text-sm font-mono text-zinc-100
            focus:outline-none focus:border-indigo-600/50 focus:ring-1 focus:ring-indigo-600/20
            hover:border-zinc-800 transition-all
            ${prefix ? 'pl-7 pr-3' : 'px-3'}`}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          min={min}
          max={max}
          step={step}
        />
      </div>

      {/* Slider-bar visual for percentage inputs */}
      {fillPct !== null && (
        <div className="mt-2.5 h-1 bg-zinc-900 rounded-full overflow-hidden">
          <div
            className="h-full rounded-full bg-gradient-to-r from-indigo-600/60 to-indigo-600 transition-all duration-300"
            style={{ width: `${fillPct}%` }}
          />
        </div>
      )}

      {/* Help text */}
      {help && (
        <span className="text-[10px] font-sans text-zinc-500 mt-1.5 block">{help}</span>
      )}
    </div>
  )
}

const IconCapital = () => (
  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
      d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
    />
  </svg>
)

const IconPosition = () => (
  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
      d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
    />
  </svg>
)

const IconStopLoss = () => (
  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
      d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"
    />
  </svg>
)

const IconTakeProfit = () => (
  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
      d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"
    />
  </svg>
)

export function BacktestParams() {
  const {
    initialCapital, positionSizePct, stopLossPct, takeProfitPct,
    exitMode, atrStopMult, atrTrailMult,
    setInitialCapital, setPositionSizePct, setStopLossPct, setTakeProfitPct,
    setExitMode, setAtrStopMult, setAtrTrailMult,
  } = useBacktestStore()

  return (
    <div className="card rounded-2xl  p-5">
      {/* Header */}
      <div className="flex items-center gap-2 mb-4">
        <div className="w-7 h-7 rounded-lg bg-indigo-50 flex items-center justify-center">
          <svg className="w-3.5 h-3.5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4"
            />
          </svg>
        </div>
        <h3 className="text-sm font-sans font-semibold text-zinc-100">Parameters</h3>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <ParamInput
          label="Initial Capital"
          icon={<IconCapital />}
          value={initialCapital}
          onChange={setInitialCapital}
          min={1000}
          step={1000}
          prefix="$"
          help="Starting portfolio value"
        />
        <ParamInput
          label="Position Size"
          icon={<IconPosition />}
          value={positionSizePct}
          onChange={setPositionSizePct}
          help="% of equity per trade"
          min={1}
          max={100}
          isPct
        />
      </div>

      {/* Exit Mode Toggle */}
      <div className="mt-3 bg-zinc-900/60 rounded-xl border border-zinc-800 p-3.5">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-6 h-6 rounded-md bg-zinc-900 flex items-center justify-center text-zinc-400">
            <IconStopLoss />
          </div>
          <label className="text-xs font-sans font-medium text-zinc-400">Exit Mode</label>
        </div>
        <div className="flex gap-1 bg-zinc-900 rounded-lg p-0.5">
          {(['simple', 'atr_trail'] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => setExitMode(mode)}
              className={`flex-1 py-1.5 px-3 text-xs font-mono rounded-md transition-all ${
                exitMode === mode
                  ? 'bg-indigo-600 text-white'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              {mode === 'simple' ? '% SL/TP' : 'ATR Trail'}
            </button>
          ))}
        </div>
        <span className="text-[10px] font-sans text-zinc-500 mt-1.5 block">
          {exitMode === 'simple'
            ? 'Fixed percentage stop-loss and take-profit'
            : 'ATR-based stops matching live bot behavior'}
        </span>
      </div>

      {/* Conditional params based on exit mode */}
      <div className="grid grid-cols-2 gap-3 mt-3">
        {exitMode === 'simple' ? (
          <>
            <ParamInput
              label="Stop Loss"
              icon={<IconStopLoss />}
              value={stopLossPct}
              onChange={setStopLossPct}
              help="0 = disabled"
              min={0}
              max={50}
              step={0.5}
              isPct
            />
            <ParamInput
              label="Take Profit"
              icon={<IconTakeProfit />}
              value={takeProfitPct}
              onChange={setTakeProfitPct}
              help="0 = disabled"
              min={0}
              max={100}
              step={0.5}
              isPct
            />
          </>
        ) : (
          <>
            <ParamInput
              label="ATR Stop Mult"
              icon={<IconStopLoss />}
              value={atrStopMult}
              onChange={setAtrStopMult}
              help="Hard stop: entry - N x ATR"
              min={0.5}
              max={10}
              step={0.1}
            />
            <ParamInput
              label="ATR Trail Mult"
              icon={<IconTakeProfit />}
              value={atrTrailMult}
              onChange={setAtrTrailMult}
              help="Trail: watermark - N x ATR"
              min={0.5}
              max={10}
              step={0.1}
            />
          </>
        )}
      </div>
    </div>
  )
}
