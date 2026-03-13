/**
 * SettingsPage — Application settings and connection configuration.
 */
import React, { useEffect, useState } from 'react'
import clsx from 'clsx'
import { useBotStore, useMarketStore, useUIStore } from '@/store'
import { connectIBKR, disconnectIBKR, fetchStatus } from '@/services/api'

function Toggle({ value, onChange, label }: { value: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      onClick={() => onChange(!value)}
      className="flex items-center gap-3 group"
    >
      <div
        className={clsx(
          'w-9 h-5 rounded-full relative transition-colors',
          value ? 'bg-terminal-green/30' : 'bg-terminal-muted',
        )}
      >
        <span
          className={clsx(
            'absolute top-0.5 w-4 h-4 rounded-full transition-all',
            value ? 'left-[18px] bg-terminal-green' : 'left-0.5 bg-terminal-dim',
          )}
        />
      </div>
      <span className="text-xs font-mono text-terminal-dim group-hover:text-terminal-text transition-colors">{label}</span>
    </button>
  )
}

function SettingsSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="bg-terminal-surface border border-terminal-border rounded-lg p-5 space-y-4">
      <h3 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest">{title}</h3>
      {children}
    </section>
  )
}

export default function SettingsPage() {
  const { status, ibkrConnected, setIBKR, setStatus } = useBotStore()
  const { sidebarCollapsed, setSidebarCollapsed } = useUIStore()
  const { watchlists, addWatchlist, removeWatchlist, addToWatchlist, removeFromWatchlist } = useMarketStore()
  const [newListName, setNewListName] = useState('')
  const [newSymbol, setNewSymbol] = useState('')
  const [addToList, setAddToList] = useState(watchlists[0]?.id ?? '')
  const [statusMsg, setStatusMsg] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetchStatus().then(setStatus).catch(() => {})
  }, [setStatus])

  const handleConnect = async () => {
    setBusy(true)
    setStatusMsg('')
    try {
      const r = await connectIBKR()
      setIBKR(r.connected)
      setStatusMsg(r.connected ? 'Connected to IBKR' : 'Connection failed')
    } catch (e: unknown) {
      setStatusMsg(e instanceof Error ? e.message : 'Connection error')
    } finally {
      setBusy(false)
    }
  }

  const handleDisconnect = async () => {
    setBusy(true)
    try {
      await disconnectIBKR()
      setIBKR(false)
      setStatusMsg('Disconnected')
    } catch { /* ignore */ } finally {
      setBusy(false)
    }
  }

  const handleAddWatchlist = (e: React.FormEvent) => {
    e.preventDefault()
    if (!newListName.trim()) return
    addWatchlist(newListName.trim())
    setNewListName('')
  }

  const handleAddSymbol = (e: React.FormEvent) => {
    e.preventDefault()
    if (!newSymbol.trim() || !addToList) return
    addToWatchlist(addToList, newSymbol.trim().toUpperCase())
    setNewSymbol('')
  }

  return (
    <div className="flex flex-col gap-5 max-w-3xl">
      {/* Connection */}
      <SettingsSection title="IBKR Connection">
        <div className="flex items-center gap-3">
          <span
            className={clsx(
              'w-2.5 h-2.5 rounded-full',
              ibkrConnected ? 'bg-terminal-green animate-pulse' : 'bg-terminal-red',
            )}
          />
          <span className="text-xs font-mono text-terminal-text">
            {ibkrConnected ? 'Connected' : 'Disconnected'}
          </span>
        </div>
        {status && (
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-[11px] font-mono">
            <span className="text-terminal-ghost">Host</span>
            <span className="text-terminal-dim">{status.ibkr_host}:{status.ibkr_port}</span>
            <span className="text-terminal-ghost">Paper Trading</span>
            <span className="text-terminal-dim">{status.is_paper ? 'Yes' : 'No'}</span>
            <span className="text-terminal-ghost">Bot Interval</span>
            <span className="text-terminal-dim">{status.bot_interval_seconds}s</span>
            <span className="text-terminal-ghost">Mock Mode</span>
            <span className="text-terminal-dim">{status.mock_mode ? 'On' : 'Off'}</span>
          </div>
        )}
        <div className="flex gap-2">
          <button
            onClick={handleConnect}
            disabled={busy}
            className="text-xs font-mono px-4 py-1.5 rounded bg-terminal-green/20 border border-terminal-green/40 text-terminal-green hover:bg-terminal-green/30 disabled:opacity-40 transition-colors"
          >
            {busy ? 'Connecting...' : 'Connect'}
          </button>
          <button
            onClick={handleDisconnect}
            disabled={busy || !ibkrConnected}
            className="text-xs font-mono px-4 py-1.5 rounded border border-terminal-red/30 text-terminal-red hover:bg-terminal-red/10 disabled:opacity-40 transition-colors"
          >
            Disconnect
          </button>
        </div>
        {statusMsg && <p className="text-[11px] font-mono text-terminal-dim">{statusMsg}</p>}
      </SettingsSection>

      {/* UI Preferences */}
      <SettingsSection title="UI Preferences">
        <Toggle
          value={sidebarCollapsed}
          onChange={setSidebarCollapsed}
          label="Collapse sidebar by default"
        />
      </SettingsSection>

      {/* Watchlist Management */}
      <SettingsSection title="Watchlist Management">
        {/* Existing watchlists */}
        <div className="space-y-3">
          {watchlists.map((wl) => (
            <div key={wl.id} className="bg-terminal-bg/50 rounded p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-mono font-semibold text-terminal-text">{wl.name}</span>
                {wl.id !== 'default' && (
                  <button
                    onClick={() => removeWatchlist(wl.id)}
                    className="text-[10px] font-mono px-2 py-0.5 rounded border border-terminal-red/30 text-terminal-red hover:bg-terminal-red/10 transition-colors"
                  >
                    Delete
                  </button>
                )}
              </div>
              <div className="flex flex-wrap gap-1">
                {wl.symbols.map((sym) => (
                  <span
                    key={sym}
                    className="text-[10px] font-mono px-2 py-0.5 rounded bg-terminal-muted text-terminal-dim flex items-center gap-1 group"
                  >
                    {sym}
                    <button
                      onClick={() => removeFromWatchlist(wl.id, sym)}
                      className="text-terminal-ghost hover:text-terminal-red transition-colors opacity-0 group-hover:opacity-100"
                    >
                      x
                    </button>
                  </span>
                ))}
                {wl.symbols.length === 0 && (
                  <span className="text-[10px] font-mono text-terminal-ghost">Empty</span>
                )}
              </div>
            </div>
          ))}
        </div>

        {/* Add watchlist */}
        <form onSubmit={handleAddWatchlist} className="flex items-end gap-2">
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-terminal-ghost uppercase">New Watchlist</label>
            <input
              value={newListName}
              onChange={(e) => setNewListName(e.target.value)}
              placeholder="My Stocks"
              className="w-48 text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
            />
          </div>
          <button
            type="submit"
            className="text-xs font-mono px-3 py-1.5 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 transition-colors"
          >
            Create
          </button>
        </form>

        {/* Add symbol to watchlist */}
        <form onSubmit={handleAddSymbol} className="flex items-end gap-2">
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-terminal-ghost uppercase">Add Symbol</label>
            <input
              value={newSymbol}
              onChange={(e) => setNewSymbol(e.target.value)}
              placeholder="TSLA"
              className="w-28 text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none uppercase"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-terminal-ghost uppercase">To List</label>
            <select
              value={addToList}
              onChange={(e) => setAddToList(e.target.value)}
              className="text-xs font-mono bg-terminal-input border border-terminal-border rounded px-2 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
            >
              {watchlists.map((wl) => (
                <option key={wl.id} value={wl.id}>{wl.name}</option>
              ))}
            </select>
          </div>
          <button
            type="submit"
            className="text-xs font-mono px-3 py-1.5 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 transition-colors"
          >
            Add
          </button>
        </form>
      </SettingsSection>

      {/* System Info */}
      {status && (
        <SettingsSection title="System Info">
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-[11px] font-mono">
            <span className="text-terminal-ghost">Bot Running</span>
            <span className={status.bot_running ? 'text-terminal-green' : 'text-terminal-dim'}>
              {status.bot_running ? 'Yes' : 'No'}
            </span>
            <span className="text-terminal-ghost">Sim Mode</span>
            <span className="text-terminal-dim">{status.sim_mode ? 'On' : 'Off'}</span>
            <span className="text-terminal-ghost">Last Run</span>
            <span className="text-terminal-dim">{status.last_run ? new Date(status.last_run).toLocaleString() : '—'}</span>
            <span className="text-terminal-ghost">Next Run</span>
            <span className="text-terminal-dim">{status.next_run ? new Date(status.next_run).toLocaleString() : '—'}</span>
          </div>
        </SettingsSection>
      )}
    </div>
  )
}
