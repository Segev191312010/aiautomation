import { memo, useMemo, useState } from 'react'

import { describeOutcome, formatNyTime } from '../format'
import type { SelectedSymbol, Signal } from '../types'

type SignalsFeedProps = {
  signals: Signal[] | null
  error: string | null
  date: string
  today: string | null
  onDateChange: (value: string) => void
  selectedSymbol: string | null
  onSelect: (selection: SelectedSymbol) => void
}

function SignalsFeedView({ signals, error, date, today, onDateChange, selectedSymbol, onSelect }: SignalsFeedProps) {
  const [hideRuleRejections, setHideRuleRejections] = useState(false)
  const visible = useMemo(
    () => (signals ?? []).filter((signal) => !hideRuleRejections || signal.outcome !== 'rule_rejected'),
    [signals, hideRuleRejections],
  )
  const ruleRejections = (signals ?? []).filter((signal) => signal.outcome === 'rule_rejected').length
  const shownDate = date || today || ''

  return (
    <>
      <div className="panel-head">
        <div>
          <h2>Signals</h2>
          <p className="panel-sub">Every decision the bot made, newest first</p>
        </div>
        <div className="panel-tools">
          <input
            type="date"
            aria-label="Signals date"
            value={shownDate}
            max={today ?? undefined}
            onChange={(event) => onDateChange(event.target.value === today ? '' : event.target.value)}
          />
          {date ? (
            <button type="button" className="ghost-button" onClick={() => onDateChange('')}>
              Today
            </button>
          ) : null}
          <label className="toggle">
            <input
              type="checkbox"
              checked={hideRuleRejections}
              onChange={(event) => setHideRuleRejections(event.target.checked)}
            />
            Hide rule rejections ({ruleRejections})
          </label>
        </div>
      </div>
      {error ? (
        <p className="inline-error" role="alert">
          Signals: {error}
        </p>
      ) : null}
      <ol className="feed" aria-label="Signals">
        {visible.map((signal) => {
          const outcome = describeOutcome(signal.outcome)
          const selected = signal.symbol === selectedSymbol
          return (
            <li key={signal.id}>
              <button
                type="button"
                className={`signal tone-${outcome.tone}${selected ? ' selected' : ''}`}
                onClick={() => onSelect({ symbol: signal.symbol, exchange: signal.primary_exchange })}
                title={signal.ai?.rationale ?? signal.reason}
              >
                <span className="signal-line">
                  <span className="mono muted">{formatNyTime(signal.created_at)}</span>
                  <span className={`strategy-tag strategy-${signal.strategy}`}>{signal.strategy.toUpperCase()}</span>
                  <strong className="symbol">{signal.symbol}</strong>
                  <span className={`badge badge-${outcome.tone}`}>{outcome.label}</span>
                  {signal.ai?.confidence ? (
                    <span className="muted small">conf {signal.ai.confidence}</span>
                  ) : null}
                </span>
                <span className="signal-reason">{signal.reason}</span>
                {signal.intent ? (
                  <span className="signal-plan mono">
                    stop {signal.intent.stop_price} · target {signal.intent.target_price}
                  </span>
                ) : null}
              </button>
            </li>
          )
        })}
      </ol>
      {visible.length === 0 ? (
        <p className="empty">
          {signals === null
            ? 'Loading signals…'
            : (signals.length === 0
              ? `No decisions recorded for ${shownDate || 'today'} yet.`
              : 'Only rule rejections so far.')}
        </p>
      ) : null}
    </>
  )
}

export const SignalsFeed = memo(SignalsFeedView)
