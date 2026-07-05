/**
 * AiSystemPage — observability for the AI automation engine.
 *
 * Visually answers "is the AI running like the architecture overview?" by
 * rendering the live pipeline as a labeled flow:
 *
 *   Trigger → Context → LLM → Safety → Execute → Persist → Evaluate → Learn
 *
 * Each stage shows a live status badge derived from the autopilot status +
 * recent activity feed. Below the pipeline: a KPI row (mode, optimizer,
 * budget, changes), a learned-performance block, and the recent AI decision
 * feed. Read-only — controls live on AutopilotPage. Polls every ~7s.
 */
import React, { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import KPICard from '@/components/tradebot/KPICard'
import {
  fetchAutopilotStatus,
  fetchAutopilotPerformance,
  fetchAutopilotFeed,
} from '@/services/api'
import type {
  AutopilotStatus,
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

// ── stage model ─────────────────────────────────────────────────────────────────

type StageTone = 'ok' | 'active' | 'warn' | 'idle' | 'error'

interface Stage {
  key: string
  label: string
  desc: string
  tone: StageTone
  detail: string
}

const TONE_META: Record<StageTone, { dot: string; badge: string; label: string; box: string }> = {
  ok:     { dot: 'bg-terminal-green',  badge: 'text-terminal-green border-terminal-green/40 bg-terminal-green/10',   label: 'READY',  box: 'border-terminal-green/30' },
  active: { dot: 'bg-terminal-blue animate-pulse', badge: 'text-terminal-blue border-terminal-blue/40 bg-terminal-blue/10', label: 'LIVE', box: 'border-terminal-blue/40 shadow-glow-blue' },
  warn:   { dot: 'bg-terminal-amber',  badge: 'text-terminal-amber border-terminal-amber/40 bg-terminal-amber/10',   label: 'HOLD',   box: 'border-terminal-amber/40' },
  error:  { dot: 'bg-terminal-red',    badge: 'text-terminal-red border-terminal-red/40 bg-terminal-red/10',         label: 'BLOCKED', box: 'border-terminal-red/40 shadow-glow-red' },
  idle:   { dot: 'bg-terminal-ghost',  badge: 'text-terminal-ghost border-terminal-border bg-terminal-muted/30',     label: 'IDLE',   box: 'border-terminal-border' },
}

/** Build the 8-stage pipeline view from live status + the most recent feed entries. */
function buildStages(status: AutopilotStatus | null, feed: AuditEntry[]): Stage[] {
  const off = status?.mode === 'OFF'
  const live = status?.mode === 'LIVE'
  const paper = status?.mode === 'PAPER'
  const blocked = !!status?.emergency_stop
  const lossLock = !!status?.daily_loss_locked
  const armed = !off && !blocked

  // Has the engine emitted any activity of a given category recently?
  const has = (pred: (e: AuditEntry) => boolean) => feed.some(pred)
  const optimizerSeen = has((e) => e.category === 'optimizer' || /optim/i.test(e.action_type))
  const decisionSeen = has((e) => e.category === 'autopilot' || /(decision|trade|order|signal)/i.test(e.action_type))
  const safetySeen = has((e) => e.category === 'safety')

  const triggerTone: StageTone = blocked ? 'error' : off ? 'idle' : status?.optimizer_running ? 'active' : 'ok'
  const contextTone: StageTone = off ? 'idle' : armed ? 'ok' : 'warn'
  const llmTone: StageTone = off ? 'idle' : status?.optimizer_running ? 'active' : armed ? 'ok' : 'warn'
  const safetyTone: StageTone = blocked ? 'error' : lossLock ? 'warn' : armed ? 'ok' : 'idle'
  const executeTone: StageTone = blocked || lossLock ? 'warn' : off ? 'idle' : live ? 'active' : paper ? 'ok' : 'idle'
  const persistTone: StageTone = feed.length > 0 ? 'ok' : off ? 'idle' : 'ok'
  const evaluateTone: StageTone = safetySeen ? 'ok' : off ? 'idle' : 'ok'
  const learnTone: StageTone = off ? 'idle' : optimizerSeen ? 'ok' : armed ? 'ok' : 'idle'

  return [
    {
      key: 'trigger', label: 'Trigger', desc: 'Optimizer schedule / bot tick',
      tone: triggerTone,
      detail: blocked ? 'Kill switch active' : status?.optimizer_running ? 'Optimizer running now' : status?.next_optimization_at ? `Next ${relTime(status.next_optimization_at)}` : off ? 'Engine dormant' : 'Armed',
    },
    {
      key: 'context', label: 'Context', desc: 'Market + perf + regime gather',
      tone: contextTone,
      detail: off ? 'No context built' : `${status?.active_rules_count ?? 0} rules · ${status?.open_positions_count ?? 0} pos`,
    },
    {
      key: 'llm', label: 'LLM Router', desc: 'Optimizer / advisor reasoning',
      tone: llmTone,
      detail: status?.optimizer_running ? 'Reasoning…' : decisionSeen ? 'Last call logged' : off ? 'Idle' : 'Ready',
    },
    {
      key: 'safety', label: 'Safety', desc: 'Brakes, budget, validation',
      tone: safetyTone,
      detail: blocked ? 'Entries blocked' : lossLock ? 'Daily-loss lock' : `${status?.daily_budget_remaining ?? '—'} changes left`,
    },
    {
      key: 'execute', label: 'Execute', desc: live ? 'Real-money orders' : 'Paper / shadow orders',
      tone: executeTone,
      detail: off ? 'No execution' : live ? 'LIVE orders enabled' : paper ? 'Paper orders' : 'Disabled',
    },
    {
      key: 'persist', label: 'Persist', desc: 'Decision ledger / audit log',
      tone: persistTone,
      detail: feed.length > 0 ? `${feed.length} recent entries` : 'No entries yet',
    },
    {
      key: 'evaluate', label: 'Evaluate', desc: 'Outcome scoring vs thesis',
      tone: evaluateTone,
      detail: safetySeen ? 'Scoring closed trades' : 'Awaiting closed trades',
    },
    {
      key: 'learn', label: 'Learn', desc: 'Feedback → next optimization',
      tone: learnTone,
      detail: off ? 'Loop paused' : status?.last_optimization_at ? `Last ${relTime(status.last_optimization_at)}` : 'No cycle yet',
    },
  ]
}

// ── stage card ──────────────────────────────────────────────────────────────────

function StageCard({ stage, index }: { stage: Stage; index: number }) {
  const meta = TONE_META[stage.tone]
  return (
    <div className={clsx('flex items-stretch', index > 0 && 'pl-1')}>
      {index > 0 && (
        <div className="hidden lg:flex items-center pr-1 text-terminal-ghost font-mono text-sm select-none" aria-hidden>
          →
        </div>
      )}
      <div className={clsx('flex-1 bg-terminal-bg/40 border rounded-lg p-3 flex flex-col gap-1.5 min-w-[140px]', meta.box)}>
        <div className="flex items-center justify-between gap-2">
          <span className="font-mono text-xs font-semibold text-terminal-text tracking-wide">{stage.label}</span>
          <span className={clsx('w-2 h-2 rounded-full shrink-0', meta.dot)} title={meta.label} />
        </div>
        <span className={clsx('self-start text-[9px] font-mono px-1.5 py-0.5 rounded border uppercase tracking-wider', meta.badge)}>
          {meta.label}
        </span>
        <span className="font-mono text-[10px] text-terminal-ghost leading-tight">{stage.desc}</span>
        <span className="font-mono text-[10px] text-terminal-dim leading-tight mt-auto pt-1">{stage.detail}</span>
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
      {entry.confidence != null && (
        <span className="font-mono text-[10px] text-terminal-purple shrink-0 mt-0.5">{fmtPct(entry.confidence, 0)}</span>
      )}
      <span className="font-mono text-[10px] text-terminal-ghost shrink-0 mt-0.5" title={fmtTime(entry.timestamp)}>{relTime(entry.timestamp)}</span>
    </div>
  )
}

// ── section wrapper ─────────────────────────────────────────────────────────────

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

const MODE_TONE: Record<string, { value: string; positive?: boolean }> = {
  OFF:   { value: 'OFF',   positive: undefined },
  PAPER: { value: 'PAPER', positive: true },
  LIVE:  { value: 'LIVE',  positive: false },
}

// ── page ────────────────────────────────────────────────────────────────────────

export default function AiSystemPage() {
  const [status, setStatus] = useState<AutopilotStatus | null>(null)
  const [perf,   setPerf]   = useState<AutopilotPerformance | null>(null)
  const [feed,   setFeed]   = useState<AuditEntry[]>([])
  const [error,  setError]  = useState<string | null>(null)
  const [authMissing, setAuthMissing] = useState(false)
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(async () => {
    try {
      const [st, pf, fd] = await Promise.all([
        fetchAutopilotStatus(),
        fetchAutopilotPerformance(),
        fetchAutopilotFeed(30),
      ])
      setStatus(st)
      setPerf(pf)
      setFeed(fd.entries)
      setError(null)
      setAuthMissing(false)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to load AI system data'
      if (msg.includes('401')) setAuthMissing(true)
      setError(msg)
    } finally {
      setLoaded(true)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 7_000)
    return () => clearInterval(t)
  }, [load])

  // ── auth-not-configured notice ────────────────────────────────────────────────
  if (authMissing) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3 text-center">
        <p className="font-mono text-sm text-terminal-amber">AI system endpoints require authentication (401).</p>
        <p className="font-mono text-xs text-terminal-dim max-w-md">
          Set <code className="text-terminal-blue">VITE_JWT_BOOTSTRAP_SECRET</code> in{' '}
          <code className="text-terminal-blue">aiautomation/.env.local</code> to match the backend's{' '}
          <code className="text-terminal-blue">JWT_BOOTSTRAP_SECRET</code>, then restart the dev server.
        </p>
      </div>
    )
  }

  const stages = buildStages(status, feed)
  const mode = status?.mode ?? 'OFF'
  const modeMeta = MODE_TONE[mode] ?? MODE_TONE.OFF
  const blocked = !!status?.emergency_stop
  const lossLock = !!status?.daily_loss_locked
  const pipelineHealthy = !blocked && !lossLock && mode !== 'OFF'

  return (
    <div className="flex flex-col gap-5">
      {/* ── error toast ────────────────────────────────────────────── */}
      {error && !authMissing && (
        <div className="bg-terminal-red/10 border border-terminal-red/40 rounded-lg px-4 py-2">
          <p className="font-mono text-xs text-terminal-red truncate">{error}</p>
        </div>
      )}

      {/* ── overview banner ───────────────────────────────────────── */}
      <section
        className={clsx(
          'rounded-lg px-4 py-3 border flex items-center justify-between gap-3',
          blocked
            ? 'bg-terminal-red/10 border-terminal-red/50 shadow-glow-red'
            : pipelineHealthy
            ? 'bg-terminal-green/5 border-terminal-green/30'
            : 'bg-terminal-surface border-terminal-border',
        )}
      >
        <div className="min-w-0">
          <p className="font-mono text-sm font-bold text-terminal-text">
            AI Pipeline —{' '}
            <span className={clsx(
              blocked ? 'text-terminal-red' : pipelineHealthy ? 'text-terminal-green' : 'text-terminal-amber',
            )}>
              {blocked ? 'HALTED' : mode === 'OFF' ? 'DORMANT' : lossLock ? 'LOCKED' : 'RUNNING'}
            </span>
          </p>
          <p className="font-mono text-[11px] text-terminal-dim mt-0.5 truncate">
            Model router → optimizer/advisor → decision ledger → evaluator → learning loop.
            {status?.last_action_at && <> Last AI action {relTime(status.last_action_at)}.</>}
          </p>
        </div>
        <span className={clsx(
          'text-[10px] font-mono px-2 py-1 rounded border uppercase tracking-widest shrink-0 font-semibold',
          mode === 'LIVE' ? 'text-terminal-red border-terminal-red/50 bg-terminal-red/10'
          : mode === 'PAPER' ? 'text-terminal-blue border-terminal-blue/50 bg-terminal-blue/10'
          : 'text-terminal-dim border-terminal-border bg-terminal-muted/30',
        )}>
          {mode} mode
        </span>
      </section>

      {/* ── pipeline flow ─────────────────────────────────────────── */}
      <SectionCard
        title="AI Pipeline Flow"
        right={
          <span className="font-mono text-[11px] text-terminal-dim">
            {status?.optimizer_running ? 'optimizer running' : status?.next_optimization_at ? `next opt ${relTime(status.next_optimization_at)}` : 'optimizer idle'}
          </span>
        }
      >
        {!loaded ? (
          <p className="font-mono text-xs text-terminal-ghost py-6 text-center">Loading pipeline…</p>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-2">
            {stages.map((s, i) => <StageCard key={s.key} stage={s} index={i} />)}
          </div>
        )}
      </SectionCard>

      {/* ── KPI row ───────────────────────────────────────────────── */}
      <section>
        <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest mb-3">Engine Telemetry</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          <KPICard label="Mode" value={modeMeta.value} positive={modeMeta.positive} highlight />
          <KPICard
            label="Optimizer"
            value={status?.optimizer_running ? 'RUNNING' : 'idle'}
            positive={status?.optimizer_running ? true : undefined}
            subLabel={status?.next_optimization_at ? `next ${relTime(status.next_optimization_at)}` : 'next —'}
          />
          <KPICard
            label="Last Cycle"
            value={status?.last_optimization_at ? relTime(status.last_optimization_at) : '—'}
            subLabel="optimization"
          />
          <KPICard label="Changes Today" value={status?.changes_today ?? '—'} />
          <KPICard label="Budget Left" value={status?.daily_budget_remaining ?? '—'} subLabel="changes/day" />
          <KPICard label="Active Rules" value={status?.active_rules_count ?? '—'} subLabel={`${status?.direct_ai_open_trades_count ?? 0} AI trades`} />
        </div>
      </section>

      {/* ── learned performance ───────────────────────────────────── */}
      <SectionCard
        title={`Learning Loop Outcomes — last ${perf?.window_days ?? 30}d`}
        right={<span className="font-mono text-[11px] text-terminal-dim">{perf?.total_trades ?? 0} trades scored</span>}
      >
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <KPICard label="Hit Rate" value={fmtPct(perf?.hit_rate)} positive={perf?.hit_rate != null ? perf.hit_rate >= 0.5 : undefined} />
          <KPICard label="Realized P&L" value={fmtUSD(perf?.realized_pnl)} positive={perf?.realized_pnl != null ? perf.realized_pnl >= 0 : undefined} />
          <KPICard label="Unrealized P&L" value={fmtUSD(perf?.unrealized_pnl)} positive={perf?.unrealized_pnl != null ? perf.unrealized_pnl >= 0 : undefined} />
          <KPICard label="ROI" value={fmtPct(perf?.roi)} positive={perf?.roi != null ? perf.roi >= 0 : undefined} />
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
            No scored trades in the window yet — the learning loop populates as the engine trades and outcomes close.
          </p>
        )}
      </SectionCard>

      {/* ── decision feed ─────────────────────────────────────────── */}
      <SectionCard
        title="Recent AI Decisions"
        right={<span className="font-mono text-[11px] text-terminal-dim">{feed.length} entries</span>}
      >
        {feed.length === 0 ? (
          <p className="font-mono text-xs text-terminal-ghost py-4 text-center">
            {loaded ? 'No AI decisions logged yet — entries appear as the engine reasons and acts.' : 'Loading…'}
          </p>
        ) : (
          <div className="flex flex-col divide-y divide-terminal-border/60">
            {feed.map((e) => <FeedRow key={e.id} entry={e} />)}
          </div>
        )}
      </SectionCard>
    </div>
  )
}
