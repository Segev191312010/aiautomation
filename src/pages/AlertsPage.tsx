/**
 * AlertsPage — manage price-based alerts.
 *
 * Alerts fire when:
 *  • price crosses above / below a threshold
 *  • % change exceeds a positive/negative bound
 *  • volume exceeds a multiple of average volume
 *
 * Triggered alerts surface as browser notifications + an in-page toast.
 * Evaluation runs in the global `useAlerts` hook (see hooks/useAlerts.ts).
 */
import React, { useEffect, useState } from 'react'
import clsx from 'clsx'
import { useBotStore } from '@/store'
import { fetchAlerts, createAlert, updateAlert, deleteAlert } from '@/services/api'
import type { Alert } from '@/services/mockBackend'

const CONDITION_LABELS: Record<Alert['condition'], string> = {
  price_above:     'Price ≥',
  price_below:     'Price ≤',
  pct_change_up:   '% Change ≥',
  pct_change_down: '% Change ≤ -',
  volume_spike:    'Volume × Avg ≥',
}

export default function AlertsPage() {
  const { mockMode } = useBotStore()
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [creating, setCreating] = useState(false)
  const [draft, setDraft]   = useState<Omit<Alert, 'id' | 'triggered' | 'created'>>({
    symbol:    'AAPL',
    condition: 'price_above',
    value:     250,
    enabled:   true,
    message:   '',
  })

  const reload = async () => setAlerts(await fetchAlerts())

  useEffect(() => {
    reload()
    // Refresh every few seconds so triggered states update from the
    // background evaluator (see useAlerts hook).
    const t = setInterval(reload, 3_000)
    return () => clearInterval(t)
  }, [])

  const handleCreate = async () => {
    await createAlert(draft)
    setCreating(false)
    setDraft({ symbol: 'AAPL', condition: 'price_above', value: 250, enabled: true, message: '' })
    reload()
  }

  const handleToggle = async (a: Alert) => {
    await updateAlert(a.id, { enabled: !a.enabled, triggered: a.enabled ? a.triggered : false })
    reload()
  }

  const handleDelete = async (id: string) => {
    if (!window.confirm('Delete this alert?')) return
    await deleteAlert(id)
    reload()
  }

  const enabled = alerts.filter((a) => a.enabled).length
  const fired   = alerts.filter((a) => a.triggered).length

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-terminal-text">Price Alerts</h2>
          <p className="text-xs text-terminal-dim mt-0.5">
            {alerts.length} alert{alerts.length !== 1 ? 's' : ''} ·{' '}
            <span className="text-terminal-green">{enabled} enabled</span> ·{' '}
            <span className="text-terminal-amber">{fired} triggered</span>
            {mockMode && <span className="ml-2 text-[10px] font-mono text-terminal-amber">[MOCK]</span>}
          </p>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="text-xs font-mono px-3 py-1.5 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 transition-colors"
        >
          + New Alert
        </button>
      </div>

      {alerts.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-terminal-ghost">
          <svg viewBox="0 0 24 24" fill="currentColor" className="w-12 h-12 mb-3 opacity-30">
            <path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.63-5.64-4.5-6.32V4a1.5 1.5 0 0 0-3 0v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z" />
          </svg>
          <p className="text-sm font-mono mb-3">No alerts yet</p>
          <button
            onClick={() => setCreating(true)}
            className="text-xs font-mono px-4 py-1.5 rounded bg-terminal-green/20 border border-terminal-green/40 text-terminal-green hover:bg-terminal-green/30 transition-colors"
          >
            Create your first alert
          </button>
        </div>
      ) : (
        <div className="bg-terminal-surface border border-terminal-border rounded-lg overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-terminal-border">
                {['Symbol', 'Condition', 'Threshold', 'Status', 'Last fired', ''].map((c, i) => (
                  <th key={i} className="py-2 px-3 text-[10px] font-mono uppercase tracking-widest text-terminal-ghost font-normal text-left">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {alerts.map((a) => (
                <tr key={a.id} className="border-b border-terminal-border/50 hover:bg-terminal-muted/20 transition-colors">
                  <td className="py-2 px-3 font-mono font-semibold text-terminal-text">{a.symbol}</td>
                  <td className="py-2 px-3 font-mono text-terminal-dim">{CONDITION_LABELS[a.condition]}</td>
                  <td className="py-2 px-3 font-mono text-terminal-text tabular-nums">{a.value}</td>
                  <td className="py-2 px-3">
                    <button
                      onClick={() => handleToggle(a)}
                      className={clsx(
                        'inline-flex items-center gap-1.5 text-[11px] font-mono px-2 py-0.5 rounded border transition-colors',
                        a.triggered
                          ? 'border-terminal-amber/50 text-terminal-amber bg-terminal-amber/5'
                          : a.enabled
                            ? 'border-terminal-green/40 text-terminal-green bg-terminal-green/5'
                            : 'border-terminal-border text-terminal-ghost',
                      )}
                    >
                      <span className={clsx(
                        'w-1.5 h-1.5 rounded-full',
                        a.triggered ? 'bg-terminal-amber animate-pulse' :
                        a.enabled   ? 'bg-terminal-green' : 'bg-terminal-ghost',
                      )} />
                      {a.triggered ? 'TRIGGERED' : a.enabled ? 'ARMED' : 'DISABLED'}
                    </button>
                  </td>
                  <td className="py-2 px-3 font-mono text-terminal-dim text-[11px]">
                    {a.last_fired ? new Date(a.last_fired).toLocaleString() : '—'}
                  </td>
                  <td className="py-2 px-3 text-right">
                    <button
                      onClick={() => handleDelete(a.id)}
                      className="text-[11px] font-mono text-terminal-ghost hover:text-terminal-red transition-colors"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {creating && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-terminal-elevated border border-terminal-border rounded-lg w-full max-w-md p-5 flex flex-col gap-3">
            <h3 className="text-sm font-semibold text-terminal-text">New Alert</h3>

            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-mono text-terminal-ghost uppercase">Symbol</span>
              <input
                value={draft.symbol}
                onChange={(e) => setDraft({ ...draft, symbol: e.target.value.toUpperCase() })}
                className="bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-xs font-mono text-terminal-text focus:border-terminal-blue focus:outline-none uppercase"
              />
            </label>

            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-mono text-terminal-ghost uppercase">Condition</span>
              <select
                value={draft.condition}
                onChange={(e) => setDraft({ ...draft, condition: e.target.value as Alert['condition'] })}
                className="bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-xs font-mono text-terminal-text focus:border-terminal-blue focus:outline-none"
              >
                <option value="price_above">Price crosses above</option>
                <option value="price_below">Price crosses below</option>
                <option value="pct_change_up">% Change ≥ (gain)</option>
                <option value="pct_change_down">% Change ≤ (loss)</option>
                <option value="volume_spike">Volume × Avg Volume ≥</option>
              </select>
            </label>

            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-mono text-terminal-ghost uppercase">Threshold value</span>
              <input
                type="number"
                step="0.01"
                value={draft.value}
                onChange={(e) => setDraft({ ...draft, value: Number(e.target.value) })}
                className="bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-xs font-mono text-terminal-text focus:border-terminal-blue focus:outline-none"
              />
            </label>

            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-mono text-terminal-ghost uppercase">Note (optional)</span>
              <input
                value={draft.message ?? ''}
                onChange={(e) => setDraft({ ...draft, message: e.target.value })}
                placeholder="e.g. AAPL breakout above resistance"
                className="bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-xs font-mono text-terminal-text focus:border-terminal-blue focus:outline-none"
              />
            </label>

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                onClick={() => setCreating(false)}
                className="text-xs font-mono px-3 py-1.5 rounded border border-terminal-border text-terminal-dim hover:text-terminal-text"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                className="text-xs font-mono px-4 py-1.5 rounded bg-terminal-green/20 border border-terminal-green/40 text-terminal-green hover:bg-terminal-green/30"
              >
                Create Alert
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
