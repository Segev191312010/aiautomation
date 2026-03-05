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
      <div className="h-8 w-48 bg-terminal-elevated rounded-xl" />
      <div className="h-10 w-full bg-terminal-elevated rounded-xl" />
      <div className="h-10 w-full bg-terminal-elevated rounded-xl" />
      <div className="h-10 w-full bg-terminal-elevated rounded-xl" />
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

type ActiveTab = 'active' | 'history'

export default function AlertsPage() {
  const loading     = useAlertStore((s) => s.loading)
  const loadAlerts  = useAlertStore((s) => s.loadAlerts)
  const loadHistory = useAlertStore((s) => s.loadHistory)

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

  return (
    <div className="flex flex-col h-full p-4">
      {/* Page header */}
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-base font-sans font-semibold text-terminal-text tracking-wide">
          Alerts
        </h1>
        {activeTab === 'active' && (
          <button
            onClick={handleCreateNew}
            className="bg-indigo-500/15 text-indigo-400 hover:bg-indigo-500/25 px-3 py-1.5 rounded-xl text-sm font-sans font-medium transition-colors"
          >
            + Create Alert
          </button>
        )}
      </div>

      {/* Tab bar */}
      <div className="flex border-b border-white/[0.06] mb-4">
        <button
          onClick={() => setActiveTab('active')}
          className={`text-sm font-sans font-medium px-4 py-2 border-b-2 transition-colors ${
            activeTab === 'active'
              ? 'border-indigo-500 text-indigo-400'
              : 'border-transparent text-terminal-ghost hover:text-terminal-dim'
          }`}
        >
          Active Alerts
        </button>
        <button
          onClick={() => setActiveTab('history')}
          className={`text-sm font-sans font-medium px-4 py-2 border-b-2 transition-colors ${
            activeTab === 'history'
              ? 'border-indigo-500 text-indigo-400'
              : 'border-transparent text-terminal-ghost hover:text-terminal-dim'
          }`}
        >
          History
        </button>
      </div>

      {/* Tab content */}
      <div className="mt-0 flex-1 min-h-0 overflow-y-auto">
        {loading ? (
          <AlertsPageSkeleton />
        ) : activeTab === 'active' ? (
          <AlertList onEdit={handleEdit} />
        ) : (
          <AlertHistoryTable />
        )}
      </div>

      {/* Create / Edit modal */}
      {showForm && (
        <AlertForm
          onClose={handleCloseForm}
          editAlert={editAlert ?? undefined}
        />
      )}
    </div>
  )
}
