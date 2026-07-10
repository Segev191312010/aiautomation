import clsx from 'clsx'
import { SectionHeader } from '@/components/common/SectionHeader'
import { IconGrid, IconLightning, IconShield } from '@/components/icons'
import type { AIStatus, AuditLogEntry, EconomicReport, LearningMetrics } from '@/types/advisor'

type StageTone = 'active' | 'good' | 'warn' | 'danger' | 'idle'

interface Stage {
  key: string
  label: string
  detail: string
  tone: StageTone
}

interface Props {
  status: AIStatus | null
  auditLog: AuditLogEntry[]
  learningMetrics: LearningMetrics | null
  economicReport: EconomicReport | null
}

const toneClasses: Record<StageTone, string> = {
  active: 'border-indigo-200 bg-indigo-50 text-indigo-700',
  good: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  warn: 'border-amber-200 bg-amber-50 text-amber-700',
  danger: 'border-red-200 bg-red-50 text-red-700',
  idle: 'border-[var(--border)] bg-white text-[var(--text-secondary)]',
}

const toneLabels: Record<StageTone, string> = {
  active: 'LIVE',
  good: 'READY',
  warn: 'WATCH',
  danger: 'BLOCKED',
  idle: 'IDLE',
}

function relativeTime(value?: string | null) {
  if (!value) return 'none'
  const timestamp = new Date(value).getTime()
  if (Number.isNaN(timestamp)) return 'unknown'

  const diffMinutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60000))
  if (diffMinutes < 1) return 'just now'
  if (diffMinutes < 60) return `${diffMinutes}m ago`

  const diffHours = Math.floor(diffMinutes / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  return `${Math.floor(diffHours / 24)}d ago`
}

function formatMoney(value: number | null | undefined) {
  if (value == null) return '--'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(value)
}

function formatPercent(value: number | null | undefined) {
  if (value == null) return '--'
  return `${(value * 100).toFixed(1)}%`
}

function hasRecent(auditLog: AuditLogEntry[], pattern: RegExp) {
  return auditLog.some((entry) => pattern.test(`${entry.category} ${entry.action_type} ${entry.description}`))
}

function buildStages(status: AIStatus | null, auditLog: AuditLogEntry[], learningMetrics: LearningMetrics | null): Stage[] {
  const mode = status?.mode ?? 'OFF'
  const off = mode === 'OFF'
  const blocked = Boolean(status?.emergency_stop)
  const lossLocked = Boolean(status?.daily_loss_locked)
  const capabilityBlocked = status?.ai_capability === 'invalid_model' || status?.ai_capability === 'unconfigured'
  const optimizerSeen = hasRecent(auditLog, /optim/i)
  const decisionSeen = hasRecent(auditLog, /(decision|trade|order|signal|direct_ai|rule_lab)/i)
  const safetySeen = hasRecent(auditLog, /(safety|guardrail|kill|loss)/i)

  return [
    {
      key: 'trigger',
      label: 'Trigger',
      detail: blocked ? 'Kill switch active' : status?.optimizer_running ? 'Optimizer running' : off ? 'Engine dormant' : 'Runtime armed',
      tone: blocked ? 'danger' : status?.optimizer_running ? 'active' : off ? 'idle' : 'good',
    },
    {
      key: 'context',
      label: 'Context',
      detail: off ? 'No runtime context' : `${status?.active_rules_count ?? 0} rules / ${status?.open_positions_count ?? 0} positions`,
      tone: off ? 'idle' : capabilityBlocked ? 'warn' : 'good',
    },
    {
      key: 'llm',
      label: 'LLM Router',
      detail: capabilityBlocked ? status?.ai_capability ?? 'blocked' : status?.optimizer_running ? 'Reasoning now' : optimizerSeen ? 'Recent optimizer activity' : 'Ready',
      tone: off ? 'idle' : capabilityBlocked ? 'danger' : status?.optimizer_running ? 'active' : 'good',
    },
    {
      key: 'safety',
      label: 'Safety',
      detail: blocked ? 'Entries blocked' : lossLocked ? 'Daily loss lock' : safetySeen ? 'Recent safety event' : 'Clear',
      tone: blocked ? 'danger' : lossLocked ? 'warn' : off ? 'idle' : 'good',
    },
    {
      key: 'execute',
      label: 'Execute',
      detail: off ? 'No execution' : mode === 'LIVE' ? 'Live orders' : 'Paper orders',
      tone: blocked || lossLocked ? 'warn' : off ? 'idle' : mode === 'LIVE' ? 'active' : 'good',
    },
    {
      key: 'persist',
      label: 'Persist',
      detail: auditLog.length ? `${auditLog.length} recent audit rows` : 'No recent rows',
      tone: auditLog.length ? 'good' : off ? 'idle' : 'warn',
    },
    {
      key: 'evaluate',
      label: 'Evaluate',
      detail: learningMetrics ? `${learningMetrics.scored_decisions} scored decisions` : 'Awaiting metrics',
      tone: learningMetrics?.scored_decisions ? 'good' : off ? 'idle' : 'warn',
    },
    {
      key: 'learn',
      label: 'Learn',
      detail: status?.last_optimization_at ? `Last cycle ${relativeTime(status.last_optimization_at)}` : decisionSeen ? 'Recent decision evidence' : 'No cycle yet',
      tone: off ? 'idle' : status?.last_optimization_at || decisionSeen ? 'good' : 'warn',
    },
  ]
}

function StageCard({ stage, index }: { stage: Stage; index: number }) {
  return (
    <div className="min-w-0">
      <div className={clsx('flex h-full min-h-[124px] flex-col rounded-2xl border p-4', toneClasses[stage.tone])}>
        <div className="flex items-start justify-between gap-3">
          <div className="text-sm font-semibold text-[var(--text-primary)]">{stage.label}</div>
          <span className="rounded-full bg-white/70 px-2 py-0.5 text-[10px] font-mono font-semibold">{toneLabels[stage.tone]}</span>
        </div>
        <div className="mt-auto pt-5 text-xs leading-5">{stage.detail}</div>
      </div>
      {index < 7 && <div className="mx-auto my-2 h-5 w-px bg-[var(--border)] lg:hidden" aria-hidden="true" />}
    </div>
  )
}

function MetricTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="min-w-0 rounded-2xl border border-[var(--border)] bg-white p-4">
      <div className="shell-kicker truncate">{label}</div>
      <div className="mt-2 truncate text-lg font-semibold text-[var(--text-primary)]">{value}</div>
      {sub && <div className="mt-1 truncate text-xs text-[var(--text-muted)]">{sub}</div>}
    </div>
  )
}

export default function AISystemPanel({ status, auditLog, learningMetrics, economicReport }: Props) {
  const stages = buildStages(status, auditLog, learningMetrics)
  const capability = status?.ai_capability ?? 'disabled'
  const capabilityTone =
    capability === 'ready' ? 'good' :
    capability === 'degraded' || capability === 'unconfigured' ? 'warn' :
    capability === 'invalid_model' ? 'danger' :
    'idle'
  const recentDecisions = auditLog.slice(0, 6)

  return (
    <div className="space-y-6">
      <section className="shell-panel p-5 sm:p-6">
        <SectionHeader
          icon={<IconLightning className="h-3.5 w-3.5 text-[var(--accent)]" />}
          eyebrow="AI System"
          title="Pipeline State"
          badge={<span className={clsx('rounded-full border px-3 py-1 text-[10px] font-mono font-semibold uppercase', toneClasses[capabilityTone])}>{capability}</span>}
        />
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
          {stages.map((stage, index) => (
            <StageCard key={stage.key} stage={stage} index={index} />
          ))}
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(360px,1.05fr)]">
        <div className="shell-panel p-5 sm:p-6">
          <SectionHeader
            icon={<IconShield className="h-3.5 w-3.5 text-emerald-500" />}
            eyebrow="Capability"
            title="Provider Readiness"
            badge={<span className="shell-chip px-3 py-1 text-[10px] font-mono">{status?.ai_provider ?? 'none'}</span>}
          />
          <div className="grid gap-3 sm:grid-cols-2">
            <MetricTile label="Provider Key" value={status?.ai_provider_configured ? 'Configured' : 'Missing'} />
            <MetricTile label="Primary Model" value={status?.ai_primary_model ?? '--'} />
            <MetricTile label="Fallback Model" value={status?.ai_fallback_model ?? '--'} />
            <MetricTile label="Cost ROI" value={economicReport?.roi_estimate == null ? '--' : `${economicReport.roi_estimate.toFixed(1)}x`} sub={economicReport ? `${formatMoney(economicReport.total_cost)} cost` : undefined} />
          </div>
          {(status?.ai_capability_errors.length || status?.ai_capability_warnings.length) ? (
            <div className="mt-4 space-y-2">
              {status.ai_capability_errors.map((item) => (
                <div key={item} className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{item}</div>
              ))}
              {status.ai_capability_warnings.map((item) => (
                <div key={item} className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">{item}</div>
              ))}
            </div>
          ) : null}
        </div>

        <div className="shell-panel p-5 sm:p-6">
          <SectionHeader
            icon={<IconGrid className="h-3.5 w-3.5 text-indigo-500" />}
            eyebrow="Learning"
            title="Outcome Loop"
            badge={<span className="shell-chip px-3 py-1 text-[10px] font-mono">{learningMetrics?.window_days ?? 30}d</span>}
          />
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <MetricTile label="Hit Rate" value={formatPercent(learningMetrics?.hit_rate)} />
            <MetricTile label="Scored" value={learningMetrics ? `${learningMetrics.scored_decisions} / ${learningMetrics.total_decisions}` : '--'} />
            <MetricTile label="P&L Impact" value={formatMoney(learningMetrics?.net_pnl_impact)} />
            <MetricTile label="Cost / Decision" value={economicReport ? formatMoney(economicReport.cost_per_decision) : '--'} />
          </div>
          <div className="mt-5 divide-y divide-[var(--border)] rounded-2xl border border-[var(--border)] bg-white">
            {recentDecisions.length ? recentDecisions.map((entry) => (
              <div key={entry.id} className="grid gap-2 px-4 py-3 text-sm sm:grid-cols-[120px_minmax(0,1fr)_90px]">
                <span className="font-mono text-[11px] uppercase text-[var(--text-muted)]">{entry.category}</span>
                <span className="min-w-0 truncate text-[var(--text-primary)]">{entry.description}</span>
                <span className="font-mono text-[11px] text-[var(--text-muted)] sm:text-right">{relativeTime(entry.timestamp)}</span>
              </div>
            )) : (
              <div className="px-4 py-8 text-center text-sm text-[var(--text-muted)]">No recent AI decisions.</div>
            )}
          </div>
        </div>
      </section>
    </div>
  )
}
