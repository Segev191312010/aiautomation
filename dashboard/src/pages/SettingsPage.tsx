import React, { useEffect, useState } from 'react'
import { useToast } from '@/components/ui/ToastProvider'
import { useSettingsStore, useBotStore } from '@/store'
import { fetchSettings, updateSettings } from '@/services/api'
import type { UserSettings } from '@/types'

export default function SettingsPage() {
  const toast = useToast()
  const { settings, setSettings } = useSettingsStore()
  const status = useBotStore((s) => s.status)

  const [defaultSymbol, setDefaultSymbol] = useState('')
  const [defaultBarSize, setDefaultBarSize] = useState('')
  const [botInterval, setBotInterval] = useState(60)
  const [watchlistInput, setWatchlistInput] = useState('')
  const [saving, setSaving] = useState(false)

  // Load settings on mount
  useEffect(() => {
    const load = async () => {
      try {
        const s = await fetchSettings()
        setSettings(s)
        setDefaultSymbol(s.default_symbol)
        setDefaultBarSize(s.default_bar_size)
        setBotInterval(s.bot_interval)
        setWatchlistInput(s.watchlist.join(', '))
      } catch {
        toast.error('Failed to load settings')
      }
    }
    load()
  }, [setSettings, toast])

  const handleSave = async () => {
    setSaving(true)
    try {
      const watchlist = watchlistInput
        .split(',')
        .map((s) => s.trim().toUpperCase())
        .filter(Boolean)

      const updated = await updateSettings({
        default_symbol: defaultSymbol.toUpperCase(),
        default_bar_size: defaultBarSize,
        bot_interval: botInterval,
        watchlist,
      })
      setSettings(updated)
      toast.success('Settings saved')
    } catch {
      toast.error('Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  const BAR_SIZES = ['1m', '5m', '15m', '30m', '1h', '1D', '1W', '1M']

  return (
    <div className="flex flex-col gap-6 max-w-2xl">
      <h1 className="text-lg font-mono font-bold text-terminal-text">Settings</h1>

      {/* ── General ───────────────────────────────────────────── */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-5">
        <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest mb-4">
          General
        </h2>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-mono text-terminal-dim">Default Symbol</label>
            <input
              value={defaultSymbol}
              onChange={(e) => setDefaultSymbol(e.target.value)}
              className="w-40 text-sm font-mono bg-terminal-input border border-terminal-border rounded px-3 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none uppercase"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-mono text-terminal-dim">Default Bar Size</label>
            <div className="flex gap-1">
              {BAR_SIZES.map((bs) => (
                <button
                  key={bs}
                  onClick={() => setDefaultBarSize(bs)}
                  className={`text-[11px] font-mono px-2 py-1 rounded border transition-colors ${
                    defaultBarSize === bs
                      ? 'border-terminal-blue/50 text-terminal-blue bg-terminal-blue/10'
                      : 'border-terminal-border text-terminal-ghost hover:text-terminal-dim'
                  }`}
                >
                  {bs}
                </button>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-mono text-terminal-dim">Default Watchlist</label>
            <input
              value={watchlistInput}
              onChange={(e) => setWatchlistInput(e.target.value)}
              placeholder="SPY, QQQ, AAPL, ..."
              className="text-sm font-mono bg-terminal-input border border-terminal-border rounded px-3 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none uppercase"
            />
            <span className="text-[10px] font-mono text-terminal-ghost">Comma-separated symbols</span>
          </div>
        </div>
      </section>

      {/* ── Bot ────────────────────────────────────────────────── */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-5">
        <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest mb-4">
          Bot
        </h2>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-mono text-terminal-dim">Evaluation Interval (seconds)</label>
          <input
            type="number"
            min={5}
            max={3600}
            value={botInterval}
            onChange={(e) => setBotInterval(Number(e.target.value))}
            className="w-32 text-sm font-mono bg-terminal-input border border-terminal-border rounded px-3 py-1.5 text-terminal-text focus:border-terminal-blue focus:outline-none"
          />
        </div>
      </section>

      {/* ── Display ────────────────────────────────────────────── */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-5">
        <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest mb-4">
          Display
        </h2>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-mono text-terminal-dim">Theme</label>
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono px-3 py-1 rounded border border-terminal-blue/50 text-terminal-blue bg-terminal-blue/10">
              Dark
            </span>
            <span className="text-[10px] font-mono text-terminal-ghost">Light theme coming in Stage 8</span>
          </div>
        </div>
      </section>

      {/* ── About ──────────────────────────────────────────────── */}
      <section className="bg-terminal-surface border border-terminal-border rounded-lg p-5">
        <h2 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest mb-4">
          About
        </h2>
        <div className="grid grid-cols-2 gap-y-2 text-xs font-mono">
          <span className="text-terminal-dim">Version</span>
          <span className="text-terminal-text">2.0.0</span>
          <span className="text-terminal-dim">Mode</span>
          <span className="text-terminal-text">
            {status?.sim_mode ? 'Simulation' : status?.ibkr_connected ? 'IBKR Live' : 'Mock'}
          </span>
          <span className="text-terminal-dim">IBKR</span>
          <span className={status?.ibkr_connected ? 'text-terminal-green' : 'text-terminal-red'}>
            {status?.ibkr_connected ? 'Connected' : 'Disconnected'}
          </span>
          <span className="text-terminal-dim">Mock Mode</span>
          <span className="text-terminal-text">{status?.mock_mode ? 'Enabled' : 'Disabled'}</span>
        </div>
      </section>

      {/* ── Save ───────────────────────────────────────────────── */}
      <div>
        <button
          onClick={handleSave}
          disabled={saving}
          className="text-sm font-mono px-6 py-2 rounded bg-terminal-blue/20 border border-terminal-blue/40 text-terminal-blue hover:bg-terminal-blue/30 disabled:opacity-40 transition-colors"
        >
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  )
}
