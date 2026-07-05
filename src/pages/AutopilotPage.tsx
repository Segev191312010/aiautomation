/**
 * AutopilotPage — control panel for the AI automation engine.
 *
 * Surfaces and controls the backend auto-learn + auto-rules pipeline:
 *  1. Mode & authority  — OFF / PAPER / LIVE, autonomy + shadow state, broker link
 *  2. Emergency brakes  — kill switch + daily-loss lock (activate / reset)
 *  3. Live KPIs         — active rules, open positions, AI trades, optimizer
 *  4. Learned insights  — performance window + per-source breakdown
 *  5. AI / auto rules    — list with promote / pause / retire controls
 *  6. Activity feed     — the AI audit log (decisions, trips, mode changes)
 */
import React, { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import KPICard from '@/components/tradebot/KPICard'
import {
  fetchAutopilotStatus,
  fetchAutopilotRules,
  fetchAutopilotPerformance,
  fetchAutopilotFeed,
  setAutopilotMode,
  activateKillSwitch,
  resetKillSwitch,
  resetDailyLossLock,
  promoteRule,
  pauseRule,
  retireRule,
} from '@/services/api'
import type {
  AutopilotStatus,
  AutopilotMode,
  AutopilotRule,
  AutopilotPerformance,
  AuditEntry,
} from '@/types'

// ── formatting helpers ─────────────────────────────────────────────────────────

function fmtUSD(v: number | null | undefined): string {
  if (v == null) return '—'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(v)
}
function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null) return '—'
  return `${(v * 100).toFixed(digits)}%`
}
function fmtTime(s: string | null | undefined): string {
  if (!s) return '—'
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString()
}
function relTime(s: string | null | undefined): string {
  if (!s) return '—'
  const d = new Date(s).getTime()
  if (Number.isNaN(d)) return '—'
  const diff = Date.now() - d
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

const MODE_META: Record<AutopilotMode, { label: string; desc: string; cls: string }> = {
  OFF:   { label: 'OFF',   desc: 'AI dormant — manual only',           cls: 'text-terminal-dim   border-terminal-border    bg-terminal-muted/30' },
  PAPER: { label: 'PAPER', desc: 'AI active, no real-money orders',     cls: 'text-terminal-blue  border-terminal-blue/50   bg-terminal-blue/10' },
  LIVE:  { label: 'LIVE',  desc: 'AI manages rules AND real orders',    cls: 'text-terminal-red   border-terminal-red/50    bg-terminal-red/10' },
}

function statusBadge(status: string): string {
  switch (status) {
    case 'active':  return 'text-terminal-green border-terminal-green/40 bg-terminal-green/10'
    case 'paused':  return 'text-terminal-amber border-terminal-amber/40 bg-terminal-amber/10'
    case 'retired': return 'text-terminal-dim   border-terminal-border   bg-terminal-muted/30'
    case 'draft':
    case 'shadow':  return 'text-terminal-purple border-terminal-purple/40 bg-terminal-purple/10'
    default:        return 'text-terminal-dim border-terminal-border bg-terminal-muted/30'
  }
}

// ── small UI atoms ──────────────────────────────────────────────────────────────

function Pill({ label, on, tone = 'blue' }: { label: string; on: boolean; tone?: 'blue' | 'green' | 'amber' | 'red' | 'purple' }) {
  const onCls = {
    blue:   'bg-terminal-blue/15 text-terminal-blue',
    green:  'bg-terminal-green/15 text-terminal-green',
    amber:  'bg-terminal-amber/15 text-terminal-amber',
    red:    'bg-terminal-red/15 text-terminal-red',
    purple: 'bg-terminal-purple/15 text-terminal-purple',
  }[tone]
  return (
    <span className={clsx(
      'text-[10px] font-mono px-2 py-0.5 rounded font-semibold uppercase tracking-wider',
      on ? onCls : 'bg-terminal-muted/40 text-terminal-ghost',
    )}>
      {label}
    </span>
  )
}

function SectionCard({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) {
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

// ── page ────────────────────────────────────────────────────────────────────────

export default function AutopilotPage() {
  const [status, setStatus] = useState<AutopilotStatus | null>(null)
  const [rules,  setRules]  = useState<AutopilotRule[]>([])
  const [perf,   setPerf]   = useState<AutopilotPerformance | null>(null)
  const [feed,   setFeed]   = useState<AuditEntry[]>([])
  const [busy,   setBusy]   = useState<string | null>(null)
  const [error,  setError]  = useState<string | null>(null)
  const [authMissing, setAuthMissing] = useState(false)

  const load = useCallback(async () => {
    try {
      const [st, rl, pf, fd] = await Promise.all([
        fetchAutopilotStatus(),
        fetchAutopilotRules(),
        fetchAutopilotPerformance(),
        fetchAutopilotFeed(25),
      ])
      setStatus(st)
      setRules(rl)
      setPerf(pf)
      setFeed(fd.entries)
      setError(null)
      setAuthMissing(false)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to load autopilot data'
      if (msg.includes('401')) setAuthMissing(true)
      setError(msg)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 7_000)
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
    if (mode === status?.mode) return
    if (mode === 'LIVE' && !window.confirm(
      'Switch autopilot to LIVE? The AI will be allowed to manage rules and place REAL-MONEY orders. Continue?',
    )) return
    void run(`mode:${mode}`, () => setAutopilotMode(mode))
  }
  const onPromote = (r: AutopilotRule) => {
    const reason = window.prompt(`Promote "${r.name}" to active?\nReason:`, 'Validated — promoting to active')
    if (reason == null) return
    void run(`promote:${r.id}`, () => promoteRule(r.id, reason || 'Promoted by operator'))
  }
  const onPause = (r: AutopilotRule) => {
    const reason = window.prompt(`Pause "${r.name}"?\nReason:`, 'Paused by operator')
    if (reason == null) return
    void run(`pause:${r.id}`, () => pauseRule(r.id, reason || undefined))
  }
  const onRetire = (r: AutopilotRule) => {
    if (!window.confirm(`Retire "${r.name}"? This disables the rule permanently.`)) return
    void run(`retire:${r.id}`, () => retireRule(r.id, 'Retired by operator'))
  }

  // ── auth-not-configured notice ────────────────────────────────────────────────
  if (authMissing) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3 text-center">
        <p className="font-mono text-sm text-terminal-amber">Autopilot endpoints require authentication (401).</p>
        <p className="font-mono text-xs text-terminal-dim max-w-md">
          Set <code className="text-terminal-blue">VITE_JWT_BOOTSTRAP_SECRET</code> in{' '}
          <code className="text-terminal-blue">aiautomation/.env.local</code> to match the backend's{' '}
          <code className="text-terminal-blue">JWT_BOOTSTRAP_SECRET</code>, then restart the dev server.
        </p>
      </div>
    )
  }

  const m = status ? MODE_META[status.mode] : null
  const aiRules = rules.filter((r) => r.ai_generated || r.created_by === 'auto' || r.id.startsWith('auto:'))
  const otherRules = rules.filter((r) => !(r.ai_generated || r.created_by === 'auto' || r.id.startsWith('auto:')))

  return (
    <div className="flex flex-col gap-5">
      {/* ── error toast ────────────────────────────────────────────── */}
      {error && !authMissing && (
        <div className="bg-terminal-red/10 border border-terminal-red/40 rounded-lg px-4 py-2">
          <p className="font-mono text-xs text-terminal-red truncate">{error}</p>
        </div>
      )}

      {/* ── emergency banners ──────────────────────────────────────── */}
      {status?.emergency_stop && (
        <div className="bg-terminal-red/10 border border-terminal-red/50 shadow-glow-red rounded-lg px-4 py-3 flex items-center justify-between gap-3">
          <div>
            <p className="font-mono text-sm font-bold text-terminal-red">⛔ KILL SWITCH ACTIVE</p>
            <p className="font-mono text-[11px] text-terminal-dim mt-0.5">
              All new AI &amp; rule entries are blocked. Exits still process.
            </p>
          </div>
          <button
            onClick={() => run('kill:reset', resetKillSwitch)}
            disabled={busy === 'kill:reset'}
            className="text-xs font-mono px-4 py-2 rounded bg-terminal-red/20 border border-terminal-red/50 text-terminal-red hover:bg-terminal-red/30 disabled:opacity-40 transition-colors shrink-0"
          >
            {busy === 'kill:reset' ? 'Resetting…' : 'Reset kill switch'}
          </button>
        </div>
      )}
      {status?.daily_loss_locked && (
        <div className="bg-terminal-amber/10 border border-terminal-amber/50 rounded-lg px-4 py-3 flex items-center justify-between gap-3">
          <div>
            <p className="font-mono text-sm font-bold text-terminal-amber">⚠ DAILY LOSS LOCK ACTIVE</p>
            <p className="font-mono text-[11px] text-terminal-dim mt-0.5">
              Daily loss limit ({fmtPct(status.daily_loss_limit_pct / 100)}) hit — new entries blocked until reset.
            </p>
          </div>
          <button
            onClick={() => run('daily:reset', resetDailyLossLock)}
            disabled={busy === 'daily:reset'}
            className="text-xs font-mono px-4 py-2 rounded bg-terminal-amber/20 border border-terminal-amber/50 text-terminal-amber hover:bg-terminal-amber/30 disabled:opacity-40 transition-colors shrink-0"
          >
            {busy === 'daily:reset' ? 'Resetting…' : 'Reset daily lock'}
          </button>
        </div>
      )}

      {/* ── Mode & authority ──────────────────────────────────────── */}
      <SectionCard
        title="Autopilot Mode & Authority"
        right={
          <div className="flex gap-1.5">
            <Pill label="Autonomy" on={!!status?.autonomy_active} tone="green" />
            <Pill label="Shadow" on={!!status?.shadow_mode} tone="amber" />
            <Pill label="Broker" on={!!status?.broker_connected} tone="green" />
          </div>
        }
      >
        <div className="flex flex-col md:flex-row md:items-center gap-4">
          <div className="flex gap-2">
            {(['OFF', 'PAPER', 'LIVE'] as AutopilotMode[]).map((mode) => {
              const active = status?.mode === mode
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
          <div className="md:ml-auto flex items-center gap-2">
            {!status?.emergency_stop && (
              <button
                onClick={() => {
                  if (window.confirm('Activate the emergency kill switch? This blocks all new AI & rule entries.')) {
                    void run('kill:on', activateKillSwitch)
                  }
                }}
                disabled={busy === 'kill:on'}
                className="text-xs font-mono px-4 py-2 rounded bg-terminal-red/15 border border-terminal-red/40 text-terminal-red hover:bg-terminal-red/25 disabled:opacity-40 transition-colors"
              >
                {busy === 'kill:on' ? 'Stopping…' : '⛔ Kill switch'}
              </button>
            )}
          </div>
        </div>
        {m && (
          <p className="font-mono text-[11px] text-terminal-dim mt-3">
            Current: <span className="text-terminal-text font-semibold">{m.label}</span> — {m.desc}.
            {status?.last_action_at && <> Last AI action {relTime(status.last_action_at)}.</>}
          </p>
        )}
      </SectionCard>

      {/* ── Live KPIs ─────────────────────────────────────────────── */}
      <section>
        <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest mb-3">Engine Status</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          <KPICard label="Active Rules"   value={status?.active_rules_count ?? '—'} highlight />
          <KPICard label="Open Positions" value={status?.open_positions_count ?? '—'} />
          <KPICard label="AI Open Trades" value={status?.direct_ai_open_trades_count ?? '—'} />
          <KPICard label="Changes Today"  value={status?.changes_today ?? '—'} />
          <KPICard label="AI Budget Left" value={status?.daily_budget_remaining ?? '—'} subLabel="changes/day" />
          <KPICard
            label="Optimizer"
            value={status?.optimizer_running ? 'RUNNING' : 'idle'}
            positive={status?.optimizer_running ? true : undefined}
            subLabel={status?.next_optimization_at ? `next ${relTime(status.next_optimization_at)}` : 'next —'}
          />
        </div>
      </section>

      {/* ── Learned insights / performance ────────────────────────── */}
      <SectionCard
        title={`Learned Performance — last ${perf?.window_days ?? 30}d`}
        right={<span className="font-mono text-[11px] text-terminal-dim">{perf?.total_trades ?? 0} trades</span>}
      >
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <KPICard label="Hit Rate"      value={fmtPct(perf?.hit_rate)} positive={perf?.hit_rate != null ? perf.hit_rate >= 0.5 : undefined} />
          <KPICard label="Realized P&L"  value={fmtUSD(perf?.realized_pnl)} positive={perf?.realized_pnl != null ? perf.realized_pnl >= 0 : undefined} />
          <KPICard label="Unrealized P&L" value={fmtUSD(perf?.unrealized_pnl)} positive={perf?.unrealized_pnl != null ? perf.unrealized_pnl >= 0 : undefined} />
          <KPICard label="ROI"           value={fmtPct(perf?.roi)} positive={perf?.roi != null ? perf.roi >= 0 : undefined} />
        </div>
        {perf && perf.by_source.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[420px]">
              <thead>
                <tr className="border-b border-terminal-border">
                  {['Source', 'Trades', 'Hit Rate', 'Realized P&L', 'ROI'].map((c) => (
                    <th key={c} className="py-1.5 px-3 text-[10px] font-mono uppercase tracking-widest text-terminal-ghost font-normal text-right first:text-left">{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {perf.by_source.map((s) => (
                  <tr key={s.source} className="border-b border-terminal-border/60">
                    <td className="py-1.5 px-3 font-mono text-xs text-terminal-text">{s.source}</td>
                    <td className="py-1.5 px-3 font-mono text-[11px] text-terminal-dim tabular-nums text-right">{s.total_trades ?? s.trades ?? 0}</td>
                    <td className="py-1.5 px-3 font-mono text-[11px] tabular-nums text-right text-terminal-dim">{fmtPct(s.hit_rate)}</td>
                    <td className={clsx('py-1.5 px-3 font-mono text-[11px] tabular-nums text-right', (s.realized_pnl ?? 0) >= 0 ? 'text-terminal-green' : 'text-terminal-red')}>{fmtUSD(s.realized_pnl)}</td>
                    <td className="py-1.5 px-3 font-mono text-[11px] tabular-nums text-right text-terminal-dim">{fmtPct(s.roi)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="font-mono text-xs text-terminal-ghost py-2">
            No closed trades in the window yet — insights populate as the engine trades.
          </p>
        )}
      </SectionCard>

      {/* ── AI & auto rules ───────────────────────────────────────── */}
      <SectionCard
        title="AI & Auto-Generated Rules"
        right={<span className="font-mono text-[11px] text-terminal-dim">{aiRules.length} AI · {otherRules.length} operator</span>}
      >
        {rules.length === 0 ? (
          <p className="font-mono text-xs text-terminal-ghost py-4 text-center">
            No rules yet. The engine seeds starter rules once autopilot has authority and the bot runs.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {[...aiRules, ...otherRules].map((r) => (
              <RuleRow
                key={r.id}
                rule={r}
                busy={busy}
                onPromote={() => onPromote(r)}
                onPause={() => onPause(r)}
                onRetire={() => onRetire(r)}
              />
            ))}
          </div>
        )}
      </SectionCard>

      {/* ── Activity feed ─────────────────────────────────────────── */}
      <SectionCard title="AI Activity Feed">
        {feed.length === 0 ? (
          <p className="font-mono text-xs text-terminal-ghost py-4 text-center">No activity yet</p>
        ) : (
          <div className="flex flex-col divide-y divide-terminal-border/60">
            {feed.map((e) => <FeedRow key={e.id} entry={e} />)}
          </div>
        )}
      </SectionCard>
    </div>
  )
}

// ── rule row ────────────────────────────────────────────────────────────────────

function RuleRow({
  rule, busy, onPromote, onPause, onRetire,
}: {
  rule: AutopilotRule
  busy: string | null
  onPromote: () => void
  onPause: () => void
  onRetire: () => void
}) {
  const isAi = rule.ai_generated || rule.created_by === 'auto' || rule.id.startsWith('auto:')
  const canPromote = rule.status === 'draft' || rule.status === 'shadow' || rule.status === 'paused' || !rule.enabled
  const active = rule.status === 'active' && rule.enabled
  return (
    <div className="border border-terminal-border rounded-lg p-3 bg-terminal-bg/40">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-sm font-semibold text-terminal-text truncate">{rule.name}</span>
            <span className={clsx('text-[10px] font-mono px-1.5 py-0.5 rounded border uppercase tracking-wider', statusBadge(rule.status))}>{rule.status}</span>
            {isAi && <Pill label="AI" on tone="purple" />}
            <span className="text-[10px] font-mono text-terminal-ghost">v{rule.version}</span>
          </div>
          <p className="font-mono text-[11px] text-terminal-dim mt-1">
            {rule.symbol || rule.universe || '—'} · {rule.action.type} {rule.action.quantity} · {rule.conditions.length} condition{rule.conditions.length === 1 ? '' : 's'} · cd {rule.cooldown_minutes}m
          </p>
          {(rule.thesis || rule.ai_reason) && (
            <p className="font-mono text-[11px] text-terminal-ghost mt-1 italic line-clamp-2">
              {rule.thesis || rule.ai_reason}
            </p>
          )}
        </div>
        <div className="flex flex-col gap-1.5 shrink-0">
          {canPromote && (
            <button
              onClick={onPromote}
              disabled={busy === `promote:${rule.id}`}
              className="text-[11px] font-mono px-3 py-1 rounded bg-terminal-green/15 border border-terminal-green/40 text-terminal-green hover:bg-terminal-green/25 disabled:opacity-40 transition-colors"
            >
              {busy === `promote:${rule.id}` ? '…' : 'Promote'}
            </button>
          )}
          {active && (
            <button
              onClick={onPause}
              disabled={busy === `pause:${rule.id}`}
              className="text-[11px] font-mono px-3 py-1 rounded bg-terminal-amber/15 border border-terminal-amber/40 text-terminal-amber hover:bg-terminal-amber/25 disabled:opacity-40 transition-colors"
            >
              {busy === `pause:${rule.id}` ? '…' : 'Pause'}
            </button>
          )}
          {rule.status !== 'retired' && (
            <button
              onClick={onRetire}
              disabled={busy === `retire:${rule.id}`}
              className="text-[11px] font-mono px-3 py-1 rounded border border-terminal-border text-terminal-dim hover:text-terminal-red hover:border-terminal-red/40 disabled:opacity-40 transition-colors"
            >
              {busy === `retire:${rule.id}` ? '…' : 'Retire'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── feed row ────────────────────────────────────────────────────────────────────

const CATEGORY_TONE: Record<string, string> = {
  safety:    'text-terminal-red',
  autopilot: 'text-terminal-blue',
  operator:  'text-terminal-amber',
  optimizer: 'text-terminal-purple',
}

function FeedRow({ entry }: { entry: AuditEntry }) {
  const tone = CATEGORY_TONE[entry.category] ?? 'text-terminal-dim'
  return (
    <div className="flex items-start gap-3 py-2">
      <span className={clsx('font-mono text-[10px] uppercase tracking-wider mt-0.5 w-20 shrink-0', tone)}>{entry.category}</span>
      <div className="min-w-0 flex-1">
        <p className="font-mono text-xs text-terminal-text">{entry.description}</p>
        {entry.reason && <p className="font-mono text-[11px] text-terminal-ghost mt-0.5 truncate">{entry.reason}</p>}
      </div>
      <span className="font-mono text-[10px] text-terminal-ghost shrink-0 mt-0.5" title={fmtTime(entry.timestamp)}>{relTime(entry.timestamp)}</span>
    </div>
  )
}
