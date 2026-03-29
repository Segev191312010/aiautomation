import clsx from 'clsx'
import type { AIStatus } from '@/types/advisor'

function StatCard({
  label,
  value,
  tone = 'default',
}: {
  label: string
  value: string
  tone?: 'default' | 'good' | 'warn' | 'danger'
}) {
  const toneClass =
    tone === 'good' ? 'text-emerald-700 bg-emerald-50 border-emerald-200' :
    tone === 'warn' ? 'text-amber-700 bg-amber-50 border-amber-200' :
    tone === 'danger' ? 'text-red-700 bg-red-50 border-red-200' :
    'text-[var(--text-primary)] bg-white border-[var(--border)]'

  return (
    <div className={clsx('rounded-xl border px-3 py-2 min-w-[120px]', toneClass)}>
      <div className="text-[10px] uppercase tracking-[0.14em] text-[var(--text-muted)]">{label}</div>
      <div className="mt-1 text-sm font-semibold">{value}</div>
    </div>
  )
}

interface Props {
  status: AIStatus | null
  onKillToggle?: () => void
  onDailyLossReset?: () => void
}

export default function AIStatusBar({ status, onKillToggle, onDailyLossReset }: Props) {
  if (!status) {
    return (
      <div className="rounded-xl border border-[var(--border)] bg-white px-4 py-3 text-sm text-[var(--text-muted)]">
        Loading autopilot status...
      </div>
    )
  }

  const modeTone =
    status.mode === 'LIVE' ? 'good' :
    status.mode === 'PAPER' ? 'warn' :
    'default'

  const botHealthTone =
    !status.bot_health ? 'default' :
    status.bot_health.stale_warning || status.bot_health.error_count_24h > 0 ? 'warn' :
    status.bot_health.is_running ? 'good' :
    'danger'

  const botHealthValue = !status.bot_health
    ? 'Monitoring Off'
    : status.bot_health.stale_warning
      ? `Stale ${status.bot_health.minutes_since_last_cycle ?? '?'}m`
      : `${status.bot_health.total_cycles_today} cycles`

  return (
    <div className="rounded-2xl border border-[var(--border)] bg-white p-4 space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Autopilot Control</h2>
          <p className="text-xs text-[var(--text-muted)]">
            AI authority, emergency stop, daily loss state, and live runtime counts.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={onKillToggle}
            className={clsx(
              'rounded-lg px-3 py-2 text-xs font-semibold transition-colors',
              status.emergency_stop
                ? 'bg-emerald-600 text-white hover:bg-emerald-700'
                : 'bg-red-600 text-white hover:bg-red-700',
            )}
          >
            {status.emergency_stop ? 'Resume Autopilot' : 'Kill Switch'}
          </button>
          {status.daily_loss_locked && (
            <button
              type="button"
              onClick={onDailyLossReset}
              className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-800 hover:bg-amber-100"
            >
              Reset Daily Lock
            </button>
          )}
        </div>
      </div>

      <div className="flex gap-3 flex-wrap">
        <StatCard label="Mode" value={status.mode} tone={modeTone} />
        <StatCard
          label="Emergency Stop"
          value={status.emergency_stop ? 'ACTIVE' : 'CLEAR'}
          tone={status.emergency_stop ? 'danger' : 'good'}
        />
        <StatCard
          label="Daily Loss"
          value={status.daily_loss_locked ? 'LOCKED' : `${status.daily_loss_limit_pct.toFixed(2)}% limit`}
          tone={status.daily_loss_locked ? 'warn' : 'default'}
        />
        <StatCard
          label="Broker"
          value={status.broker_connected ? 'Connected' : 'Offline'}
          tone={status.broker_connected ? 'good' : 'danger'}
        />
        <StatCard label="Bot Health" value={botHealthValue} tone={botHealthTone} />
        <StatCard label="Open Positions" value={String(status.open_positions_count)} />
        <StatCard label="Active Rules" value={String(status.active_rules_count)} />
        <StatCard label="Direct AI Trades" value={String(status.direct_ai_open_trades_count)} />
      </div>

      {status.bot_health && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-subtle)] px-3 py-2 text-xs text-[var(--text-secondary)]">
          Last signal: <span className="font-semibold text-[var(--text-primary)]">{status.bot_health.last_signal_symbol ?? 'None'}</span>
          {' | '}
          Errors 24h: <span className="font-semibold text-[var(--text-primary)]">{status.bot_health.error_count_24h}</span>
          {' | '}
          Degraded 24h: <span className="font-semibold text-[var(--text-primary)]">{status.bot_health.degraded_mode_count_24h}</span>
        </div>
      )}
    </div>
  )
}
