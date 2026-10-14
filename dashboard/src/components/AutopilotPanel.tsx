import { memo } from 'react'

import type { Slice } from '../hooks/useLiveData'
import { formatNyClock, formatNyTime, toNumber } from '../format'
import type {
  ManagerCurrentPlanView,
  ManagerPlanHistoryView,
  ManagerPlanSummary,
  ManagerSpend,
  ManagerSpendView,
  ManagerStatus,
  ManagerStatusView,
  ManagerUnavailable,
  ManagerVerdict,
  ManagerVerdictsView,
} from '../types'

/**
 * The AI day manager, read-only. The switch is set from Telegram and the CLI
 * only; this panel has no buttons and sends nothing (SOUL.md: never display
 * controls for authority the backend does not expose).
 */
type AutopilotPanelProps = {
  status: Slice<ManagerStatusView>
  plan: Slice<ManagerCurrentPlanView>
  plans: Slice<ManagerPlanHistoryView>
  verdicts: Slice<ManagerVerdictsView>
  spend: Slice<ManagerSpendView>
}

type Tone = 'take' | 'ai' | 'risk' | 'rules' | 'data' | 'warn'

// Plan outcome tokens (trading.manager.constants.PLAN_OUTCOMES); unknown ones show verbatim.
const PLAN_OUTCOMES: Record<string, { label: string; tone: Tone }> = {
  valid: { label: 'Valid', tone: 'take' },
  ai_timeout: { label: 'AI timed out', tone: 'ai' },
  ai_malformed: { label: 'AI malformed', tone: 'ai' },
  ai_refusal: { label: 'AI refused', tone: 'ai' },
  ai_truncated: { label: 'AI truncated', tone: 'ai' },
  ai_unavailable: { label: 'AI unavailable', tone: 'ai' },
  ai_order_field_echo: { label: 'AI echoed order fields', tone: 'risk' },
  ai_request_not_sent: { label: 'Not sent', tone: 'data' },
  ai_api_error: { label: 'API error', tone: 'ai' },
  budget_exhausted: { label: 'Budget spent', tone: 'risk' },
  context_unavailable: { label: 'No context', tone: 'data' },
  spend_unavailable: { label: 'Spend ledger unavailable', tone: 'data' },
}

const NEUTRAL_REASONS: Record<string, string> = {
  plan_missing: 'no plan written yet',
  plan_expired: 'the last plan expired',
  plan_error: 'the last plan could not be read',
  switch_off: 'the switch is off',
  switch_off_midday: 'the switch was turned off today',
}

const REGIMES: Record<string, string> = {
  trend_up: 'Trend up',
  trend_down: 'Trend down',
  range: 'Range',
  high_vol: 'High vol',
  unclear: 'Unclear',
}

export function describePlanOutcome(outcome: string): { label: string; tone: Tone } {
  const key = outcome.trim().toLowerCase()
  return PLAN_OUTCOMES[key] ?? { label: key.replace(/_/g, ' ') || 'unknown', tone: 'warn' }
}

export function describeRegime(regime: string | null): string {
  if (regime === null) return '—'
  return REGIMES[regime] ?? regime.replace(/_/g, ' ')
}

export function describeNeutralReason(reason: string | null): string {
  if (reason === null) return 'neutral'
  return NEUTRAL_REASONS[reason] ?? reason.replace(/_/g, ' ')
}

/** "on", "on ×0.5" or "OFF" for a day step; "TAKE", "TAKE ×0.5" or "SKIP" for a setup step. */
export function describeStep(step: string, kind: 'day' | 'setup'): { label: string; tone: Tone } {
  const value = toNumber(step)
  if (value === null) return { label: step, tone: 'warn' }
  if (value <= 0) return kind === 'day' ? { label: 'OFF', tone: 'risk' } : { label: 'SKIP', tone: 'ai' }
  const word = kind === 'day' ? 'on' : 'TAKE'
  return { label: value >= 1 ? word : `${word} ×${step}`, tone: 'take' }
}

/** "$0.62 of $5 · 12%" in whole cents, never more precise than the owner reads it. */
export function describeSpend(spend: ManagerSpend): string {
  const spent = toNumber(spend.committed_usd) ?? 0
  const budget = toNumber(spend.budget_usd) ?? 0
  const pct = Math.round(spend.used_pct)
  return `$${spent.toFixed(2)} of $${budget.toFixed(2)} · ${pct}%`
}

export function spendTone(spend: ManagerSpend): 'ok' | 'warn' | 'danger' {
  if (spend.exhausted) return 'danger'
  return spend.used_pct >= 80 ? 'warn' : 'ok'
}

function seconds(ms: number | null): string {
  return ms === null ? '—' : `${(ms / 1000).toFixed(1)} s`
}

function cost(value: string): string {
  const amount = toNumber(value)
  return amount === null ? '—' : `$${amount.toFixed(2)}`
}

function unavailableNotice(view: ManagerUnavailable): string {
  const why = view.reason === 'schema_migration_pending' ? 'database migration pending' : view.reason
  return `Autopilot data unavailable: ${why}${view.detail ? ` (${view.detail})` : ''}`
}

function firstUnavailable(...views: Array<{ available: boolean } | null>): ManagerUnavailable | null {
  for (const view of views) {
    if (view && view.available === false) return view as ManagerUnavailable
  }
  return null
}

function SwitchBadge({ status }: { status: ManagerStatus }) {
  const { switch: sw, latch } = status
  const on = sw.state === 'on'
  const via = sw.source === 'telegram' ? 'Telegram' : sw.source === 'cli' ? 'autotraderctl' : null
  const since = sw.since ? `since ${formatNyClock(sw.since)} ET` : null
  const detail = [since, via ? `via ${via}` : null, sw.reason].filter(Boolean).join(' · ')
  const today = latch
    ? latch.managed
      ? `Today's managed MOMO follows the plan (latched ${formatNyClock(latch.latched_at)} ET)`
      : `Today's managed MOMO runs neutral, exactly as MOMO_HOD_V2 (latched ${formatNyClock(latch.latched_at)} ET)`
    : 'Managed MOMO not latched yet today'
  return (
    <div className="autopilot-switch" aria-label="Autopilot switch">
      <span className={`badge switch-badge switch-${on ? 'on' : 'off'}`}>{on ? 'Autopilot ON' : 'Autopilot OFF'}</span>
      <small className="muted">{detail || 'never switched'}</small>
      <small className="muted">{today}</small>
    </div>
  )
}

function SpendMeter({ spend }: { spend: ManagerSpend }) {
  const tone = spendTone(spend)
  const text = describeSpend(spend)
  return (
    <div className={`spend-meter spend-${tone}`} aria-label="Plan spend today">
      <div className="meter-line">
        <span className="label">Plan spend</span>
        <span className="mono">{text}</span>
      </div>
      <div
        className="meter-track"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.min(100, Math.max(0, spend.used_pct))}
        aria-valuetext={text}
      >
        <div className="meter-fill" style={{ width: `${Math.min(100, Math.max(0, spend.used_pct))}%` }} />
      </div>
      <small className="muted">
        {spend.calls} call{spend.calls === 1 ? '' : 's'}
        {spend.exhausted ? ' · budget spent: no new plans today' : ` · $${(toNumber(spend.remaining_usd) ?? 0).toFixed(2)} left`}
      </small>
    </div>
  )
}

function PlanInForce({ view }: { view: ManagerCurrentPlanView }) {
  if (!view.available) return null
  if (!view.in_force || view.plan === null) {
    const last = view.last_attempt
    return (
      <div className="plan-none">
        <p className="signal-reason">
          No plan in force: managed strategies run neutral ({describeNeutralReason(view.neutral_reason)}).
        </p>
        {last ? (
          <p className="signal-plan">
            Last attempt {last.slot} ET #{last.attempt}: {describePlanOutcome(last.outcome).label}
            {last.valid_until ? ` · was valid until ${formatNyClock(last.valid_until)} ET` : ''}
          </p>
        ) : null}
      </div>
    )
  }
  const plan = view.plan
  const held = Object.entries(plan.gates)
    .filter(([, value]) => value)
    .map(([name]) => name.replace(/_/g, ' '))
  return (
    <div className="plan-current">
      <div className="signal-line">
        <span className={`badge regime-badge regime-plan`}>{describeRegime(plan.regime)}</span>
        <span className="mono muted">
          {plan.slot} ET #{plan.attempt}
        </span>
        <span className="muted small">valid until {formatNyClock(plan.valid_until)} ET</span>
        {held.length ? <span className="badge badge-risk">held off: {held.join(', ')}</span> : null}
      </div>
      <ul className="plan-strategies" aria-label="Strategy steps">
        {plan.strategies.map((item) => {
          const step = describeStep(item.step, 'day')
          return (
            <li key={item.strategy}>
              <span className={`strategy-tag strategy-${item.strategy}`}>{item.strategy.toUpperCase()}</span>
              <span className={`badge badge-${step.tone}`}>{step.label}</span>
              <span className="signal-reason muted">{item.reason}</span>
            </li>
          )
        })}
      </ul>
      {plan.focus.length ? (
        <ol className="plan-focus" aria-label="Focus list">
          {plan.focus.map((entry) => (
            <li key={entry.con_id}>
              <strong className="symbol">{entry.symbol ?? `con ${entry.con_id}`}</strong>
              <span className="muted small">{entry.strategies.join(', ')}</span>
              <span className="signal-reason">{entry.note}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="signal-plan">Focus: none (managed strategies take no new entries).</p>
      )}
      {plan.dropped.length ? (
        <p className="signal-plan">
          Narrow-only gate dropped {plan.dropped.length} {plan.dropped.length === 1 ? 'entry' : 'entries'}
          {plan.dropped.some((item) => item.widening) ? ' (widening attempt)' : ''}:{' '}
          {plan.dropped.map((item) => `${item.kind} ${item.token}${item.detail ? ` ${item.detail}` : ''}`).join('; ')}
        </p>
      ) : null}
    </div>
  )
}

/** The feed's left-edge accent: only take, risk and warn have one. */
function rowTone(tone: Tone): 'take' | 'risk' | 'warn' | 'rules' {
  return tone === 'take' || tone === 'risk' || tone === 'warn' ? tone : 'rules'
}

function HistoryRow({ plan }: { plan: ManagerPlanSummary }) {
  const outcome = describePlanOutcome(plan.outcome)
  return (
    <li className={`order tone-${rowTone(outcome.tone)}`}>
      <span className="signal-line">
        <span className="mono muted">{formatNyTime(plan.created_at)}</span>
        <span className="mono">
          {plan.slot} #{plan.attempt}
        </span>
        <span className={`badge badge-${outcome.tone}`}>{outcome.label}</span>
        {plan.in_force ? <span className="badge badge-take">in force</span> : null}
        {plan.regime ? <span className="muted small">{describeRegime(plan.regime)}</span> : null}
      </span>
      <span className="signal-plan mono">
        {plan.focus_count === null ? 'no focus' : `focus ${plan.focus_count}`}
        {plan.dropped_count ? ` · dropped ${plan.dropped_count}` : ''} · {seconds(plan.latency_ms)} · {cost(plan.cost_usd)}
        {plan.valid_until ? ` · until ${formatNyClock(plan.valid_until)} ET` : ''}
      </span>
    </li>
  )
}

function VerdictRow({ verdict }: { verdict: ManagerVerdict }) {
  const step = verdict.mode === 'neutral' ? { label: 'neutral', tone: 'rules' as Tone } : describeStep(verdict.step, 'setup')
  const levels =
    verdict.entry_price || verdict.stop_price || verdict.target_price
      ? `entry ${verdict.entry_price ?? '—'} · stop ${verdict.stop_price ?? '—'} · target ${verdict.target_price ?? '—'}`
      : null
  return (
    <li className={`order tone-${step.tone === 'ai' ? 'warn' : rowTone(step.tone)}`}>
      <span className="signal-line">
        <span className="mono muted">{formatNyTime(verdict.created_at)}</span>
        {verdict.strategy ? <span className="strategy-tag">{verdict.strategy}</span> : null}
        <strong className="symbol">{verdict.symbol ?? 'setup pending'}</strong>
        <span className={`badge badge-${step.tone}`}>{step.label}</span>
      </span>
      {verdict.note ? <span className="signal-reason">{verdict.note}</span> : null}
      {levels ? <span className="signal-plan mono">{levels}</span> : null}
    </li>
  )
}

function AutopilotPanelView({ status, plan, plans, verdicts, spend }: AutopilotPanelProps) {
  const problems = [status.error, plan.error, plans.error, verdicts.error, spend.error].filter(
    (problem): problem is string => Boolean(problem),
  )
  const unavailable = firstUnavailable(status.data, plan.data, plans.data, verdicts.data, spend.data)
  const current = status.data?.available ? status.data : null
  const login = current?.competing_login
  const spendData = spend.data?.available ? spend.data : current?.spend ?? null
  const history = plans.data?.available ? plans.data.plans : []
  const listed = verdicts.data?.available ? verdicts.data.verdicts : []
  const loading = status.data === null && !status.error

  return (
    <>
      <div className="panel-head">
        <div>
          <h2>Autopilot</h2>
          <p className="panel-sub">AI day manager, shadow only · switched from Telegram, never from here (read-only)</p>
        </div>
        {current ? (
          <dl className="status-counts autopilot-counts" aria-label="Autopilot counts">
            <div>
              <dt>Plans</dt>
              <dd>
                {current.plans.valid}/{current.plans.attempts}
              </dd>
            </div>
            <div>
              <dt>Day universe</dt>
              <dd title={`${current.day_universe.seen} names seen today`}>{current.day_universe.qualified}</dd>
            </div>
            <div className={current.migrations.current ? '' : 'attention'}>
              <dt>Schema</dt>
              <dd>v{current.migrations.applied_version}</dd>
            </div>
          </dl>
        ) : null}
      </div>
      {login?.active ? (
        <p className="notice warn" role="alert">
          IBKR login active elsewhere (or TWS connection lost) at {formatNyClock(login.last_alert_at)} ET: log out of the IBKR
          app/website, or the bot sees no bars.
        </p>
      ) : null}
      {unavailable ? <p className="notice warn">{unavailableNotice(unavailable)}</p> : null}
      {problems.length ? (
        <p className="inline-error" role="alert">
          Autopilot: {[...new Set(problems)].join(' · ')}
        </p>
      ) : null}
      {loading ? <p className="empty">Loading autopilot…</p> : null}
      {current ? (
        <div className="autopilot-body">
          <section className="autopilot-column" aria-label="Autopilot switch and spend">
            <SwitchBadge status={current} />
            {spendData ? <SpendMeter spend={spendData} /> : null}
          </section>
          <section className="autopilot-column" aria-label="Plan in force">
            <h3 className="eyebrow">Plan in force</h3>
            {plan.data ? <PlanInForce view={plan.data} /> : <p className="empty">Loading plan…</p>}
          </section>
          <section className="autopilot-column" aria-label="Plan history and verdicts">
            <h3 className="eyebrow">Plans today</h3>
            {history.length ? (
              <ol className="feed compact" aria-label="Plan history">
                {history.map((item) => (
                  <HistoryRow key={item.plan_id} plan={item} />
                ))}
              </ol>
            ) : (
              <p className="empty">No plan attempts yet today.</p>
            )}
            <h3 className="eyebrow">Last verdicts</h3>
            {listed.length ? (
              <ol className="feed compact" aria-label="Verdicts">
                {listed.map((item) => (
                  <VerdictRow key={item.id} verdict={item} />
                ))}
              </ol>
            ) : (
              <p className="empty">No managed verdicts yet today.</p>
            )}
          </section>
        </div>
      ) : null}
    </>
  )
}

export const AutopilotPanel = memo(AutopilotPanelView)
