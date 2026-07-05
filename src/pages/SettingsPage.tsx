/**
 * SettingsPage — system & connection control panel.
 *
 * Sections:
 *  1. System status      — IBKR host/port, paper/sim/mock flags, bot run state
 *  2. Connection controls — connect / disconnect IBKR, reset sim account
 *  3. Autopilot config   — mode (OFF/PAPER/LIVE), daily-loss limit, locks, resets
 *  4. About              — app identity + backend note
 */
import React, { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import {
  fetchStatus,
  connectIBKR,
  disconnectIBKR,
  resetSimAccount,
  fetchAutopilotConfig,
  setAutopilotMode,
  resetDailyLossLock,
} from '@/services/api'
import type { SystemStatus, AutopilotConfig, AutopilotMode } from '@/types'

// ── helpers ────────────────────────────────────────────────────────────────────

function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null) return '—'
  return `${v.toFixed(digits)}%`
}

const MODE_META: Record<AutopilotMode, { label: string; desc: string; cls: string }> = {
  OFF:   { label: 'OFF',   desc: 'AI dormant — manual only',         cls: 'text-terminal-dim   border-terminal-border  bg-terminal-muted/30' },
  PAPER: { label: 'PAPER', desc: 'AI active, no real-money orders',  cls: 'text-terminal-blue  border-terminal-blue/50 bg-terminal-blue/10' },
  LIVE:  { label: 'LIVE',  desc: 'AI manages rules AND real orders', cls: 'text-terminal-red   border-terminal-red/50  bg-terminal-red/10' },
}

// ── small UI atoms ───────────────────────────────────────────────────────────────

function Section({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  )
}

function StatusRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5 border-b border-terminal-border/60 last:border-0">
      <span className="font-mono text-[11px] text-terminal-ghost uppercase tracking-wider">{label}</span>
      <span className="font-mono text-xs text-terminal-text text-right tabular-nums">{children}</span>
    </div>
  )
}

function BoolPill({ on, onLabel = 'YES', offLabel = 'NO', tone = 'green' }: {
  on: boolean
  onLabel?: string
  offLabel?: string
  tone?: 'green' | 'blue' | 'amber' | 'red'
}) {
  const onCls = {
    green: 'bg-terminal-green/15 text-terminal-green',
    blue:  'bg-terminal-blue/15 text-terminal-blue',
    amber: 'bg-terminal-amber/15 text-terminal-amber',
    red:   'bg-terminal-red/15 text-terminal-red',
  }[tone]
  return (
    <span className={clsx(
      'text-[10px] font-mono px-2 py-0.5 rounded font-semibold uppercase tracking-wider',
      on ? onCls : 'bg-terminal-muted/40 text-terminal-ghost',
    )}>
      {on ? onLabel : offLabel}
    </span>
  )
}

// ── page ─────────────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const [status, setStatus]  = useState<SystemStatus | null>(null)
  const [config, setConfig]  = useState<AutopilotConfig | null>(null)
  const [busy,   setBusy]    = useState<string | null>(null)
  const [error,  setError]   = useState<string | null>(null)
  const [authMissing, setAuthMissing] = useState(false)

  const load = useCallback(async () => {
    try {
      const st = await fetchStatus()
      setStatus(st)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load system status')
    }
    try {
      const cfg = await fetchAutopilotConfig()
      setConfig(cfg)
      setAuthMissing(false)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to load autopilot config'
      if (msg.includes('401')) setAuthMissing(true)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 10_000)
    return () => clearInterval(t)
  }, [load])

  const run = useCallback(async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key)
    setError(null)
    try {
      await fn()
      await load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Action failed')
    } finally {
      setBusy(null)
    }
  }, [load])

  const onMode = (mode: AutopilotMode) => {
    if (mode === config?.autopilot_mode) return
    if (mode === 'LIVE' && !window.confirm(
      'Switch autopilot to LIVE? The AI will be allowed to manage rules and place REAL-MONEY orders. Continue?',
    )) return
    void run(`mode:${mode}`, () => setAutopilotMode(mode))
  }

  const onResetSim = () => {
    if (!window.confirm('Reset the simulation account to its initial cash balance? All sim positions and history will be wiped.')) return
    void run('sim:reset', resetSimAccount)
  }

  return (
    <div className="flex flex-col gap-5">
      {/* ── error toast ────────────────────────────────────────────── */}
      {error && (
        <div className="bg-terminal-red/10 border border-terminal-red/40 rounded-lg px-4 py-2">
          <p className="font-mono text-xs text-terminal-red truncate">{error}</p>
        </div>
      )}

      {/* ── System status ──────────────────────────────────────────── */}
      <Section
        title="System Status"
        right={status ? <BoolPill on={status.ibkr_connected} onLabel="IBKR LINKED" offLabel="IBKR OFFLINE" /> : null}
      >
        {status ? (
          <div className="flex flex-col">
            <StatusRow label="IBKR Host">{status.ibkr_host || '—'}</StatusRow>
            <StatusRow label="IBKR Port">{status.ibkr_port ?? '—'}</StatusRow>
            <StatusRow label="Connected"><BoolPill on={status.ibkr_connected} /></StatusRow>
            <StatusRow label="Paper Account"><BoolPill on={status.is_paper} tone="blue" /></StatusRow>
            <StatusRow label="Simulation Mode"><BoolPill on={status.sim_mode} tone="amber" /></StatusRow>
            <StatusRow label="Mock Mode"><BoolPill on={status.mock_mode} tone="amber" /></StatusRow>
            <StatusRow label="Bot Running"><BoolPill on={status.bot_running} onLabel="RUNNING" offLabel="STOPPED" /></StatusRow>
            <StatusRow label="Bot Interval">{status.bot_interval_seconds != null ? `${status.bot_interval_seconds}s` : '—'}</StatusRow>
            {status.last_run && <StatusRow label="Last Run">{new Date(status.last_run).toLocaleString()}</StatusRow>}
            {status.next_run && <StatusRow label="Next Run">{new Date(status.next_run).toLocaleString()}</StatusRow>}
          </div>
        ) : (
          <p className="font-mono text-xs text-terminal-ghost py-2">Loading system status…</p>
        )}
      </Section>

      {/* ── Connection controls ────────────────────────────────────── */}
      <Section title="Connection Controls">
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => run('ibkr:connect', connectIBKR)}
            disabled={busy === 'ibkr:connect' || status?.ibkr_connected}
            className="text-xs font-mono px-4 py-2 rounded bg-terminal-green/15 border border-terminal-green/40 text-terminal-green hover:bg-terminal-green/25 disabled:opacity-40 transition-colors"
          >
            {busy === 'ibkr:connect' ? 'Connecting…' : 'Connect IBKR'}
          </button>
          <button
            onClick={() => run('ibkr:disconnect', disconnectIBKR)}
            disabled={busy === 'ibkr:disconnect' || !status?.ibkr_connected}
            className="text-xs font-mono px-4 py-2 rounded bg-terminal-amber/15 border border-terminal-amber/40 text-terminal-amber hover:bg-terminal-amber/25 disabled:opacity-40 transition-colors"
          >
            {busy === 'ibkr:disconnect' ? 'Disconnecting…' : 'Disconnect IBKR'}
          </button>
          <div className="md:ml-auto">
            <button
              onClick={onResetSim}
              disabled={busy === 'sim:reset'}
              className="text-xs font-mono px-4 py-2 rounded border border-terminal-border text-terminal-dim hover:text-terminal-red hover:border-terminal-red/40 disabled:opacity-40 transition-colors"
            >
              {busy === 'sim:reset' ? 'Resetting…' : 'Reset Sim Account'}
            </button>
          </div>
        </div>
        <p className="font-mono text-[11px] text-terminal-ghost mt-3">
          Connecting attaches to the IBKR gateway at{' '}
          <span className="text-terminal-dim">{status ? `${status.ibkr_host}:${status.ibkr_port}` : '…'}</span>.
          Resetting the sim account restores starting cash and clears all simulated positions and history.
        </p>
      </Section>

      {/* ── Autopilot config ───────────────────────────────────────── */}
      <Section
        title="Autopilot Configuration"
        right={
          <div className="flex gap-1.5">
            <BoolPill on={!!config?.emergency_stop} onLabel="KILL ON" offLabel="KILL OFF" tone="red" />
            <BoolPill on={!!config?.daily_loss_locked} onLabel="LOSS LOCKED" offLabel="UNLOCKED" tone="amber" />
          </div>
        }
      >
        {authMissing ? (
          <div className="flex flex-col gap-2 py-2">
            <p className="font-mono text-sm text-terminal-amber">Autopilot config requires authentication (401).</p>
            <p className="font-mono text-xs text-terminal-dim max-w-xl">
              Set <code className="text-terminal-blue">VITE_JWT_BOOTSTRAP_SECRET</code> in{' '}
              <code className="text-terminal-blue">aiautomation/.env.local</code> to match the backend's{' '}
              <code className="text-terminal-blue">JWT_BOOTSTRAP_SECRET</code>, then restart the dev server.
            </p>
          </div>
        ) : (
          <>
            <div className="flex flex-col md:flex-row md:items-center gap-4">
              <div className="flex gap-2">
                {(['OFF', 'PAPER', 'LIVE'] as AutopilotMode[]).map((mode) => {
                  const active = config?.autopilot_mode === mode
                  const meta = MODE_META[mode]
                  return (
                    <button
                      key={mode}
                      onClick={() => onMode(mode)}
                      disabled={busy === `mode:${mode}`}
                      className={clsx(
                        'flex flex-col items-start px-4 py-2.5 rounded border transition-colors min-w-[120px] disabled:opacity-50',
                        active ? meta.cls : 'border-terminal-border text-terminal-dim hover:text-terminal-text hover:border-terminal-muted',
                      )}
                    >
                      <span className="font-mono text-sm font-bold tracking-wider">{meta.label}</span>
                      <span className="font-mono text-[10px] text-terminal-ghost mt-0.5 text-left leading-tight">{meta.desc}</span>
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-4">
              <div className="flex flex-col gap-1 border border-terminal-border rounded-lg p-3 bg-terminal-bg/40">
                <span className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">Current Mode</span>
                <span className="font-mono text-lg font-bold text-terminal-text">{config?.autopilot_mode ?? '—'}</span>
              </div>
              <div className="flex flex-col gap-1 border border-terminal-border rounded-lg p-3 bg-terminal-bg/40">
                <span className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">Daily Loss Limit</span>
                <span className="font-mono text-lg font-bold text-terminal-text tabular-nums">{fmtPct(config?.daily_loss_limit_pct)}</span>
              </div>
              <div className="flex flex-col gap-1 border border-terminal-border rounded-lg p-3 bg-terminal-bg/40">
                <span className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">Daily Loss Lock</span>
                <span className={clsx('font-mono text-lg font-bold', config?.daily_loss_locked ? 'text-terminal-amber' : 'text-terminal-green')}>
                  {config?.daily_loss_locked ? 'LOCKED' : 'CLEAR'}
                </span>
              </div>
            </div>

            {config?.daily_loss_locked && (
              <div className="mt-4 flex items-center justify-between gap-3 bg-terminal-amber/10 border border-terminal-amber/40 rounded-lg px-4 py-3">
                <p className="font-mono text-[11px] text-terminal-dim">
                  Daily loss limit reached — new entries are blocked until the lock is reset.
                </p>
                <button
                  onClick={() => run('daily:reset', resetDailyLossLock)}
                  disabled={busy === 'daily:reset'}
                  className="text-xs font-mono px-4 py-2 rounded bg-terminal-amber/20 border border-terminal-amber/50 text-terminal-amber hover:bg-terminal-amber/30 disabled:opacity-40 transition-colors shrink-0"
                >
                  {busy === 'daily:reset' ? 'Resetting…' : 'Reset Daily Lock'}
                </button>
              </div>
            )}
          </>
        )}
      </Section>

      {/* ── About ──────────────────────────────────────────────────── */}
      <Section title="About">
        <div className="flex flex-col gap-1.5">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-base font-bold text-terminal-text">TradeBot v2</span>
            <span className="font-mono text-[10px] text-terminal-ghost uppercase tracking-widest">AI Automation Console</span>
          </div>
          <p className="font-mono text-[11px] text-terminal-dim max-w-xl">
            This dashboard is a thin client — all data, account state, and trading actions proxy to the
            FastAPI backend. No trading logic runs in the browser; the UI only reads status and issues
            control commands the backend executes.
          </p>
        </div>
      </Section>
    </div>
  )
}
