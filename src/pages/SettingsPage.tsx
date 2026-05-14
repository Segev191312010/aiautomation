/**
 * SettingsPage — application configuration.
 *
 * Lets the user configure:
 *  • IBKR connection details (host/port/client id, paper vs live)
 *  • Data provider preference (mock / yahoo / IBKR)
 *  • Chart engine (lightweight-charts vs TradingView embed)
 *  • Bot tick interval
 *  • Screener universe + alerts toggle
 *
 * In mock mode all changes are persisted to localStorage. With a real backend
 * the settings would round-trip via an API; the mock backend currently
 * provides the persistence layer.
 */
import React, { useEffect, useState } from 'react'
import clsx from 'clsx'
import { useBotStore } from '@/store'
import { fetchSettings, updateSettings, connectIBKR, disconnectIBKR, isBackendAlive } from '@/services/api'
import type { AppSettings } from '@/services/mockBackend'

export default function SettingsPage() {
  const { ibkrConnected, mockMode, setIBKR } = useBotStore()
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [saved, setSaved] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetchSettings().then(setSettings)
  }, [])

  if (!settings) {
    return <div className="text-xs font-mono text-terminal-ghost p-8">Loading settings…</div>
  }

  const patch = async (p: Partial<AppSettings>) => {
    const next = await updateSettings(p)
    setSettings(next)
    setSaved('Saved.')
    setTimeout(() => setSaved(''), 1500)
  }

  const handleConnect = async () => {
    setBusy(true)
    try {
      const r = ibkrConnected ? await disconnectIBKR() : await connectIBKR()
      setIBKR(r.connected)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-6 max-w-3xl">
      {/* ── Header ──────────────────────────────────────────── */}
      <div>
        <h2 className="text-sm font-semibold text-terminal-text">Settings</h2>
        <p className="text-xs text-terminal-dim mt-0.5">
          Configure how the trading dashboard talks to your broker and data providers.
          {mockMode && (
            <span className="ml-2 text-[10px] font-mono text-terminal-amber">
              [MOCK MODE — no backend detected, changes persist locally]
            </span>
          )}
          {!mockMode && isBackendAlive() && (
            <span className="ml-2 text-[10px] font-mono text-terminal-green">[Backend connected]</span>
          )}
        </p>
        {saved && <span className="text-[10px] font-mono text-terminal-green">{saved}</span>}
      </div>

      {/* ── IBKR ──────────────────────────────────────────── */}
      <Card title="Interactive Brokers (IBKR)">
        <p className="text-[11px] font-mono text-terminal-dim mb-3 leading-relaxed">
          To trade live, install IB Gateway or TWS, enable API access (File → Global Configuration → API), and
          point the host/port below at it. Default paper-trading port is <code className="text-terminal-amber">7497</code>;
          live port is <code className="text-terminal-amber">7496</code>.
          The trading bot will reject live orders unless a backend confirms paper mode is OFF.
        </p>

        <div className="grid grid-cols-3 gap-3">
          <Field label="Host">
            <input
              value={settings.ibkr_host}
              onChange={(e) => patch({ ibkr_host: e.target.value })}
              className="input"
            />
          </Field>
          <Field label="Port">
            <input
              type="number"
              value={settings.ibkr_port}
              onChange={(e) => patch({ ibkr_port: Number(e.target.value) })}
              className="input"
            />
          </Field>
          <Field label="Client ID">
            <input
              type="number"
              value={settings.ibkr_client_id}
              onChange={(e) => patch({ ibkr_client_id: Number(e.target.value) })}
              className="input"
            />
          </Field>
        </div>

        <div className="flex items-center gap-3 mt-3">
          <Toggle
            label="Paper trading account"
            value={settings.is_paper}
            onChange={(v) => patch({ is_paper: v })}
          />
          <button
            onClick={handleConnect}
            disabled={busy}
            className={clsx(
              'ml-auto text-xs font-mono px-3 py-1.5 rounded border transition-colors',
              ibkrConnected
                ? 'border-terminal-green/40 text-terminal-green bg-terminal-green/5'
                : 'border-terminal-blue/40 text-terminal-blue bg-terminal-blue/5 hover:bg-terminal-blue/10',
              busy && 'opacity-50',
            )}
          >
            {ibkrConnected ? '✓ Connected (click to disconnect)' : 'Connect to IBKR'}
          </button>
        </div>

        {!isBackendAlive() && (
          <p className="text-[10px] font-mono text-terminal-amber mt-2">
            ⚠ No Python backend detected. IBKR connections require the FastAPI server.
            In mock mode the "Connect" button only flips the UI state.
          </p>
        )}
      </Card>

      {/* ── Data provider ─────────────────────────────────── */}
      <Card title="Market Data">
        <Radio
          name="provider"
          value={settings.data_provider}
          onChange={(v) => patch({ data_provider: v as AppSettings['data_provider'] })}
          options={[
            { value: 'mock',   label: 'Mock (synthetic prices)', desc: 'Browser-only — no network calls. Best for UI testing.' },
            { value: 'yahoo',  label: 'Yahoo Finance',           desc: 'Free real OHLCV bars via the backend proxy.' },
            { value: 'ibkr',   label: 'IBKR',                    desc: 'Real-time bars from your IBKR session.' },
          ]}
        />
      </Card>

      {/* ── Chart engine ──────────────────────────────────── */}
      <Card title="Chart Engine">
        <Radio
          name="chart"
          value={settings.chart_engine}
          onChange={(v) => patch({ chart_engine: v as AppSettings['chart_engine'] })}
          options={[
            { value: 'lightweight', label: 'TradingView Lightweight Charts (offline)', desc: 'Built-in. Works without internet. Limited drawing tools.' },
            { value: 'tradingview', label: 'TradingView Embedded Widget',              desc: 'Full TradingView features. Requires tradingview.com to be reachable.' },
          ]}
        />
        {settings.chart_engine === 'tradingview' && (
          <div className="mt-3">
            <Toggle
              label="Use TradingView dark theme"
              value={settings.tradingview_theme === 'dark'}
              onChange={(v) => patch({ tradingview_theme: v ? 'dark' : 'light' })}
            />
          </div>
        )}
      </Card>

      {/* ── Bot timing ──────────────────────────────────── */}
      <Card title="Bot Engine">
        <Field label="Tick interval (seconds)">
          <input
            type="number"
            min={5}
            max={3600}
            value={settings.bot_interval_seconds}
            onChange={(e) => patch({ bot_interval_seconds: Number(e.target.value) })}
            className="input w-32"
          />
        </Field>
        <p className="text-[10px] font-mono text-terminal-dim mt-2">
          How often the rules engine re-evaluates all enabled rules. Lower = more responsive but more API load.
        </p>
      </Card>

      {/* ── Screener / Alerts ─────────────────────────────── */}
      <Card title="Screener &amp; Alerts">
        <Toggle
          label="Enable 24/7 stock screener"
          value={settings.enable_screener}
          onChange={(v) => patch({ enable_screener: v })}
        />
        <div className="mt-2">
          <Field label="Default universe">
            <select
              value={settings.screener_universe}
              onChange={(e) => patch({ screener_universe: e.target.value as AppSettings['screener_universe'] })}
              className="select"
            >
              <option value="sp500">S&amp;P 500</option>
              <option value="nasdaq100">NASDAQ 100</option>
              <option value="russell1k">Russell 1000</option>
              <option value="custom">Custom watchlist</option>
            </select>
          </Field>
        </div>
        <div className="mt-3">
          <Toggle
            label="Enable price alerts"
            value={settings.enable_alerts}
            onChange={(v) => patch({ enable_alerts: v })}
          />
        </div>
      </Card>

      {/* ── Safety / data reset ──────────────────────────── */}
      <Card title="Data &amp; Safety">
        <p className="text-[11px] font-mono text-terminal-dim mb-2 leading-relaxed">
          All rules, alerts, simulated positions, and trade history are stored in your browser's
          localStorage under the key <code className="text-terminal-amber">tradebot.mockBackend.v1</code>.
          Clearing this will reset everything.
        </p>
        <button
          onClick={() => {
            if (window.confirm('Clear ALL local state? This deletes rules, alerts, simulated positions, and trade history.')) {
              try { localStorage.removeItem('tradebot.mockBackend.v1') } catch { /* */ }
              location.reload()
            }
          }}
          className="text-xs font-mono px-3 py-1.5 rounded border border-terminal-red/40 text-terminal-red hover:bg-terminal-red/10 transition-colors"
        >
          Reset local data
        </button>
      </Card>

      <style>{`
        .input { background:#0a1525; border:1px solid #1c2e4a; border-radius:4px; padding:6px 8px;
                 font-family:"JetBrains Mono",monospace; font-size:12px; color:#dce8f5; outline:none; width:100%; }
        .input:focus { border-color:#4f91ff; }
        .select { background:#0a1525; border:1px solid #1c2e4a; border-radius:4px; padding:6px 8px;
                  font-family:"JetBrains Mono",monospace; font-size:12px; color:#dce8f5; outline:none; }
        .select:focus { border-color:#4f91ff; }
      `}</style>
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function Card({ title, children }: { title: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="bg-terminal-surface border border-terminal-border rounded-lg p-4">
      <h3 className="text-[10px] font-mono text-terminal-ghost uppercase tracking-widest mb-3">{title}</h3>
      {children}
    </section>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-mono text-terminal-ghost uppercase tracking-wider">{label}</span>
      {children}
    </label>
  )
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="inline-flex items-center gap-2 cursor-pointer select-none">
      <button
        onClick={() => onChange(!value)}
        type="button"
        className={clsx(
          'relative w-9 h-5 rounded-full border transition-all',
          value ? 'bg-terminal-green border-terminal-green' : 'bg-terminal-muted border-terminal-border',
        )}
        aria-pressed={value}
      >
        <span className={clsx(
          'absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-transform',
          value ? 'translate-x-4' : 'translate-x-0.5',
        )} />
      </button>
      <span className="text-xs font-mono text-terminal-dim">{label}</span>
    </label>
  )
}

function Radio({
  name, value, onChange, options,
}: {
  name: string
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string; desc?: string }[]
}) {
  return (
    <div className="flex flex-col gap-2">
      {options.map((opt) => (
        <label key={opt.value} className={clsx(
          'flex items-start gap-2 px-3 py-2 rounded border cursor-pointer transition-colors',
          value === opt.value
            ? 'border-terminal-blue/40 bg-terminal-blue/5'
            : 'border-terminal-border hover:border-terminal-muted',
        )}>
          <input
            type="radio"
            name={name}
            value={opt.value}
            checked={value === opt.value}
            onChange={() => onChange(opt.value)}
            className="mt-0.5 accent-terminal-blue"
          />
          <div>
            <div className="text-xs font-mono text-terminal-text">{opt.label}</div>
            {opt.desc && <div className="text-[10px] font-mono text-terminal-ghost mt-0.5">{opt.desc}</div>}
          </div>
        </label>
      ))}
    </div>
  )
}
