import { useEffect, useState } from 'react'
import type { Rule } from '@/types'
import type {
  RulePromotionReadiness,
  RuleValidationRecord,
  RuleVersionRecord,
} from '@/types/advisor'
import {
  fetchAutopilotRulePromotionReadiness,
  fetchAutopilotRuleValidations,
  fetchAutopilotRuleVersions,
  manualPauseAutopilotRule,
  manualRetireAutopilotRule,
} from '@/services/api'

function statusTone(status?: string) {
  switch (status) {
    case 'active':
      return 'bg-emerald-50 text-emerald-700 border-emerald-200'
    case 'paper':
      return 'bg-amber-50 text-amber-700 border-amber-200'
    case 'paused':
      return 'bg-slate-100 text-slate-700 border-slate-200'
    case 'retired':
      return 'bg-red-50 text-red-700 border-red-200'
    default:
      return 'bg-indigo-50 text-indigo-700 border-indigo-200'
  }
}

function fmtTimestamp(value?: string | null) {
  if (!value) return '--'
  try {
    return new Date(value).toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return value
  }
}

function fmtHitRate(value?: number | null) {
  if (value == null) return '--'
  return `${(value * 100).toFixed(1)}%`
}

function fmtSignedUsd(value?: number | null) {
  if (value == null) return '--'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
    signDisplay: 'exceptZero',
  }).format(value)
}

function fmtMetric(value?: number | null, digits = 2) {
  if (value == null) return '--'
  return value.toFixed(digits)
}

function gateTone(eligible?: boolean) {
  return eligible
    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
    : 'bg-amber-50 text-amber-700 border-amber-200'
}

interface Props {
  rules: Rule[]
  onRefresh: () => Promise<void>
}

export default function AutopilotRuleLab({ rules, onRefresh }: Props) {
  const [selectedRuleId, setSelectedRuleId] = useState<string | null>(null)
  const [versions, setVersions] = useState<RuleVersionRecord[]>([])
  const [validations, setValidations] = useState<RuleValidationRecord[]>([])
  const [promotionReadiness, setPromotionReadiness] = useState<RulePromotionReadiness | null>(null)
  const [detailsLoading, setDetailsLoading] = useState(false)
  const [detailsError, setDetailsError] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  async function loadRuleDetails(ruleId: string) {
    setDetailsLoading(true)
    setDetailsError(null)
    try {
      const [nextVersions, nextValidations, nextReadiness] = await Promise.all([
        fetchAutopilotRuleVersions(ruleId),
        fetchAutopilotRuleValidations(ruleId),
        fetchAutopilotRulePromotionReadiness(ruleId),
      ])
      setVersions(nextVersions)
      setValidations(nextValidations)
      setPromotionReadiness(nextReadiness)
    } catch (err) {
      setVersions([])
      setValidations([])
      setPromotionReadiness(null)
      setDetailsError(err instanceof Error ? err.message : 'Failed to load rule details')
    } finally {
      setDetailsLoading(false)
    }
  }

  useEffect(() => {
    if (!rules.length) {
      setSelectedRuleId(null)
      return
    }
    if (!selectedRuleId || !rules.some((rule) => rule.id === selectedRuleId)) {
      setSelectedRuleId(rules[0].id)
    }
  }, [rules, selectedRuleId])

  useEffect(() => {
    if (!selectedRuleId) {
      setVersions([])
      setValidations([])
      setPromotionReadiness(null)
      setDetailsError(null)
      return
    }
    let cancelled = false
    void (async () => {
      setDetailsLoading(true)
      setDetailsError(null)
      try {
        const [nextVersions, nextValidations, nextReadiness] = await Promise.all([
          fetchAutopilotRuleVersions(selectedRuleId),
          fetchAutopilotRuleValidations(selectedRuleId),
          fetchAutopilotRulePromotionReadiness(selectedRuleId),
        ])
        if (cancelled) return
        setVersions(nextVersions)
        setValidations(nextValidations)
        setPromotionReadiness(nextReadiness)
      } catch (err) {
        if (cancelled) return
        setVersions([])
        setValidations([])
        setPromotionReadiness(null)
        setDetailsError(err instanceof Error ? err.message : 'Failed to load rule details')
      } finally {
        if (!cancelled) setDetailsLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [selectedRuleId])

  const selectedRule = rules.find((rule) => rule.id === selectedRuleId) ?? null

  async function handlePause(rule: Rule) {
    const reason = window.prompt(`Pause rule "${rule.name}"`, 'Paused by operator')
    if (reason == null) return
    setActionLoading(rule.id)
    try {
      await manualPauseAutopilotRule(rule.id, reason)
      await onRefresh()
      await loadRuleDetails(rule.id)
    } finally {
      setActionLoading(null)
    }
  }

  async function handleRetire(rule: Rule) {
    const reason = window.prompt(`Retire rule "${rule.name}"`, 'Retired by operator')
    if (reason == null) return
    setActionLoading(rule.id)
    try {
      await manualRetireAutopilotRule(rule.id, reason)
      await onRefresh()
      await loadRuleDetails(rule.id)
    } finally {
      setActionLoading(null)
    }
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[1.45fr,0.95fr] gap-5">
      <section className="rounded-2xl border border-[var(--border)] bg-white overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border)]">
          <div>
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">AI Rules</h2>
            <p className="text-xs text-[var(--text-muted)]">
              Structured rules owned by Autopilot. Human actions are limited to emergency pause and retire.
            </p>
          </div>
          <div className="text-xs text-[var(--text-muted)]">{rules.length} rules</div>
        </div>

        {!rules.length ? (
          <div className="px-5 py-10 text-sm text-[var(--text-muted)]">
            No AI-managed rules yet. Draft and paper rules created by the Rule Lab will appear here.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px]">
              <thead>
                <tr className="border-b border-[var(--border)] text-left text-[11px] uppercase tracking-[0.12em] text-[var(--text-muted)]">
                  <th className="px-5 py-3 font-medium">Rule</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Scope</th>
                  <th className="px-4 py-3 font-medium">Style</th>
                  <th className="px-4 py-3 font-medium">Version</th>
                  <th className="px-4 py-3 font-medium">Reason</th>
                  <th className="px-4 py-3 font-medium text-right">Emergency Actions</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => {
                  const selected = rule.id === selectedRuleId
                  return (
                    <tr
                      key={rule.id}
                      className={selected ? 'bg-indigo-50/60' : 'hover:bg-[var(--bg-hover)]'}
                    >
                      <td className="px-5 py-3">
                        <button
                          type="button"
                          onClick={() => setSelectedRuleId(rule.id)}
                          className="text-left"
                        >
                          <div className="font-medium text-[var(--text-primary)]">{rule.name}</div>
                          <div className="text-xs text-[var(--text-muted)]">
                            {rule.symbol || rule.universe || 'Universe'} - {rule.conditions.length} conditions
                          </div>
                        </button>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex rounded-full border px-2 py-1 text-xs font-semibold ${statusTone(rule.status)}`}>
                          {rule.status ?? 'active'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-[var(--text-secondary)]">
                        {rule.symbol || rule.universe || '--'}
                      </td>
                      <td className="px-4 py-3 text-sm text-[var(--text-secondary)]">
                        {rule.hold_style ?? '--'}
                      </td>
                      <td className="px-4 py-3 text-sm text-[var(--text-secondary)]">
                        v{rule.version ?? 1}
                      </td>
                      <td className="px-4 py-3 text-sm text-[var(--text-secondary)] max-w-[240px]">
                        <div className="truncate" title={rule.ai_reason ?? rule.thesis ?? ''}>
                          {rule.ai_reason ?? rule.thesis ?? '--'}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => void handlePause(rule)}
                            disabled={actionLoading === rule.id || rule.status === 'paused' || rule.status === 'retired'}
                            className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] disabled:opacity-50"
                          >
                            Pause
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleRetire(rule)}
                            disabled={actionLoading === rule.id || rule.status === 'retired'}
                            className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
                          >
                            Retire
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <aside className="rounded-2xl border border-[var(--border)] bg-white p-5 space-y-4">
        <div>
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Version History</h2>
          <p className="text-xs text-[var(--text-muted)]">
            Immutable rule snapshots, validation evidence, and promotion-gate state for the selected rule.
          </p>
        </div>

        {selectedRule ? (
          <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-hover)]/70 p-4 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="font-medium text-[var(--text-primary)]">{selectedRule.name}</div>
              <span className={`inline-flex rounded-full border px-2 py-1 text-xs font-semibold ${statusTone(selectedRule.status)}`}>
                {selectedRule.status ?? 'active'}
              </span>
            </div>
            <div className="text-xs text-[var(--text-muted)]">
              {selectedRule.thesis ?? selectedRule.ai_reason ?? 'No AI rationale recorded yet.'}
            </div>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-muted)]">Owner</div>
                <div className="mt-1 text-[var(--text-primary)]">{selectedRule.created_by ?? 'human'}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-muted)]">Updated</div>
                <div className="mt-1 text-[var(--text-primary)]">{fmtTimestamp(selectedRule.updated_at)}</div>
              </div>
            </div>
          </div>
        ) : (
          <div className="text-sm text-[var(--text-muted)]">Select a rule to inspect its version history.</div>
        )}

        {detailsError && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            {detailsError}
          </div>
        )}

        <div className="rounded-xl border border-[var(--border)] px-4 py-4 space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-[var(--text-primary)]">Promotion Gate</h3>
              <p className="text-xs text-[var(--text-muted)]">
                Paper rules only move to active after validation evidence clears every promotion check.
              </p>
            </div>
            {promotionReadiness && (
              <span className={`inline-flex rounded-full border px-2 py-1 text-xs font-semibold ${gateTone(promotionReadiness.eligible)}`}>
                {promotionReadiness.eligible ? 'Ready' : 'Blocked'}
              </span>
            )}
          </div>

          {detailsLoading && !promotionReadiness ? (
            <div className="text-sm text-[var(--text-muted)]">Loading promotion status...</div>
          ) : promotionReadiness ? (
            <>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <div className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-muted)]">Rule Status</div>
                  <div className="mt-1 text-[var(--text-primary)]">{promotionReadiness.status}</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-muted)]">Latest Validation</div>
                  <div className="mt-1 text-[var(--text-primary)]">
                    {promotionReadiness.latest_validation
                      ? `${promotionReadiness.latest_validation.validation_mode} • ${fmtTimestamp(promotionReadiness.latest_validation.created_at)}`
                      : '--'}
                  </div>
                </div>
              </div>

              {promotionReadiness.reasons.length ? (
                <div>
                  <div className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-muted)]">Blocking Reasons</div>
                  <ul className="mt-2 space-y-1 text-sm text-[var(--text-secondary)]">
                    {promotionReadiness.reasons.map((reason) => (
                      <li key={reason}>• {reason}</li>
                    ))}
                  </ul>
                </div>
              ) : (
                <div className="text-sm text-emerald-700">
                  Latest validation is clear. This paper rule is promotion-ready when the release flow allows it.
                </div>
              )}
              {promotionReadiness.data_quality_note && (
                <div className="mt-2 text-xs italic text-[var(--text-muted)]">
                  {promotionReadiness.data_quality_note}
                </div>
              )}
            </>
          ) : (
            <div className="text-sm text-[var(--text-muted)]">No promotion status available for this rule yet.</div>
          )}
        </div>

        <div>
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">Validation Runs</h3>
          <p className="text-xs text-[var(--text-muted)] mt-1">
            Recorded paper, replay, or manual evidence that backs promotion decisions.
          </p>
        </div>

        {detailsLoading && !validations.length ? (
          <div className="text-sm text-[var(--text-muted)]">Loading validation history...</div>
        ) : validations.length ? (
          <div className="space-y-3">
            {validations.map((run) => (
              <div key={`${run.version}:${run.validation_mode}:${run.created_at}`} className="rounded-xl border border-[var(--border)] px-4 py-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <div className="font-medium text-[var(--text-primary)]">v{run.version}</div>
                    <span className={`inline-flex rounded-full border px-2 py-1 text-[11px] font-semibold ${gateTone(run.passed)}`}>
                      {run.passed ? 'Passed' : 'Failed'}
                    </span>
                  </div>
                  <div className="text-xs text-[var(--text-muted)]">{fmtTimestamp(run.created_at)}</div>
                </div>
                <div className="mt-1 text-xs text-[var(--text-muted)]">{run.validation_mode}</div>
                <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <div className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-muted)]">Trades</div>
                    <div className="mt-1 text-[var(--text-primary)]">{run.trades_count}</div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-muted)]">Hit Rate</div>
                    <div className="mt-1 text-[var(--text-primary)]">{fmtHitRate(run.hit_rate)}</div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-muted)]">Net P&L</div>
                    <div className="mt-1 text-[var(--text-primary)]">{fmtSignedUsd(run.net_pnl)}</div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-muted)]">Expectancy</div>
                    <div className="mt-1 text-[var(--text-primary)]">{fmtMetric(run.expectancy)}</div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-muted)]">Max Drawdown</div>
                    <div className="mt-1 text-[var(--text-primary)]">
                      {run.max_drawdown != null ? `${run.max_drawdown.toFixed(1)}%` : '--'}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-muted)]">Overlap Score</div>
                    <div className="mt-1 text-[var(--text-primary)]">{fmtMetric(run.overlap_score)}</div>
                  </div>
                </div>
                {(run.evaluated_closed_count != null || run.data_quality) && (
                  <div className="mt-3 flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-[var(--text-muted)]">
                    <span className="font-medium">Evidence:</span>
                    {run.evaluated_closed_count != null && (
                      <span>{run.evaluated_closed_count} canonical trades</span>
                    )}
                    {run.excluded_legacy_count != null && run.excluded_legacy_count > 0 && (
                      <span>({run.excluded_legacy_count} legacy excluded)</span>
                    )}
                    {run.data_quality && (
                      <span className={`inline-flex rounded-full border px-1.5 py-0.5 text-[10px] font-semibold ${
                        run.data_quality === 'canonical'
                          ? 'border-emerald-200 text-emerald-700'
                          : 'border-amber-200 text-amber-700'
                      }`}>
                        {run.data_quality}
                      </span>
                    )}
                  </div>
                )}
                {run.notes && (
                  <div className="mt-3 text-sm text-[var(--text-secondary)]">{run.notes}</div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-sm text-[var(--text-muted)]">No validation runs recorded for this rule yet.</div>
        )}

        <div>
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">Version History</h3>
          <p className="text-xs text-[var(--text-muted)] mt-1">
            Immutable snapshots of rule structure, rationale, and status transitions.
          </p>
        </div>

        {detailsLoading && !versions.length ? (
          <div className="text-sm text-[var(--text-muted)]">Loading version history...</div>
        ) : versions.length ? (
          <div className="space-y-3">
            {versions.map((version) => (
              <div key={`${version.rule_id}:${version.version}:${version.created_at}`} className="rounded-xl border border-[var(--border)] px-4 py-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="font-medium text-[var(--text-primary)]">v{version.version}</div>
                  <div className="text-xs text-[var(--text-muted)]">{fmtTimestamp(version.created_at)}</div>
                </div>
                <div className="mt-1 text-xs text-[var(--text-muted)]">
                  {version.author ?? 'ai'} - {version.status ?? 'active'}
                </div>
                <div className="mt-2 text-sm text-[var(--text-secondary)]">
                  {version.note ?? 'Snapshot recorded without an explicit diff summary.'}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-sm text-[var(--text-muted)]">No version snapshots recorded for this rule yet.</div>
        )}
      </aside>
    </div>
  )
}
