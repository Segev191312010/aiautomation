/**
 * AlertList — card list of configured alerts with toggle, edit, and delete actions.
 *
 * Data: useAlertStore().alerts
 * Mutations: api.toggleAlert / api.deleteAlert,
 *            followed by useAlertStore.getState().loadAlerts() to reconcile.
 */
import type { Alert } from '@/types'
import { useAlertStore } from '@/store'
import * as api from '@/services/api'
import { formatConditionSummary } from '@/utils/conditionHelpers'

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  onEdit: (alert: Alert) => void
}

// ── TypeBadge ─────────────────────────────────────────────────────────────────

function TypeBadge({ alertType }: { alertType: Alert['alert_type'] }) {
  const isOneShot = alertType === 'one_shot'
  return (
    <span
      className={[
        'px-2 py-0.5 rounded-md text-[10px] font-sans font-semibold uppercase tracking-wider',
        isOneShot
          ? 'bg-indigo-50 text-indigo-600 border border-indigo-100'
          : 'bg-amber-50 text-amber-600 border border-amber-600/25',
      ].join(' ')}
    >
      {isOneShot ? 'One-shot' : 'Recurring'}
    </span>
  )
}

// ── EnabledToggle — modern pill switch ────────────────────────────────────────

function EnabledToggle({
  enabled,
  onClick,
}: {
  enabled: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={enabled ? 'Disable alert' : 'Enable alert'}
      aria-pressed={enabled}
      className={[
        'relative inline-flex w-9 h-5 rounded-full transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-offset-transparent',
        enabled
          ? 'bg-emerald-600/70 focus:ring-green-600/40'
          : 'bg-zinc-800 focus:ring-zinc-200',
      ].join(' ')}
    >
      <span
        className={[
          'absolute top-0.5 w-4 h-4 rounded-full shadow-md transition-transform duration-200',
          enabled ? 'translate-x-4 bg-zinc-900' : 'translate-x-0.5 bg-zinc-600',
        ].join(' ')}
      />
    </button>
  )
}

// ── Status indicator dot ──────────────────────────────────────────────────────

function StatusDot({ alert }: { alert: Alert }) {
  if (!alert.enabled) return <span className="w-1.5 h-1.5 rounded-full bg-zinc-600" />
  if (alert.last_triggered) return <span className="w-1.5 h-1.5 rounded-full bg-amber-600" />
  return <span className="w-1.5 h-1.5 rounded-full bg-emerald-600 animate-pulse" />
}

// ── AlertCard ─────────────────────────────────────────────────────────────────

interface AlertCardProps {
  alert: Alert
  onEdit: () => void
  onToggle: () => void
  onDelete: () => void
}

function AlertCard({ alert, onEdit, onToggle, onDelete }: AlertCardProps) {
  // Determine left border color based on alert state
  const borderColor = !alert.enabled
    ? 'border-l-zinc-600/30'
    : alert.last_triggered
      ? 'border-l-amber-600/60'
      : 'border-l-green-600/60'

  const dimmed = !alert.enabled

  return (
    <div
      className={[
        'group relative flex items-center gap-4 px-4 py-3.5',
        'border-l-2 border-b border-b-zinc-800 last:border-b-0',
        'hover:bg-zinc-900 transition-colors duration-100',
        borderColor,
        dimmed ? 'opacity-60' : '',
      ].join(' ')}
    >
      {/* Status dot */}
      <div className="shrink-0">
        <StatusDot alert={alert} />
      </div>

      {/* Main content — grows */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5 flex-wrap">
          {/* Symbol */}
          <span className="font-mono text-sm font-semibold text-amber-600">
            {alert.symbol}
          </span>
          {/* Condition summary */}
          <span className="font-mono text-xs text-zinc-400 truncate">
            {formatConditionSummary(alert.condition)}
          </span>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {/* Name */}
          <span className="text-xs font-sans text-zinc-500 truncate max-w-[180px]">
            {alert.name}
          </span>
          <TypeBadge alertType={alert.alert_type} />
          {/* Last triggered */}
          {alert.last_triggered && (
            <span className="text-[10px] font-mono text-zinc-500">
              last: {new Date(alert.last_triggered).toLocaleDateString(undefined, {
                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
              })}
            </span>
          )}
        </div>
      </div>

      {/* Right side controls */}
      <div className="flex items-center gap-3 shrink-0">
        <EnabledToggle enabled={alert.enabled} onClick={onToggle} />

        {/* Action buttons — visible on hover */}
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-100">
          <button
            type="button"
            onClick={onEdit}
            title="Edit alert"
            className={[
              'px-2.5 py-1 rounded-lg text-[11px] font-sans font-medium',
              'text-zinc-500 hover:text-indigo-600',
              'hover:bg-indigo-50 border border-transparent hover:border-indigo-100',
              'transition-all duration-100',
            ].join(' ')}
          >
            Edit
          </button>
          <button
            type="button"
            onClick={onDelete}
            title="Delete alert"
            className={[
              'px-2.5 py-1 rounded-lg text-[11px] font-sans font-medium',
              'text-zinc-500 hover:text-red-400',
              'hover:bg-red-500/10 border border-transparent hover:border-red-300',
              'transition-all duration-100',
            ].join(' ')}
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function AlertList({ onEdit }: Props) {
  const alerts     = useAlertStore((s) => s.alerts)
  const loadAlerts = useAlertStore((s) => s.loadAlerts)

  async function handleToggle(alert: Alert) {
    await api.toggleAlert(alert.id)
    await loadAlerts()
  }

  async function handleDelete(alert: Alert) {
    const confirmed = window.confirm(
      `Delete alert "${alert.name}"? This cannot be undone.`,
    )
    if (!confirmed) return
    await api.deleteAlert(alert.id)
    await loadAlerts()
  }

  // ── Empty state ──────────────────────────────────────────────────────────

  if (alerts.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-3">
        <div className="w-10 h-10 rounded-xl bg-zinc-900 flex items-center justify-center">
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5 text-zinc-500">
            <path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.89 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z" />
          </svg>
        </div>
        <div className="text-center">
          <p className="text-sm font-sans text-zinc-400 font-medium">No alerts configured</p>
          <p className="text-xs font-sans text-zinc-500 mt-1">Create one to get notified when conditions are met.</p>
        </div>
      </div>
    )
  }

  // ── Sort: enabled first, then by created_at DESC ─────────────────────────

  const sorted = [...alerts].sort((a, b) => {
    if (a.enabled !== b.enabled) return a.enabled ? -1 : 1
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  })

  return (
    <div className="card rounded-2xl overflow-hidden border border-zinc-800">
      {sorted.map((alert) => (
        <AlertCard
          key={alert.id}
          alert={alert}
          onEdit={() => onEdit(alert)}
          onToggle={() => handleToggle(alert)}
          onDelete={() => handleDelete(alert)}
        />
      ))}
    </div>
  )
}
