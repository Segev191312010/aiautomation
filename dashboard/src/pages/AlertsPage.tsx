/**
 * AlertsPage — main alerts management page.
 * Tabs: Active Alerts | History
 */
import { useEffect, useState } from 'react'
import { useAlertStore } from '@/store'
import type { Alert } from '@/types'
import AlertList from '@/components/alerts/AlertList'
import AlertHistoryTable from '@/components/alerts/AlertHistoryTable'
import AlertForm from '@/components/alerts/AlertForm'

// ── Skeleton ──────────────────────────────────────────────────────────────────

function AlertsPageSkeleton() {
  return (
    <div className="flex flex-col gap-3 animate-pulse">
      <div className="h-10 w-full bg-zinc-900 rounded-xl" />
      <div className="h-10 w-full bg-zinc-900 rounded-xl" />
      <div className="h-10 w-full bg-zinc-900 rounded-xl" />
    </div>
  )
}

// ── Status pill ───────────────────────────────────────────────────────────────

function StatusPill({ count, label, color }: { count: number; label: string; color: 'green' | 'amber' | 'ghost' }) {
  const colorMap = {
    green: 'bg-emerald-600/10 text-emerald-400 border-emerald-300/20',
    amber: 'bg-amber-600/10 text-amber-600 border-amber-300/20',
    ghost: 'bg-zinc-800/60 text-zinc-400 border-zinc-800',
  }
  return (
    <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-sans font-medium ${colorMap[color]}`}>
      <span className="tabular-nums font-semibold">{count}</span>
      <span>{label}</span>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

type ActiveTab = 'active' | 'history'

export default function AlertsPage() {
  const loading     = useAlertStore((s) => s.loading)
  const loadAlerts  = useAlertStore((s) => s.loadAlerts)
  const loadHistory = useAlertStore((s) => s.loadHistory)
  const alerts      = useAlertStore((s) => s.alerts)
  const history     = useAlertStore((s) => s.history)

  const [activeTab, setActiveTab] = useState<ActiveTab>('active')
  const [showForm, setShowForm]   = useState(false)
  const [editAlert, setEditAlert] = useState<Alert | null>(null)

  useEffect(() => {
    void loadAlerts()
    void loadHistory()
  }, [loadAlerts, loadHistory])

  function handleEdit(alert: Alert) {
    setEditAlert(alert)
    setShowForm(true)
  }

  function handleCloseForm() {
    setShowForm(false)
    setEditAlert(null)
  }

  function handleCreateNew() {
    setEditAlert(null)
    setShowForm(true)
  }

  // ── Derived summary stats ───────────────────────────────────────────────

  const activeCount  = alerts.filter((a) => a.enabled).length
  const disabledCount = alerts.filter((a) => !a.enabled).length

  // Triggered today = history rows fired within the last 24h
  const oneDayAgo = Date.now() - 24 * 60 * 60 * 1000
  const todayCount = history.filter((h) => new Date(h.fired_at).getTime() >= oneDayAgo).length

  return (
    <div className="flex flex-col h-full p-5">

      {/* ── Page header ─────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between mb-5 gap-4">
        <div>
          <div className="flex items-center gap-2.5 mb-1">
            {/* Bell icon */}
            <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-indigo-50 shrink-0">
              <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4 text-indigo-600">
                <path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.89 2 2 2zm6-6v-5c0-3.07-1.64-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.63 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z" />
              </svg>
            </div>
            <h1 className="text-base font-sans font-semibold text-zinc-100 tracking-wide">
              Alerts
            </h1>
          </div>
          <p className="text-xs font-sans text-zinc-500 ml-9">
            Price and indicator triggers with browser notifications
          </p>
        </div>

        {activeTab === 'active' && (
          <button
            onClick={handleCreateNew}
            className={[
              'flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-sm font-sans font-semibold shrink-0',
              'bg-indigo-100 text-indigo-600 border border-indigo-100',
              'hover:bg-indigo-100 hover:border-indigo-600/50 hover:shadow-glow-blue',
              'transition-all duration-150',
            ].join(' ')}
          >
            <svg viewBox="0 0 24 24" fill="currentColor" className="w-3.5 h-3.5">
              <path d="M19 13H13V19H11V13H5V11H11V5H13V11H19V13Z" />
            </svg>
            Create Alert
          </button>
        )}
      </div>

      {/* ── Status summary bar ──────────────────────────────────────────── */}
      {!loading && (
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <StatusPill count={activeCount}   label={activeCount === 1 ? 'active' : 'active alerts'} color="green" />
          <StatusPill count={todayCount}    label={todayCount === 1 ? 'triggered today' : 'triggered today'} color="amber" />
          {disabledCount > 0 && (
            <StatusPill count={disabledCount} label="disabled" color="ghost" />
          )}
        </div>
      )}

      {/* ── Tab bar ─────────────────────────────────────────────────────── */}
      <div className="flex border-b border-zinc-800 mb-4">
        {(['active', 'history'] as ActiveTab[]).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={[
              'text-sm font-sans font-medium px-4 py-2 border-b-2 transition-colors',
              activeTab === tab
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-zinc-500 hover:text-zinc-400',
            ].join(' ')}
          >
            {tab === 'active' ? 'Active Alerts' : 'History'}
          </button>
        ))}
      </div>

      {/* ── Tab content ─────────────────────────────────────────────────── */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {loading ? (
          <AlertsPageSkeleton />
        ) : activeTab === 'active' ? (
          <AlertList onEdit={handleEdit} />
        ) : (
          <AlertHistoryTable />
        )}
      </div>

      {/* ── Create / Edit modal ─────────────────────────────────────────── */}
      {showForm && (
        <AlertForm
          onClose={handleCloseForm}
          editAlert={editAlert ?? undefined}
        />
      )}
    </div>
  )
}
