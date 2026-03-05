import React, { useEffect, useState } from 'react'
import { useToast } from '@/components/ui/ToastProvider'
import { useSettingsStore, useBotStore } from '@/store'
import { fetchSettings, updateSettings } from '@/services/api'
import type { UserSettings } from '@/types'

export default function SettingsPage() {
  const toast = useToast()
  const { settings, setSettings } = useSettingsStore()
  const status = useBotStore((s) => s.status)

  const [defaultSymbol,   setDefaultSymbol]   = useState('')
  const [defaultBarSize,  setDefaultBarSize]  = useState('')
  const [botInterval,     setBotInterval]     = useState(60)
  const [watchlistInput,  setWatchlistInput]  = useState('')
  const [saving,          setSaving]          = useState(false)

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
        default_symbol:   defaultSymbol.toUpperCase(),
        default_bar_size: defaultBarSize,
        bot_interval:     botInterval,
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
      <h1 className="text-lg font-sans font-bold text-terminal-text">Settings</h1>

      {/* ── General ───────────────────────────────────────────── */}
      <section className="glass rounded-2xl shadow-glass p-5">
        <h2 className="text-xs font-sans font-medium text-terminal-dim tracking-wide mb-4">
          General
        </h2>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-sans font-medium text-terminal-dim">Default Symbol</label>
            <input
              value={defaultSymbol}
              onChange={(e) => setDefaultSymbol(e.target.value)}
              className="w-40 text-sm font-mono bg-terminal-input border border-white/[0.06] rounded-xl px-3 py-1.5 text-terminal-text focus:border-indigo-500/50 focus:outline-none uppercase"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-sans font-medium text-terminal-dim">Default Bar Size</label>
            <div className="flex gap-1.5 flex-wrap">
              {BAR_SIZES.map((bs) => (
                <button
                  key={bs}
                  onClick={() => setDefaultBarSize(bs)}
                  className={`text-xs font-mono px-2.5 py-1 rounded-xl border transition-colors ${
                    defaultBarSize === bs
                      ? 'border-indigo-500/40 text-indigo-400 bg-indigo-500/15'
                      : 'border-white/[0.06] text-terminal-ghost hover:text-terminal-dim hover:border-white/[0.12]'
                  }`}
                >
                  {bs}
                </button>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-sans font-medium text-terminal-dim">Default Watchlist</label>
            <input
              value={watchlistInput}
              onChange={(e) => setWatchlistInput(e.target.value)}
              placeholder="SPY, QQQ, AAPL, ..."
              className="text-sm font-mono bg-terminal-input border border-white/[0.06] rounded-xl px-3 py-1.5 text-terminal-text focus:border-indigo-500/50 focus:outline-none uppercase"
            />
            <span className="text-[10px] font-sans text-terminal-ghost">Comma-separated symbols</span>
          </div>
        </div>
      </section>

      {/* ── Bot ────────────────────────────────────────────────── */}
      <section className="glass rounded-2xl shadow-glass p-5">
        <h2 className="text-xs font-sans font-medium text-terminal-dim tracking-wide mb-4">
          Bot
        </h2>
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-sans font-medium text-terminal-dim">Evaluation Interval (seconds)</label>
          <input
            type="number"
            min={5}
            max={3600}
            value={botInterval}
            onChange={(e) => setBotInterval(Number(e.target.value))}
            className="w-32 text-sm font-mono bg-terminal-input border border-white/[0.06] rounded-xl px-3 py-1.5 text-terminal-text focus:border-indigo-500/50 focus:outline-none"
          />
        </div>
      </section>

      {/* ── Display ────────────────────────────────────────────── */}
      <section className="glass rounded-2xl shadow-glass p-5">
        <h2 className="text-xs font-sans font-medium text-terminal-dim tracking-wide mb-4">
          Display
        </h2>
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-sans font-medium text-terminal-dim">Theme</label>
          <div className="flex items-center gap-3">
            <span className="text-xs font-sans px-3 py-1 rounded-xl border border-indigo-500/40 text-indigo-400 bg-indigo-500/15">
              Dark
            </span>
            <span className="text-[10px] font-sans text-terminal-ghost">Light theme coming in Stage 8</span>
          </div>
        </div>
      </section>

      {/* ── About ──────────────────────────────────────────────── */}
      <section className="glass rounded-2xl shadow-glass p-5">
        <h2 className="text-xs font-sans font-medium text-terminal-dim tracking-wide mb-4">
          About
        </h2>
        <div className="grid grid-cols-2 gap-y-2.5 text-xs">
          <span className="font-sans text-terminal-dim">Version</span>
          <span className="font-mono text-terminal-text">2.0.0</span>
          <span className="font-sans text-terminal-dim">Mode</span>
          <span className="font-sans text-terminal-text">
            {status?.sim_mode ? 'Simulation' : status?.ibkr_connected ? 'IBKR Live' : 'Disconnected'}
          </span>
          <span className="font-sans text-terminal-dim">IBKR</span>
          <span className={`font-sans ${status?.ibkr_connected ? 'text-emerald-400' : 'text-red-400'}`}>
            {status?.ibkr_connected ? 'Connected' : 'Disconnected'}
          </span>
        </div>
      </section>

      {/* ── Save ───────────────────────────────────────────────── */}
      <div>
        <button
          onClick={handleSave}
          disabled={saving}
          className="text-sm font-sans font-medium px-6 py-2 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-500 text-white hover:opacity-90 disabled:opacity-40 transition-opacity"
        >
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  )
}
