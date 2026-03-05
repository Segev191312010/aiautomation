/**
 * AlertList — table of configured alerts with toggle, edit, and delete actions.
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

// ── Column headers ────────────────────────────────────────────────────────────

const COLUMNS = [
  'Name',
  'Symbol',
  'Condition',
  'Type',
  'Enabled',
  'Last Triggered',
  'Actions',
] as const

// ── TypeBadge ─────────────────────────────────────────────────────────────────

function TypeBadge({ alertType }: { alertType: Alert['alert_type'] }) {
  const isOneShot = alertType === 'one_shot'
  return (
    <span
      className={[
        'px-2 py-0.5 rounded-lg text-[10px] font-sans font-medium uppercase tracking-wide',
        isOneShot
          ? 'bg-indigo-500/15 text-indigo-400 border border-indigo-500/25'
          : 'bg-terminal-amber/15 text-terminal-amber border border-terminal-amber/25',
      ].join(' ')}
    >
      {isOneShot ? 'ONE-SHOT' : 'RECURRING'}
    </span>
  )
}

// ── EnabledToggle — modern pill switch with sliding circle ────────────────────

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
        'relative inline-flex w-9 h-5 rounded-full transition-colors duration-200 focus:outline-none',
        enabled
          ? 'bg-indigo-500/70'
          : 'bg-white/10',
      ].join(' ')}
    >
      <span
        className={[
          'absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform duration-200',
          enabled ? 'translate-x-4' : 'translate-x-0.5',
        ].join(' ')}
      />
    </button>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function AlertList({ onEdit }: Props) {
  const alerts = useAlertStore((s) => s.alerts)
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
      <div className="flex items-center justify-center py-14">
        <p className="text-sm font-sans text-terminal-ghost">
          No alerts configured. Create one to get notified.
        </p>
      </div>
    )
  }

  // ── Table ────────────────────────────────────────────────────────────────

  return (
    <div className="glass rounded-2xl overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-white/[0.06]">
              {COLUMNS.map((col) => (
                <th
                  key={col}
                  className="px-3 py-2.5 text-left text-xs font-sans font-medium text-terminal-dim tracking-wide"
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {alerts.map((alert) => (
              <tr
                key={alert.id}
                className="text-sm font-sans text-terminal-dim border-b border-white/[0.06] last:border-0 hover:bg-white/[0.03] transition-colors"
              >
                {/* Name */}
                <td className="px-3 py-3 text-terminal-text whitespace-nowrap">
                  {alert.name}
                </td>

                {/* Symbol — keep font-mono for ticker */}
                <td className="px-3 py-3 font-mono text-terminal-amber font-semibold whitespace-nowrap">
                  {alert.symbol}
                </td>

                {/* Condition */}
                <td className="px-3 py-3 font-mono whitespace-nowrap">
                  {formatConditionSummary(alert.condition)}
                </td>

                {/* Type */}
                <td className="px-3 py-3 whitespace-nowrap">
                  <TypeBadge alertType={alert.alert_type} />
                </td>

                {/* Enabled */}
                <td className="px-3 py-3">
                  <EnabledToggle
                    enabled={alert.enabled}
                    onClick={() => handleToggle(alert)}
                  />
                </td>

                {/* Last Triggered — keep font-mono for date */}
                <td className="px-3 py-3 whitespace-nowrap font-mono text-terminal-ghost text-xs">
                  {alert.last_triggered
                    ? new Date(alert.last_triggered).toLocaleString()
                    : '--'}
                </td>

                {/* Actions */}
                <td className="px-3 py-3">
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      onClick={() => onEdit(alert)}
                      title="Edit alert"
                      className="text-terminal-ghost hover:text-indigo-400 transition-colors text-xs font-sans"
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(alert)}
                      title="Delete alert"
                      className="text-terminal-ghost hover:text-red-400 transition-colors text-xs font-sans"
                    >
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
