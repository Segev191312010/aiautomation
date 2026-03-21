/**
 * FeedTab — Live autopilot activity feed + intervention queue.
 * Reads audit log from useAdvisorStore. Shows intervention items
 * with acknowledge/resolve actions in a side panel.
 */
import React, { useEffect, useState } from 'react'
import AIActivityFeed from '@/components/advisor/AIActivityFeed'
import { useAdvisorStore } from '@/store'
import type { AutopilotIntervention } from '@/types/advisor'
import {
  acknowledgeAutopilotIntervention,
  fetchAutopilotInterventions,
  resolveAutopilotIntervention,
} from '@/services/api'

// ── Intervention panel ────────────────────────────────────────────────────────

function fmtTs(value: string) {
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

interface InterventionPanelProps {
  items: AutopilotIntervention[]
  onAcknowledge: (id: number) => void
  onResolve: (id: number) => void
}

function InterventionPanel({ items, onAcknowledge, onResolve }: InterventionPanelProps) {
  return (
    <div className="bg-white border border-[var(--border)] rounded-xl p-5">
      <div className="flex items-center justify-between gap-2 mb-4">
        <div>
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Intervention Queue</h2>
          <p className="text-xs text-[var(--text-muted)]">
            Items that need human acknowledgement or resolution.
          </p>
        </div>
        <span className="text-xs font-mono text-[var(--text-muted)]">{items.length} open</span>
      </div>

      {items.length === 0 ? (
        <div className="flex items-center justify-center py-8 text-sm font-sans text-[var(--text-muted)]">
          No open intervention items.
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <div key={item.id} className="rounded-xl border border-[var(--border)] px-4 py-3">
              <div className="flex items-center justify-between gap-2 mb-1">
                <span className="text-sm font-semibold text-[var(--text-primary)] leading-snug">
                  {item.summary}
                </span>
                <span className="text-[9px] font-sans font-bold uppercase tracking-widest text-red-700 flex-shrink-0">
                  {item.severity}
                </span>
              </div>
              <p className="text-xs font-sans text-[var(--text-secondary)] leading-relaxed mb-2">
                {item.required_action}
              </p>
              <div className="text-[9px] font-mono text-[var(--text-muted)] mb-3">
                {item.category} · {item.source} · {item.symbol ?? 'system'} · {fmtTs(item.opened_at)}
              </div>
              <div className="flex items-center justify-end gap-2">
                {!item.acknowledged_at && (
                  <button
                    type="button"
                    onClick={() => onAcknowledge(item.id)}
                    className="text-xs font-sans text-[var(--text-secondary)] border border-[var(--border)]
                               px-2.5 py-1.5 rounded-lg hover:bg-[var(--bg-hover)] transition-colors"
                  >
                    Acknowledge
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => onResolve(item.id)}
                  className="text-xs font-sans font-semibold bg-indigo-600 text-white
                             px-2.5 py-1.5 rounded-lg hover:bg-indigo-700 transition-colors"
                >
                  Resolve
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function FeedTab() {
  const { auditLog, revertAction, fetchAuditLog } = useAdvisorStore()
  const [interventions, setInterventions] = useState<AutopilotIntervention[]>([])

  useEffect(() => {
    void fetchAuditLog(100)
    void fetchAutopilotInterventions(false).then(setInterventions).catch(() => {})
  }, [fetchAuditLog])

  async function handleAcknowledge(id: number) {
    await acknowledgeAutopilotIntervention(id)
    const updated = await fetchAutopilotInterventions(false)
    setInterventions(updated)
  }

  async function handleResolve(id: number) {
    await resolveAutopilotIntervention(id)
    const updated = await fetchAutopilotInterventions(false)
    setInterventions(updated)
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[1.2fr,0.8fr] gap-5">
      {/* Activity feed */}
      <div className="bg-white border border-[var(--border)] rounded-xl p-5">
        <div className="mb-4">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Live Activity Feed</h2>
          <p className="text-xs text-[var(--text-muted)] mt-0.5">
            Every meaningful autopilot action, rejection, revert, and manual override.
          </p>
        </div>
        <AIActivityFeed
          entries={auditLog}
          onRevert={(id) => void revertAction(id)}
        />
      </div>

      {/* Interventions */}
      <InterventionPanel
        items={interventions}
        onAcknowledge={(id) => void handleAcknowledge(id)}
        onResolve={(id) => void handleResolve(id)}
      />
    </div>
  )
}
