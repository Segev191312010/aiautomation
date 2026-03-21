/**
 * AutopilotStatusStrip — Always-visible header strip showing autopilot mode,
 * kill switch, daily loss, open positions, active rules, and broker state.
 * Reads from useAdvisorStore (aiStatus). Kill switch calls emergencyStop()
 * from the store, which in turn calls postEmergencyStop() / resetEmergencyStop().
 */
import React from 'react'
import clsx from 'clsx'
import { useAdvisorStore } from '@/store'
import { resetEmergencyStop, resetDailyLossLock } from '@/services/api'

// ── Mode badge ────────────────────────────────────────────────────────────────

function ModeBadge({ mode }: { mode: 'OFF' | 'PAPER' | 'LIVE' | undefined }) {
  const config = {
    OFF:   { bg: 'bg-zinc-100 border-zinc-300 text-zinc-600', dot: 'bg-zinc-400' },
    PAPER: { bg: 'bg-amber-50 border-amber-300 text-amber-700', dot: 'bg-amber-400' },
    LIVE:  { bg: 'bg-emerald-50 border-emerald-300 text-emerald-700', dot: 'bg-emerald-500' },
  }
  const { bg, dot } = (mode && config[mode]) ?? config.OFF
  return (
    <div className={clsx('flex items-center gap-1.5 rounded-full border px-3 py-1', bg)}>
      <span className={clsx('w-2 h-2 rounded-full flex-shrink-0', dot)} />
      <span className="text-sm font-bold tracking-wide">{mode ?? 'OFF'}</span>
    </div>
  )
}

// ── Divider ───────────────────────────────────────────────────────────────────

function Divider() {
  return <div className="w-px self-stretch bg-[var(--border)]" />
}

// ── Stat item ─────────────────────────────────────────────────────────────────

interface StatItemProps {
  label: string
  value: React.ReactNode
}

function StatItem({ label, value }: StatItemProps) {
  return (
    <div className="flex flex-col gap-0.5 min-w-[60px]">
      <span className="text-[9px] font-sans font-medium uppercase tracking-widest text-[var(--text-muted)]">
        {label}
      </span>
      <span className="text-sm font-semibold font-sans text-[var(--text-primary)] leading-tight">
        {value}
      </span>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function AutopilotStatusStrip() {
  const { aiStatus, emergencyStop, fetchAIStatus, fetchGuardrails } = useAdvisorStore()

  async function handleKillToggle() {
    if (aiStatus?.emergency_stop) {
      await resetEmergencyStop()
      await Promise.all([fetchAIStatus(), fetchGuardrails()])
    } else {
      await emergencyStop()
    }
  }

  async function handleDailyLossReset() {
    await resetDailyLossLock()
    await Promise.all([fetchAIStatus(), fetchGuardrails()])
  }

  // Loading state
  if (!aiStatus) {
    return (
      <div className="bg-white border border-[var(--border)] rounded-xl px-5 py-3 text-xs font-sans text-[var(--text-muted)]">
        Loading autopilot status...
      </div>
    )
  }

  const dailyLossValue = aiStatus.daily_loss_locked ? (
    <span className="inline-flex items-center gap-1">
      <span className="text-xs font-bold text-red-600 bg-red-50 border border-red-200 rounded px-1.5 py-0.5 uppercase tracking-wide">
        LOCKED
      </span>
    </span>
  ) : (
    <span className="text-[var(--text-primary)]">
      {aiStatus.daily_loss_limit_pct.toFixed(1)}% limit
    </span>
  )

  const brokerValue = (
    <span className={clsx(
      'flex items-center gap-1',
      aiStatus.broker_connected ? 'text-emerald-700' : 'text-red-600',
    )}>
      <span className={clsx(
        'w-1.5 h-1.5 rounded-full flex-shrink-0',
        aiStatus.broker_connected ? 'bg-emerald-500' : 'bg-red-500',
      )} />
      {aiStatus.broker_connected ? 'Connected' : 'Offline'}
    </span>
  )

  return (
    <div className="bg-white border border-[var(--border)] rounded-xl px-5 py-3">
      <div className="flex items-center gap-4 flex-wrap">
        {/* Mode badge — prominent */}
        <ModeBadge mode={aiStatus.mode} />

        <Divider />

        {/* Kill switch — always big and red (or green when resuming) */}
        <button
          type="button"
          onClick={() => void handleKillToggle()}
          className={clsx(
            'rounded-lg px-4 py-2 text-xs font-bold uppercase tracking-wider transition-colors flex-shrink-0',
            aiStatus.emergency_stop
              ? 'bg-emerald-600 text-white hover:bg-emerald-700'
              : 'bg-red-600 text-white hover:bg-red-700',
          )}
        >
          {aiStatus.emergency_stop ? 'Resume Autopilot' : 'KILL'}
        </button>

        {/* Daily loss reset button — only when locked */}
        {aiStatus.daily_loss_locked && (
          <button
            type="button"
            onClick={() => void handleDailyLossReset()}
            className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-800 hover:bg-amber-100 transition-colors flex-shrink-0"
          >
            Reset Daily Lock
          </button>
        )}

        <Divider />

        <StatItem label="Daily Loss" value={dailyLossValue} />

        <Divider />

        <StatItem
          label="Positions"
          value={`${aiStatus.open_positions_count} open`}
        />

        <Divider />

        <StatItem
          label="Rules"
          value={`${aiStatus.active_rules_count} active`}
        />

        <Divider />

        <StatItem label="Broker" value={brokerValue} />

        {/* Changes today — low-key but informative */}
        {aiStatus.changes_today > 0 && (
          <>
            <Divider />
            <StatItem
              label="Changes Today"
              value={
                <span className="text-amber-700">{aiStatus.changes_today}</span>
              }
            />
          </>
        )}
      </div>
    </div>
  )
}
