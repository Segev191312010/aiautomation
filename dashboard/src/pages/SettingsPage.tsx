import React, { useEffect, useState } from 'react'
import { useToast } from '@/components/ui/ToastProvider'
import { useSettingsStore, useBotStore } from '@/store'
import { fetchSettings, updateSettings } from '@/services/api'

// ── Inline SVG icons (no extra dependency) ───────────────────────────────────

function IconSettings({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  )
}

function IconGlobe({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="2" y1="12" x2="22" y2="12" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  )
}

function IconCpu({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <rect x="9" y="9" width="6" height="6" />
      <line x1="9" y1="1" x2="9" y2="4" /><line x1="15" y1="1" x2="15" y2="4" />
      <line x1="9" y1="20" x2="9" y2="23" /><line x1="15" y1="20" x2="15" y2="23" />
      <line x1="20" y1="9" x2="23" y2="9" /><line x1="20" y1="14" x2="23" y2="14" />
      <line x1="1" y1="9" x2="4" y2="9" /><line x1="1" y1="14" x2="4" y2="14" />
    </svg>
  )
}

function IconMonitor({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="3" width="20" height="14" rx="2" />
      <line x1="8" y1="21" x2="16" y2="21" />
      <line x1="12" y1="17" x2="12" y2="21" />
    </svg>
  )
}

function IconInfo({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="16" x2="12" y2="12" />
      <line x1="12" y1="8" x2="12.01" y2="8" />
    </svg>
  )
}

function IconSave({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
      <polyline points="17 21 17 13 7 13 7 21" />
      <polyline points="7 3 7 8 15 8" />
    </svg>
  )
}

function IconRefresh({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <polyline points="1 4 1 10 7 10" />
      <path d="M3.51 15a9 9 0 1 0 .49-3.63" />
    </svg>
  )
}

// ── Label component ───────────────────────────────────────────────────────────

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <label className="text-xs font-sans font-medium text-gray-500 tracking-wide uppercase mb-2 block">
      {children}
    </label>
  )
}

// ── Section card wrapper ──────────────────────────────────────────────────────

interface SectionCardProps {
  icon: React.ReactNode
  title: string
  delay?: number
  children: React.ReactNode
}

function SectionCard({ icon, title, delay = 0, children }: SectionCardProps) {
  return (
    <section
      className="card rounded-2xl shadow-card overflow-hidden animate-fade-in-up"
      style={{ animationDelay: `${delay}ms`, animationFillMode: 'both' }}
    >
      {/* Section header */}
      <div className="flex items-center gap-2.5 px-5 py-4 border-b border-gray-200">
        <span className="flex items-center justify-center w-7 h-7 rounded-lg bg-indigo-50 text-indigo-600">
          {icon}
        </span>
        <h2 className="text-xs font-sans font-semibold text-gray-500 tracking-widest uppercase">
          {title}
        </h2>
      </div>
      {/* Section body */}
      <div className="p-5">
        {children}
      </div>
    </section>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

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

  const handleReset = () => {
    if (!settings) return
    setDefaultSymbol(settings.default_symbol)
    setDefaultBarSize(settings.default_bar_size)
    setBotInterval(settings.bot_interval)
    setWatchlistInput(settings.watchlist.join(', '))
    toast.success('Reset to last saved values')
  }

  const BAR_SIZES = ['1m', '5m', '15m', '30m', '1h', '1D', '1W', '1M']

  // Shared input class
  const inputCls =
    'w-full text-sm font-mono bg-white border border-gray-200 rounded-xl px-4 py-2.5 ' +
    'text-gray-800 placeholder-gray-400 transition-colors duration-150 ' +
    'focus:border-indigo-100 focus:ring-1 focus:ring-indigo-300 focus:outline-none uppercase'

  return (
    <div className="flex flex-col gap-6 max-w-2xl pb-8">

      {/* ── Page header ──────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 animate-fade-in-up" style={{ animationDelay: '0ms', animationFillMode: 'both' }}>
        <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-600/20 to-purple-600/10 border border-indigo-100 shadow-glow-blue">
          <IconSettings className="w-5 h-5 text-indigo-600" />
        </div>
        <div>
          <h1 className="text-base font-sans font-bold text-gray-800 tracking-tight">Settings</h1>
          <p className="text-[11px] font-sans text-gray-400 mt-0.5">Configure platform preferences and defaults</p>
        </div>
      </div>

      {/* ── General ──────────────────────────────────────────────────────── */}
      <SectionCard icon={<IconGlobe className="w-3.5 h-3.5" />} title="General" delay={60}>
        <div className="flex flex-col gap-5">

          {/* Default Symbol */}
          <div>
            <FieldLabel>Default Symbol</FieldLabel>
            <input
              value={defaultSymbol}
              onChange={(e) => setDefaultSymbol(e.target.value)}
              placeholder="AAPL"
              className={`${inputCls} max-w-[10rem]`}
            />
          </div>

          {/* Divider */}
          <div className="h-px bg-gray-100/60" />

          {/* Default Bar Size */}
          <div>
            <FieldLabel>Default Bar Size</FieldLabel>
            <div className="flex gap-1.5 flex-wrap">
              {BAR_SIZES.map((bs) => (
                <button
                  key={bs}
                  onClick={() => setDefaultBarSize(bs)}
                  className={`text-xs font-mono px-3 py-1.5 rounded-xl border transition-all duration-150 ${
                    defaultBarSize === bs
                      ? 'border-indigo-100 text-indigo-600 bg-indigo-50 shadow-[0_0_12px_rgba(99,102,241,0.15)]'
                      : 'border-gray-200 text-gray-400 bg-white hover:text-gray-500 hover:border-white/[0.12] hover:bg-gray-50'
                  }`}
                >
                  {bs}
                </button>
              ))}
            </div>
          </div>

          {/* Divider */}
          <div className="h-px bg-gray-100/60" />

          {/* Default Watchlist */}
          <div>
            <FieldLabel>Default Watchlist</FieldLabel>
            <input
              value={watchlistInput}
              onChange={(e) => setWatchlistInput(e.target.value)}
              placeholder="SPY, QQQ, AAPL, ..."
              className={inputCls}
            />
            <p className="text-[10px] font-sans text-gray-400 mt-1.5 tracking-wide">
              Comma-separated ticker symbols
            </p>
          </div>

        </div>
      </SectionCard>

      {/* ── Bot ──────────────────────────────────────────────────────────── */}
      <SectionCard icon={<IconCpu className="w-3.5 h-3.5" />} title="Bot" delay={120}>
        <div>
          <FieldLabel>Evaluation Interval</FieldLabel>
          <div className="flex items-center gap-3">
            <input
              type="number"
              min={5}
              max={3600}
              value={botInterval}
              onChange={(e) => setBotInterval(Number(e.target.value))}
              className={`${inputCls} max-w-[8rem]`}
            />
            <span className="text-xs font-sans text-gray-400">seconds</span>
          </div>
          <p className="text-[10px] font-sans text-gray-400 mt-1.5 tracking-wide">
            How frequently the rule engine evaluates open conditions (5 – 3600 s)
          </p>
        </div>
      </SectionCard>

      {/* ── Display ──────────────────────────────────────────────────────── */}
      <SectionCard icon={<IconMonitor className="w-3.5 h-3.5" />} title="Display" delay={180}>
        <div>
          <FieldLabel>Theme</FieldLabel>
          <div className="flex items-center gap-3">
            <span className="inline-flex items-center gap-1.5 text-xs font-sans px-3 py-1.5 rounded-xl border border-indigo-100 text-indigo-600 bg-indigo-50">
              <span className="w-1.5 h-1.5 rounded-full bg-indigo-600 inline-block" />
              Dark
            </span>
            <span className="text-[10px] font-sans text-gray-400">Light theme coming in Stage 8</span>
          </div>
        </div>
      </SectionCard>

      {/* ── About ────────────────────────────────────────────────────────── */}
      <SectionCard icon={<IconInfo className="w-3.5 h-3.5" />} title="About" delay={240}>
        <div className="grid grid-cols-[auto_1fr] gap-x-8 gap-y-3">

          <span className="text-xs font-sans text-gray-400 uppercase tracking-wide">Version</span>
          <span className="text-xs font-mono text-gray-800">2.0.0</span>

          <span className="text-xs font-sans text-gray-400 uppercase tracking-wide">Mode</span>
          <span className="text-xs font-sans text-gray-800">
            {status?.sim_mode ? 'Simulation' : status?.ibkr_connected ? 'IBKR Live' : 'Disconnected'}
          </span>

          <span className="text-xs font-sans text-gray-400 uppercase tracking-wide">IBKR</span>
          <span className={`text-xs font-sans flex items-center gap-1.5 ${status?.ibkr_connected ? 'text-emerald-400' : 'text-red-400'}`}>
            <span className={`w-1.5 h-1.5 rounded-full inline-block ${status?.ibkr_connected ? 'bg-emerald-400' : 'bg-red-400'}`} />
            {status?.ibkr_connected ? 'Connected' : 'Disconnected'}
          </span>

        </div>
      </SectionCard>

      {/* ── Action row ───────────────────────────────────────────────────── */}
      <div
        className="flex items-center gap-3 animate-fade-in-up"
        style={{ animationDelay: '300ms', animationFillMode: 'both' }}
      >
        {/* Save button */}
        <button
          onClick={handleSave}
          disabled={saving}
          className={[
            'inline-flex items-center gap-2 text-sm font-sans font-semibold px-5 py-2.5 rounded-xl',
            'bg-gradient-to-r from-indigo-600 to-purple-600 text-white',
            'shadow-[0_0_20px_rgba(99,102,241,0.3)] hover:shadow-[0_0_28px_rgba(99,102,241,0.45)]',
            'hover:opacity-95 active:scale-[0.98] transition-all duration-150',
            'disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none',
          ].join(' ')}
        >
          {saving ? (
            <>
              <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              Saving…
            </>
          ) : (
            <>
              <IconSave className="w-3.5 h-3.5" />
              Save Settings
            </>
          )}
        </button>

        {/* Reset button */}
        <button
          onClick={handleReset}
          disabled={saving || !settings}
          className={[
            'inline-flex items-center gap-2 text-sm font-sans font-medium px-4 py-2.5 rounded-xl',
            'border border-gray-200 text-gray-500 bg-transparent',
            'hover:border-white/[0.14] hover:text-gray-800 hover:bg-gray-50',
            'active:scale-[0.98] transition-all duration-150',
            'disabled:opacity-30 disabled:cursor-not-allowed',
          ].join(' ')}
        >
          <IconRefresh className="w-3.5 h-3.5" />
          Reset
        </button>
      </div>

    </div>
  )
}
