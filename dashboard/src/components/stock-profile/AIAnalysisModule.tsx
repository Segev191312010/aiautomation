/**
 * AI Analysis Module — Bull/Bear Debate + Multi-Persona Analysis
 *
 * Inspired by:
 * - TradingAgents: adversarial debate between bull and bear perspectives
 * - ai-hedge-fund: multiple investment philosophy personas analyzing a stock
 */
import { useState } from 'react'
import {
  runBullBearDebate,
  runPersonaAnalysis,
  type DebateResult,
  type PersonaAnalysisResult,
} from '@/services/api'

interface Props {
  symbol: string
  price?: number
  sector?: string
}

const PERSONA_LABELS: Record<string, { label: string; color: string }> = {
  momentum: { label: 'Momentum', color: 'text-blue-400' },
  value: { label: 'Value', color: 'text-green-400' },
  growth: { label: 'Growth', color: 'text-purple-400' },
  risk: { label: 'Risk Mgr', color: 'text-red-400' },
}

function ConvictionBar({ value, max = 1 }: { value: number; max?: number }) {
  const pct = Math.abs(value / max) * 100
  const isPositive = value >= 0
  return (
    <div className="flex items-center gap-2 w-full">
      <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${isPositive ? 'bg-emerald-500' : 'bg-red-500'}`}
          style={{ width: `${Math.min(100, pct)}%` }}
        />
      </div>
      <span className="text-[10px] font-mono w-8 text-right text-zinc-400">
        {value >= 0 ? '+' : ''}{(value * 100).toFixed(0)}
      </span>
    </div>
  )
}

export default function AIAnalysisModule({ symbol, price, sector }: Props) {
  const [debate, setDebate] = useState<DebateResult | null>(null)
  const [personas, setPersonas] = useState<PersonaAnalysisResult | null>(null)
  const [loading, setLoading] = useState<'debate' | 'persona' | null>(null)
  const [error, setError] = useState('')

  async function handleDebate() {
    setLoading('debate')
    setError('')
    try {
      const result = await runBullBearDebate({
        symbol,
        price: price ?? 0,
        sector: sector ?? 'Unknown',
      })
      setDebate(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Debate failed')
    } finally {
      setLoading(null)
    }
  }

  async function handlePersona() {
    setLoading('persona')
    setError('')
    try {
      const result = await runPersonaAnalysis({
        symbol,
        price: price ?? 0,
        sector: sector ?? 'Unknown',
      })
      setPersonas(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Analysis failed')
    } finally {
      setLoading(null)
    }
  }

  return (
    <div className="card rounded-lg p-5 space-y-4" id="ai-analysis">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[10px] font-sans uppercase tracking-[0.22em] text-zinc-500">
            AI-Powered
          </div>
          <h2 className="text-base font-sans font-semibold text-zinc-100">
            Multi-Perspective Analysis
          </h2>
          <p className="text-[11px] text-zinc-500 mt-0.5">
            Bull/Bear debate and multi-persona scoring for {symbol}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void handleDebate()}
            disabled={loading !== null}
            className="text-[11px] font-sans font-semibold px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-200 transition-colors disabled:opacity-50"
          >
            {loading === 'debate' ? 'Debating...' : 'Run Debate'}
          </button>
          <button
            type="button"
            onClick={() => void handlePersona()}
            disabled={loading !== null}
            className="text-[11px] font-sans font-semibold px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-200 transition-colors disabled:opacity-50"
          >
            {loading === 'persona' ? 'Analyzing...' : 'Run Personas'}
          </button>
        </div>
      </div>

      {error && (
        <p className="text-xs text-red-400">{error}</p>
      )}

      {/* Bull/Bear Debate Results */}
      {debate && (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold text-zinc-300">Bull vs Bear Debate</h3>
            <span
              className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-full ${
                debate.winner === 'BULL'
                  ? 'bg-emerald-500/20 text-emerald-400'
                  : debate.winner === 'BEAR'
                    ? 'bg-red-500/20 text-red-400'
                    : 'bg-zinc-700 text-zinc-400'
              }`}
            >
              {debate.winner} {debate.should_trade ? '(tradeable)' : '(no signal)'}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-4">
            {/* Bull side */}
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-sans font-bold uppercase tracking-wider text-emerald-400">Bull</span>
                <span className="text-[10px] text-zinc-500 font-mono">{(debate.bull.conviction * 100).toFixed(0)}%</span>
              </div>
              <p className="text-[11px] text-zinc-400 leading-relaxed">{debate.bull.thesis}</p>
              {debate.bull.key_factors.length > 0 && (
                <ul className="space-y-0.5">
                  {debate.bull.key_factors.map((f, i) => (
                    <li key={i} className="text-[10px] text-emerald-400/70 flex items-start gap-1">
                      <span className="mt-0.5 shrink-0">+</span>
                      <span>{f}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* Bear side */}
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-sans font-bold uppercase tracking-wider text-red-400">Bear</span>
                <span className="text-[10px] text-zinc-500 font-mono">{(debate.bear.conviction * 100).toFixed(0)}%</span>
              </div>
              <p className="text-[11px] text-zinc-400 leading-relaxed">{debate.bear.thesis}</p>
              {debate.bear.key_factors.length > 0 && (
                <ul className="space-y-0.5">
                  {debate.bear.key_factors.map((f, i) => (
                    <li key={i} className="text-[10px] text-red-400/70 flex items-start gap-1">
                      <span className="mt-0.5 shrink-0">-</span>
                      <span>{f}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div className="pt-2 border-t border-zinc-800">
            <div className="flex items-center justify-between text-[10px] text-zinc-500">
              <span>Net conviction</span>
              <span className={debate.net_conviction > 0 ? 'text-emerald-400' : debate.net_conviction < 0 ? 'text-red-400' : ''}>
                {debate.net_conviction > 0 ? '+' : ''}{(debate.net_conviction * 100).toFixed(0)}%
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Multi-Persona Analysis Results */}
      {personas && (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold text-zinc-300">Multi-Persona Verdict</h3>
            <span
              className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-full ${
                personas.verdict === 'BULLISH'
                  ? 'bg-emerald-500/20 text-emerald-400'
                  : personas.verdict === 'BEARISH'
                    ? 'bg-red-500/20 text-red-400'
                    : 'bg-zinc-700 text-zinc-400'
              }`}
            >
              {personas.verdict} ({(personas.composite_score * 100).toFixed(0)})
            </span>
          </div>

          <div className="space-y-2.5">
            {Object.entries(personas.personas).map(([key, p]) => {
              const meta = PERSONA_LABELS[key] ?? { label: key, color: 'text-zinc-400' }
              return (
                <div key={key} className="space-y-1">
                  <div className="flex items-center justify-between">
                    <span className={`text-[10px] font-sans font-bold uppercase tracking-wider ${meta.color}`}>
                      {meta.label}
                      <span className="text-zinc-600 ml-1 font-normal lowercase">
                        ({(p.weight * 100).toFixed(0)}% wt)
                      </span>
                    </span>
                  </div>
                  <ConvictionBar value={p.score} />
                  {p.key_insight && (
                    <p className="text-[10px] text-zinc-500 italic">{p.key_insight}</p>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Empty state */}
      {!debate && !personas && !loading && (
        <div className="text-center py-6 text-zinc-600 text-xs">
          Click "Run Debate" for adversarial bull/bear analysis or "Run Personas" for multi-perspective scoring.
        </div>
      )}
    </div>
  )
}
